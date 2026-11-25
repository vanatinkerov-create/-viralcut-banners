"""Запись прямого эфира и нарезка моментов по ходу стрима (P0/P1)."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from . import analyze, db, pipeline, transcribe, utils
from .config import PROJECTS_DIR, ffmpeg
from .errors import Cancelled

# Первое окно короче остальных: первый клип появляется через несколько минут,
# а не после полного пятиминутного окна.
WINDOW_SECONDS = max(60, int(os.environ.get("VIRALCUT_LIVE_WINDOW", "300")))
FIRST_WINDOW_SECONDS = max(30, int(os.environ.get("VIRALCUT_LIVE_FIRST_WINDOW", "120")))
MIN_TAIL_SECONDS = 8.0
POLL_SECONDS = 1.5
OFFLINE_RETRY_SECONDS = max(5.0, float(os.environ.get("VIRALCUT_LIVE_OFFLINE_RETRY", "30")))
MAX_BACKOFF_SECONDS = 30.0
SEGMENT_WORKERS = max(1, min(8, int(os.environ.get("VIRALCUT_LIVE_DOWNLOAD_WORKERS", "4"))))
SEGMENT_BATCH = 12
AD_MARKERS = re.compile(
    r"twitch-stitched-ad|SCTE35|CUE-OUT|CUE-IN|X-TV-TWITCH-AD|stitched-ad|EXT-X-CUE-OUT",
    re.I,
)
AD_DURATION = re.compile(r"DURATION=([\d.]+)")
AD_ID = re.compile(r'ID="([^"]+)"')
OFFLINE_HINTS = re.compile(
    r"not currently live|channel is offline|is offline|no live stream|live event has ended|"
    r"failed to download m3u8 information.*404|404.*m3u8",
    re.I,
)


class StreamOffline(RuntimeError):
    """Канал не в эфире: это не сетевая ошибка, ждать начала нужно отдельно."""


def _offline_reason(message: str) -> str:
    text = message.lower()
    if "not currently live" in text or "offline" in text:
        return "канал не в эфире"
    if "404" in text:
        return "поток не найден — эфир не идёт или уже закончился"
    return "эфир недоступен"


def _workdir(pid: str) -> Path:
    path = PROJECTS_DIR / pid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hls_url(url: str, browser: Optional[str] = None) -> str:
    """Достаёт m3u8 живого потока через yt-dlp (без скачивания)."""
    import yt_dlp

    opts: Dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True,
                            "noplaylist": True, "socket_timeout": 15, "retries": 1,
                            "extractor_retries": 1, "cachedir": False}
    from . import downloader

    opts.update(downloader._cookie_opts(browser))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if OFFLINE_HINTS.search(message):
            raise StreamOffline(_offline_reason(message)) from exc
        raise
    if info and "entries" in info:
        entries = [e for e in info["entries"] if e]
        info = entries[0] if entries else info
    if info and info.get("is_live") is False:
        raise StreamOffline("канал не в эфире")
    candidates = [f for f in (info or {}).get("formats", [])
                  if "m3u8" in (f.get("protocol") or "") and f.get("url")
                  and (f.get("vcodec") or "none") != "none"]
    if not candidates:
        raise RuntimeError("Не удалось получить живой поток (m3u8) по этой ссылке.")
    return max(candidates, key=lambda f: f.get("height") or 0)["url"]


def _http_get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ViralCut/1.1",
                                               "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch(url: str, timeout: float = 15.0) -> str:
    return _http_get(url, timeout=timeout).decode("utf-8", "replace")


def _master_variant(text: str, base: str) -> Optional[Dict[str, Any]]:
    """Выбирает лучший вариант из master-плейлиста (если пришёл master)."""
    best: Optional[Dict[str, Any]] = None
    pending: Optional[Dict[str, Any]] = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = line[len("#EXT-X-STREAM-INF:"):]
            res = re.search(r"RESOLUTION=(\d+)x(\d+)", attrs)
            bw = re.search(r"BANDWIDTH=(\d+)", attrs)
            pending = {"height": int(res.group(2)) if res else 0,
                       "bandwidth": int(bw.group(1)) if bw else 0}
        elif line and not line.startswith("#") and pending is not None:
            pending["url"] = urljoin(base, line)
            if best is None or (pending["height"], pending["bandwidth"]) > (
                    best["height"], best["bandwidth"]):
                best = pending
            pending = None
    return best


def _media_playlist(url: str) -> Tuple[str, str]:
    """Загружает плейлист; если это master — переходит к лучшему варианту."""
    text = _fetch(url)
    if "#EXT-X-STREAM-INF" in text:
        variant = _master_variant(text, url)
        if variant:
            url = variant["url"]
            text = _fetch(url)
    return text, url


def _parse_playlist(text: str, base: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    duration: Optional[float] = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            m = re.search(r"([\d.]+)", line)
            duration = float(m.group(1)) if m else 4.0
        elif line and not line.startswith("#"):
            segments.append({"uri": urljoin(base, line), "duration": duration or 4.0,
                             "duration_set": duration})
            duration = None
    return segments


def _media_sequence(text: str) -> Optional[int]:
    match = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    return int(match.group(1)) if match else None


def _duration_from_times(times_path: Path) -> float:
    total = 0.0
    try:
        with open(times_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                    total = max(total, float(item["start"]) + float(item["dur"]))
                except Exception:
                    continue
    except OSError:
        return 0.0
    return total


class Recorder:
    def __init__(self, pid: str, url: str, stop: threading.Event, browser: Optional[str] = None):
        self.pid = pid
        self.url = url
        self.stop = stop
        self.browser = browser
        self.workdir = _workdir(pid)
        self.snapshots = self.workdir / "playlists.jsonl"
        self.segments_dir = self.workdir / "segments"
        self.segments_dir.mkdir(exist_ok=True)
        self.times_path = self.segments_dir / "times.jsonl"
        self.sequence: Optional[int] = None
        self.duration = 0.0
        self.bytes = 0
        self.init_url: Optional[str] = None
        self.ad_intervals: List[Dict[str, Any]] = []
        self.attempts = 0
        self.status = "connecting"
        self.live_error: Optional[str] = None
        self.polls = 0

    def _ensure_init(self, text: str, base: str) -> None:
        match = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
        if not match:
            return
        url = urljoin(base, match.group(1))
        if url == self.init_url:
            return
        (self.segments_dir / "init.mp4").write_bytes(_http_get(url, timeout=30))
        self.init_url = url

    def _save_state(self, stage: Optional[str] = None) -> None:
        meta: Dict[str, Any] = {
            "live": True,
            "live_duration": round(self.duration, 1),
            "live_bytes": int(self.bytes),
            "live_segments": self.sequence,
            "live_status": self.status,
            "live_attempts": self.attempts,
        }
        if self.live_error:
            meta["live_error"] = self.live_error
            db.merge_meta(self.pid, meta)
        else:
            meta["live_error"] = None
            db.merge_meta(self.pid, meta, drop=("live_error",))
        db.update_project(self.pid, progress=min(99.0, max(0.0, self.duration)))
        if stage:
            db.set_recording_stage(self.pid, stage)

    def _log_ad_markers(self, text: str, sequence: int) -> None:
        changed = False
        for line in text.splitlines():
            if not AD_MARKERS.search(line):
                continue
            duration = 30.0
            match = AD_DURATION.search(line)
            if match:
                duration = float(match.group(1))
            start = round(self.duration, 1)
            end = round(self.duration + duration, 1)
            ad_id = AD_ID.search(line)
            ad_key = ad_id.group(1) if ad_id else f"{start}-{end}"
            if any(a.get("key") == ad_key for a in self.ad_intervals):
                continue
            item = {"sequence": sequence, "time": start,
                    "start": start, "end": end, "key": ad_key,
                    "type": "twitch-stitched-ad" if "stitched-ad" in line.lower() else "ad",
                    "text": line[:2000]}
            self.ad_intervals.append(item)
            changed = True
            with open(self.snapshots, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        if changed:
            db.merge_meta(self.pid, {"ad_intervals": self.ad_intervals[-200:]})

    def _download_pending(self, pending: List[Tuple[int, Dict[str, Any]]]) -> List[Tuple[int, Dict[str, Any], bytes]]:
        """Скачивает пачку сегментов параллельно, порядок сохраняется."""
        workers = max(1, min(SEGMENT_WORKERS, len(pending)))

        def fetch(item: Tuple[int, Dict[str, Any]]) -> bytes:
            return _http_get(item[1]["uri"], timeout=25)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="live-seg") as pool:
            blobs = list(pool.map(fetch, pending))
        return [(seq, seg, blob) for (seq, seg), blob in zip(pending, blobs)]

    def _reconnect(self, playlist_url: Optional[str], exc: Exception) -> Optional[str]:
        self.attempts += 1
        self.status = "reconnecting"
        self.live_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        if self.attempts >= 3:
            playlist_url = None  # m3u8 мог протухнуть — перезапросим
        delay = min(MAX_BACKOFF_SECONDS, 2.0 ** min(self.attempts, 5))
        print(f"[live] reconnect ({self.attempts}): {self.live_error}",
              file=sys.stderr, flush=True)
        self._save_state(f"Нет связи · переподключаюсь (попытка {self.attempts})")
        self.stop.wait(delay)
        return playlist_url

    def run(self) -> None:
        playlist_url: Optional[str] = None
        while not self.stop.is_set():
            self.polls += 1
            if self.polls % 10 == 0 and db.get_project(self.pid) is None:
                # проект удалили из интерфейса — запись больше не нужна
                self.stop.set()
                break
            try:
                if playlist_url is None:
                    self.status = "connecting"
                    self.live_error = None
                    self._save_state("Подключаюсь к эфиру…")
                    playlist_url = _hls_url(self.url, self.browser)
                text, playlist_url = _media_playlist(playlist_url)
                self._ensure_init(text, playlist_url)
                media_seq = _media_sequence(text)
                if self.sequence is None:
                    self.sequence = media_seq if media_seq is not None else 0
                self._log_ad_markers(text, self.sequence)
                segments = _parse_playlist(text, playlist_url)
                if segments:
                    # связь есть и плейлист разобран — сразу показываем «Запись»,
                    # не дожидаясь, пока скачается стартовый набор сегментов
                    self.status = "live"
                    self.live_error = None
                    if self.duration <= 0:
                        print(f"[live] playlist ok: {len(segments)} сегментов, "
                              f"sequence={self.sequence}", file=sys.stderr, flush=True)
                    self._save_state(f"Запись · {int(self.duration // 60)} мин")
                base = media_seq if media_seq is not None else self.sequence
                pending: List[Tuple[int, Dict[str, Any]]] = []
                for pos, seg in enumerate(segments):
                    if seg["duration_set"] is None:
                        continue
                    seq = base + pos
                    if seq < self.sequence:
                        continue
                    if (self.segments_dir / f"{seq:08d}.ts").exists():
                        self.sequence = max(self.sequence, seq + 1)
                        continue
                    pending.append((seq, seg))
                known = 0
                if pending:
                    # ограничиваем пачку, чтобы после долгого обрыва не держать
                    # в памяти сотни мегабайт
                    for seq, seg, data in self._download_pending(pending[:SEGMENT_BATCH]):
                        target = self.segments_dir / f"{seq:08d}.ts"
                        target.write_bytes(data)
                        with open(self.times_path, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"seq": seq, "start": round(self.duration, 3),
                                                 "dur": round(seg["duration"], 3)}) + "\n")
                        self.bytes += len(data)
                        self.duration += seg["duration"]
                        self.sequence = max(self.sequence, seq + 1)
                        known += 1
                if known:
                    self.attempts = 0
                    self.status = "live"
                    self.live_error = None
                if "#EXT-X-ENDLIST" in text and known == 0:
                    self.stop.set()
                self._save_state(f"Запись · {int(self.duration // 60)} мин")
                self.stop.wait(POLL_SECONDS)
            except StreamOffline as exc:
                self.attempts += 1
                self.status = "offline"
                self.live_error = str(exc)[:200]
                playlist_url = None
                self._save_state(f"Эфир не идёт · жду начала (проверка {self.attempts})")
                self.stop.wait(OFFLINE_RETRY_SECONDS)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 410):
                    # подписанный плейлист/CDN больше не отдаёт поток — эфир кончился
                    self.attempts += 1
                    self.status = "offline"
                    self.live_error = f"поток недоступен (HTTP {exc.code}) — эфир закончился"
                    playlist_url = None
                    self._save_state(f"Эфир не идёт · жду начала (проверка {self.attempts})")
                    self.stop.wait(OFFLINE_RETRY_SECONDS)
                else:
                    if exc.code == 403:
                        playlist_url = None  # токен доступа истёк — нужен новый m3u8
                    playlist_url = self._reconnect(playlist_url, exc)
            except Exception as exc:  # noqa: BLE001
                playlist_url = self._reconnect(playlist_url, exc)


def _seg_spans(workdir: Path, start: float, end: float) -> Tuple[List[Path], float]:
    """Список сегментов записи, покрывающих [start, end], и время начала первого."""
    segments_dir = workdir / "segments"
    times_path = segments_dir / "times.jsonl"
    entries = []
    if times_path.exists():
        for line in times_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                entries.append((int(item["seq"]), float(item["start"]), float(item["dur"])))
            except Exception:
                continue
    entries.sort(key=lambda e: e[0])
    picked = [e for e in entries if e[1] < end and e[1] + e[2] > start]
    if not picked:
        return [], 0.0
    offset = picked[0][1]
    paths = [segments_dir / f"{seq:08d}.ts" for seq, _, _ in picked
             if (segments_dir / f"{seq:08d}.ts").exists()]
    return paths, offset


def _times_map(workdir: Path) -> Dict[int, float]:
    """seq -> длительность сегмента из times.jsonl."""
    out: Dict[int, float] = {}
    path = workdir / "segments" / "times.jsonl"
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                    out[int(item["seq"])] = float(item["dur"])
                except Exception:
                    continue
    except OSError:
        pass
    return out


def _local_playlist(workdir: Path, paths: List[Path], name: str) -> Path:
    """Локальный HLS-плейлист для выбранных сегментов.

    Раньше сегменты склеивались протоколом concat: (байт в байт). На fMP4-потоках
    Twitch (есть init.mp4) в записи встречается скачок таймкодов, и ffmpeg с -t
    обрывал окно на этом скачке (120 с превращались в 30 с). HLS-демуксер читает
    каждый сегмент отдельно и сам выравнивает таймлайн, поэтому окна и клипы
    получаются нужной длины и без сдвига субтитров.
    """
    seg_dir = workdir / "segments"
    durations = _times_map(workdir)
    target = max([durations.get(int(p.stem), 2.0) for p in paths] + [2.0])
    lines = ["#EXTM3U", "#EXT-X-VERSION:7",
             f"#EXT-X-TARGETDURATION:{int(target) + 1}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    if (seg_dir / "init.mp4").exists():
        lines.append('#EXT-X-MAP:URI="init.mp4"')
    for path in paths:
        lines.append(f"#EXTINF:{durations.get(int(path.stem), 2.0):.3f},")
        lines.append(path.name)
    lines.append("#EXT-X-ENDLIST")
    playlist = seg_dir / f"{name}.m3u8"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return playlist


def _ad_overlap(start: float, end: float, intervals: List[Dict[str, Any]]) -> float:
    merged: List[tuple] = []
    for item in intervals:
        if "end" not in item:
            continue  # устаревшие метки без длительности пропускаем
        a = float(item.get("start") or item.get("time") or 0)
        b = float(item.get("end") or a + 30)
        if b <= a:
            continue
        merged.append((a, b))
    merged.sort()
    union: List[tuple] = []
    for a, b in merged:
        if union and a <= union[-1][1] + 0.5:
            union[-1] = (union[-1][0], max(union[-1][1], b))
        else:
            union.append((a, b))
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in union)


def _settings(opts: Dict[str, Any]) -> Dict[str, Any]:
    return {
        # в эфире по умолчанию показываем весь кадр (рабочее поле), а не кроп
        # по лицу: иначе клип превращается в крупный план стримера
        "aspect": opts.get("aspect", "9:16"),
        "mode": opts.get("mode", "blur") if opts.get("mode") != "crop" else "crop",
        "subtitles": opts.get("subtitles", True), "accent": opts.get("accent", "yellow"),
        "uppercase": opts.get("uppercase", True), "show_hook": opts.get("show_hook", True),
        "focus_x": 0.5, "sub_size": int(opts.get("sub_size", 118)),
        "sub_font": opts.get("sub_font", "montserrat"),
        "sub_position": opts.get("sub_position", "bottom"),
        "sub_offset": int(opts.get("sub_offset", 0)),
        "sub_speed": opts.get("sub_speed", "calm"),
    }


def _window_label(start: float, end: float) -> str:
    def fmt(value: float) -> str:
        minutes, seconds = divmod(int(round(value)), 60)
        return f"{minutes}:{seconds:02d}"

    return f"{fmt(start)}–{fmt(end)}"


def _live_duration(pid: str) -> float:
    meta = (db.get_project(pid) or {}).get("meta") or {}
    value = float(meta.get("live_duration") or 0)
    if value > 0:
        return value
    return _duration_from_times(_workdir(pid) / "segments" / "times.jsonl")


def process_windows(pid: str, opts: Dict[str, Any], stop: threading.Event) -> None:
    """Пока идёт запись — каждое окно распознаётся и из него режутся моменты."""
    workdir = _workdir(pid)
    language: Optional[str] = None
    processed = 0
    failures = 0
    try:
        while True:
            duration = _live_duration(pid)
            project = db.get_project(pid)
            if project is None:
                return
            meta = project.get("meta") or {}
            last_window = float(meta.get("live_processed") or 0)
            window = FIRST_WINDOW_SECONDS if last_window <= 0 else WINDOW_SECONDS
            remaining = duration - last_window

            if stop.is_set():
                # кнопка «Стоп» или конец эфира: дожимаем остаток, но не ждём
                # следующего полного окна
                if remaining < MIN_TAIL_SECONDS:
                    break
                win_end = min(duration, last_window + window)
            else:
                if remaining < window + 3:
                    time.sleep(2)
                    continue
                win_end = min(duration, last_window + window)

            win_start = last_window
            label = _window_label(win_start, win_end)
            ads = meta.get("ad_intervals") or []
            win_len = max(1.0, win_end - win_start)
            if _ad_overlap(win_start, win_end, ads) >= 0.8 * win_len:
                db.update_project(pid, stage=f"Окно {label} — почти целиком реклама · пропускаю")
                db.merge_meta(pid, {"live_processed": win_end})
                time.sleep(1)
                continue

            audio = workdir / f"win_{int(win_start)}.wav"
            try:
                paths, offset = _seg_spans(workdir, win_start, win_end)
                if not paths:
                    raise RuntimeError("сегменты записи ещё не готовы")
                print(f"[live] extracting {win_start:.0f}-{win_end:.0f}", file=sys.stderr, flush=True)
                db.update_project(pid, stage=f"Распознавание речи · окно {label}")
                db.merge_meta(pid, {"live_window": {"start": round(win_start, 1),
                                                    "end": round(win_end, 1)}})
                seek = max(0.0, win_start - offset)
                playlist = _local_playlist(workdir, paths, f"win_{int(win_start)}")
                cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(playlist)]
                if seek > 0.01:
                    # -ss после -i: точное позиционирование по таймлайну плейлиста
                    cmd += ["-ss", f"{seek:.3f}"]
                cmd += ["-t", str(win_end - win_start),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)]
                try:
                    result = utils.run(cmd, timeout=240)
                finally:
                    playlist.unlink(missing_ok=True)
                if result.returncode or not audio.exists() or audio.stat().st_size < 1024:
                    raise RuntimeError(f"ffmpeg не извлёк звук (код {result.returncode})")
                transcript = transcribe.transcribe(audio, workdir, language=language)
                language = transcript.get("language") or language
                print(f"[live] window {win_start:.0f}-{win_end:.0f} "
                      f"segments={len(transcript['segments'])}", file=sys.stderr)
                db.update_project(pid, stage=f"Поиск моментов · окно {label}")
                found = analyze.find_segments(
                    transcript["segments"], audio, win_end - win_start,
                    count=int(opts.get("live_count", 3) or 3),
                    min_dur=max(8.0, float(opts.get("min_dur", 15))),
                    max_dur=min(90.0, float(opts.get("max_dur", 60))),
                    target_dur=float(opts.get("target_dur", 30)),
                )
                print(f"[live] found={len(found)}", file=sys.stderr)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                failures += 1
                short = f"{type(exc).__name__}: {str(exc)[:120]}"
                print(f"[live] window {label} failed ({failures}): {short}", file=sys.stderr,
                      flush=True)
                traceback.print_exc()
                if failures < 3:
                    db.update_project(pid, stage=f"Повтор окна {label} · {short}", error=short)
                    time.sleep(min(10, 3 * failures))
                    continue
                # окно не даётся — не застреваем на нём навсегда, идём дальше
                db.merge_meta(pid, {"live_processed": win_end,
                                    "live_failed": int(meta.get("live_failed") or 0) + 1})
                db.update_project(pid, stage=f"Окно {label} пропущено · {short}", error=short)
                failures = 0
                time.sleep(1)
                continue
            failures = 0
            audio.unlink(missing_ok=True)

            settings = _settings(opts)
            ready_count = int(meta.get("live_clips") or 0)
            for i, cand in enumerate(found):
                _check_live_cancel(pid, stop)
                clip_start = win_start + cand["start"]
                clip_end = win_start + cand["end"]
                if _ad_overlap(clip_start, clip_end, ads) > 5.0:
                    print(f"[live] skip clip on ad {clip_start:.0f}-{clip_end:.0f}",
                          file=sys.stderr, flush=True)
                    continue
                db.update_project(pid, stage=f"Рендер клипа {i + 1}/{len(found)} · окно {label}")
                # слова из распознавания окна идут от его начала — храним время эфира,
                # чтобы клип можно было перерендерить и после окончания записи
                clip_words = [{**w, "start": round(float(w["start"]) + win_start, 3),
                               "end": round(float(w["end"]) + win_start, 3)}
                              for w in cand["words"]]
                clip = db.add_clip(pid, processed + i, clip_start, clip_end, cand["score"],
                                   cand["title"], cand["hook"], cand["caption"], clip_words,
                                   settings, status="rendering")
                try:
                    _render_live_clip(workdir, clip, win_start, settings, opts)
                    db.update_clip(clip["id"], status="ready", error=None)
                    ready_count += 1
                except Cancelled:
                    raise
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    db.update_clip(clip["id"], status="error", error=str(exc)[:400])
            processed += len(found)
            db.merge_meta(pid, {"live_processed": win_end, "live_clips": ready_count})
            db.update_project(pid, stage=f"Запись · клипов: {ready_count}", error=None)
            meta = (db.get_project(pid) or {}).get("meta", {})
    except Cancelled:
        db.update_project(pid, status="cancelled", stage="Отменено")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        db.update_project(pid, stage=f"Ошибка конвейера: {str(exc)[:160]}",
                          error=str(exc)[:400])


def _check_live_cancel(pid: str, stop: threading.Event) -> None:
    # явная отмена проекта; конец записи (ENDLIST/кнопка «Стоп») — не отмена,
    # оставшиеся окна должны обработаться
    if (db.get_project(pid) or {}).get("status") == "cancelled":
        raise Cancelled()


def _absolute_words(words: List[Dict[str, Any]], clip_start: float) -> List[Dict[str, Any]]:
    """Слова старых live-клипов хранились от начала окна — приводим к времени эфира."""
    if not words:
        return words
    first = min(float(w["start"]) for w in words)
    if first >= clip_start - 2:
        return words  # уже абсолютные (новые клипы)
    shift = clip_start - first
    return [{**w, "start": round(float(w["start"]) + shift, 3),
             "end": round(float(w["end"]) + shift, 3)} for w in words]


def _render_live_clip(workdir: Path, clip: Dict[str, Any], win_start: float,
                      settings: Dict[str, Any],
                      opts: Optional[Dict[str, Any]] = None) -> None:
    start = float(clip["start"]) - win_start
    end = float(clip["end"]) - win_start
    src = workdir / f"src_{clip['id']}.mp4"
    paths, offset = _seg_spans(workdir, float(clip["start"]) - 1, float(clip["end"]) + 1)
    if not paths:
        raise RuntimeError("Нет сегментов записи для фрагмента.")
    seek = max(0.0, float(clip["start"]) - offset)
    playlist = _local_playlist(workdir, paths, f"clip_{clip['id']}")
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(playlist)]
    if seek > 0.01:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-t", str(max(1.0, end - start)),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(src)]
    try:
        proc = utils.run(cmd, timeout=300)
    finally:
        playlist.unlink(missing_ok=True)
    if proc.returncode or not src.exists():
        raise RuntimeError("Не удалось вырезать фрагмент записи.")
    words = [{**w, "start": float(w["start"]) - win_start - start,
              "end": float(w["end"]) - win_start - start} for w in clip["words"]]
    virtual = {**clip, "start": 0.0, "end": end - start, "words": words}
    try:
        pipeline._render_one(src, workdir, virtual, settings, None, opts or {})
    finally:
        src.unlink(missing_ok=True)


_STOP_EVENTS: Dict[str, threading.Event] = {}


def stop_stream(pid: str) -> None:
    event = _STOP_EVENTS.get(pid)
    if event is None:
        event = _STOP_EVENTS.setdefault(pid, threading.Event())
    event.set()


def run_stream(pid: str, options: Dict[str, Any]) -> None:
    stop = _STOP_EVENTS.setdefault(pid, threading.Event())
    recorder = Recorder(pid, db.get_project(pid)["url"], stop, options.get("cookies_browser") or None)
    record_thread = threading.Thread(target=recorder.run, name=f"live-rec-{pid}", daemon=True)
    process_thread = threading.Thread(target=process_windows, args=(pid, options, stop),
                                      name=f"live-proc-{pid}", daemon=True)
    record_thread.start()
    process_thread.start()
    interrupted = False
    while not stop.is_set() and record_thread.is_alive():
        if not process_thread.is_alive():
            # без нарезчика запись бессмысленна — не пишем диск впустую
            interrupted = True
            break
        time.sleep(1)
    stop.set()
    record_thread.join(timeout=5)
    # дожидаемся, пока последние окна дойдут до готовых клипов
    process_thread.join()
    project = db.get_project(pid) or {}
    meta = project.get("meta") or {}
    if interrupted:
        db.update_project(pid, status="error",
                          stage="Нарезка остановлена · запись прервана",
                          error=project.get("error") or "Обработка клипов остановилась.")
    elif project.get("status") == "cancelled":
        db.update_project(pid, stage="Отменено")
    elif meta.get("live_status") == "offline" and not int(meta.get("live_clips") or 0):
        db.update_project(pid, status="done", stage="Запись остановлена · эфира не было")
    else:
        db.update_project(pid, status="done",
                          stage=f"Запись завершена · клипов: {meta.get('live_clips', 0)}")
    _STOP_EVENTS.pop(pid, None)
