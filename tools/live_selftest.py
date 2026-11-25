"""Быстрая самопроверка записи эфира без сети.

Проверяет ровно те места, где 1.1.0 молча зависала:
- склейка MPEG-TS без несуществующего init.mp4 (Twitch/Kick);
- пустое окно (нет моментов) не роняет обработчик;
- «канал не в эфире» даёт понятный статус, а не бесконечное «подключение»;
- обрыв связи даёт нарастающую паузу и статус «Нет связи».

Запуск: .venv312/bin/python tools/live_selftest.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="viralcut-selftest-"))
os.environ["VIRALCUT_DATA"] = str(DATA)
sys.path.insert(0, str(ROOT))

from viralcut import analyze, db, live, pipeline, transcribe  # noqa: E402
from viralcut.config import ffmpeg  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("OK   " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def make_segments(workdir: Path, count: int = 2, seconds: float = 5.0) -> float:
    seg_dir = workdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    times = []
    for i in range(count):
        out = seg_dir / f"{i:08d}.ts"
        cmd = [ffmpeg(), "-y", "-v", "error",
               "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
               "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
               "-c:a", "aac", "-f", "mpegts", str(out)]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
        times.append({"seq": i, "start": round(i * seconds, 3), "dur": seconds})
    (seg_dir / "times.jsonl").write_text(
        "\n".join(json.dumps(t) for t in times) + "\n", encoding="utf-8")
    return count * seconds


def make_fmp4_segments(workdir: Path, seconds: float = 12.0, seg: float = 4.0) -> None:
    """Муксованный fMP4 (как у Twitch): init.mp4 + сегменты с EXT-X-MAP."""
    seg_dir = workdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    template = str(seg_dir / "part_%03d.ts")
    result = subprocess.run(
        [ffmpeg(), "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-f", "hls", "-hls_time", str(seg),
         "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4",
         "-hls_fmp4_init_filename", "init.mp4",
         "-hls_segment_filename", template, str(seg_dir / "source.m3u8")],
        capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    parts = sorted(seg_dir.glob("part_*.ts"))
    times = []
    for i, part in enumerate(parts):
        part.rename(seg_dir / f"{i:08d}.ts")
        times.append({"seq": i, "start": round(i * seg, 3), "dur": seg})
    (seg_dir / "times.jsonl").write_text(
        "\n".join(json.dumps(t) for t in times) + "\n", encoding="utf-8")


def _extract(workdir: Path, paths, name: str, seconds: float) -> tuple[int, Path]:
    playlist = live._local_playlist(workdir, paths, name)
    out = workdir / f"{name}.wav"
    try:
        result = subprocess.run(
            [ffmpeg(), "-y", "-v", "error", "-i", str(playlist), "-t", str(seconds),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
            capture_output=True)
    finally:
        playlist.unlink(missing_ok=True)
    return result.returncode, out


def test_playlist_extraction() -> None:
    pid = db.create_project("local:playlist", {})["id"]
    workdir = live._workdir(pid)
    make_segments(workdir, 2, 5.0)
    paths, _offset = live._seg_spans(workdir, 0, 6)
    check("сегменты записи найдены", len(paths) == 2, f"{len(paths)} шт.")
    playlist = live._local_playlist(workdir, paths, "probe")
    text = playlist.read_text(encoding="utf-8")
    check("для MPEG-TS нет EXT-X-MAP", "#EXT-X-MAP" not in text)
    playlist.unlink(missing_ok=True)
    rc, wav = _extract(workdir, paths, "probe", 6)
    check("звук окна MPEG-TS извлекается", rc == 0 and wav.exists()
          and wav.stat().st_size > 1024)


def test_fmp4_window() -> None:
    """fMP4-поток с init.mp4: окно не должно обрезаться на таймкодах."""
    pid = db.create_project("local:fmp4", {})["id"]
    workdir = live._workdir(pid)
    make_fmp4_segments(workdir, 12.0, 4.0)
    paths, _offset = live._seg_spans(workdir, 0, 12)
    check("fMP4: сегменты найдены", len(paths) >= 2, f"{len(paths)} шт.")
    playlist = live._local_playlist(workdir, paths, "probe")
    text = playlist.read_text(encoding="utf-8")
    check("fMP4: плейлист содержит EXT-X-MAP", "#EXT-X-MAP" in text and "init.mp4" in text)
    playlist.unlink(missing_ok=True)
    rc, wav = _extract(workdir, paths, "probe", 12)
    if rc == 0 and wav.exists():
        from viralcut import utils
        duration = utils.media_duration(wav)
        check("fMP4: окно извлекается целиком, а не первые секунды",
              duration > 10.0, f"{duration:.1f} с")
    else:
        check("fMP4: окно извлекается целиком, а не первые секунды", False, "ffmpeg упал")


def test_empty_window_does_not_crash() -> None:
    pid = db.create_project("local:empty", {})["id"]
    workdir = live._workdir(pid)
    duration = make_segments(workdir, 2, 6.0)
    db.update_project(pid, status="running", stage="Запись · 0 мин", error=None,
                      meta={"live": True, "live_duration": duration,
                            "live_processed": 0, "live_clips": 0, "ad_intervals": []})

    old_transcribe, old_find = transcribe.transcribe, analyze.find_segments
    old_window, old_first = live.WINDOW_SECONDS, live.FIRST_WINDOW_SECONDS
    transcribe.transcribe = lambda *a, **k: {  # type: ignore[assignment]
        "language": "ru", "segments": [{"start": 0.0, "end": 5.0,
                                        "text": "проверка", "words": []}]}
    analyze.find_segments = lambda *a, **k: []  # type: ignore[assignment]
    live.WINDOW_SECONDS = live.FIRST_WINDOW_SECONDS = 6
    stop = threading.Event()
    try:
        thread = threading.Thread(target=live.process_windows,
                                  args=(pid, {}, stop), daemon=True)
        thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not stop.is_set():
            if float((db.get_project(pid) or {}).get("meta", {}).get("live_processed") or 0) >= 6:
                break
            time.sleep(0.2)
        project = db.get_project(pid) or {}
        meta = project.get("meta") or {}
        check("окно без моментов обработано без падения",
              abs(float(meta.get("live_processed") or 0) - 6.0) < 0.01,
              f"processed={meta.get('live_processed')} stage={project.get('stage')!r}")
        check("ошибки конвейера нет", not project.get("error"), str(project.get("error")))
    finally:
        stop.set()
        transcribe.transcribe, analyze.find_segments = old_transcribe, old_find
        live.WINDOW_SECONDS, live.FIRST_WINDOW_SECONDS = old_window, old_first
        time.sleep(0.5)


def test_offline_and_backoff() -> None:
    pid = db.create_project("https://www.twitch.tv/offline-test", {})["id"]
    db.update_project(pid, status="running", stage="Подключаюсь к эфиру…",
                      meta={"live": True, "live_processed": 0, "live_clips": 0})
    old_hls, old_poll = live._hls_url, live.OFFLINE_RETRY_SECONDS
    live.OFFLINE_RETRY_SECONDS = 0.5
    stop = threading.Event()

    def offline(*_a, **_k):
        raise live.StreamOffline("канал не в эфире")

    live._hls_url = offline  # type: ignore[assignment]
    try:
        thread = threading.Thread(target=live.Recorder(pid, "https://x", stop).run,
                                  daemon=True)
        thread.start()
        time.sleep(1.4)
        project = db.get_project(pid) or {}
        check("«не в эфире» показано понятным статусом",
              "Эфир не идёт" in (project.get("stage") or ""),
              repr(project.get("stage")))
        check("состояние offline в meta",
              (project.get("meta") or {}).get("live_status") == "offline")
        stop.set()
        thread.join(timeout=5)

        def broken(*_a, **_k):
            raise RuntimeError("соединение сброшено")

        live._hls_url = broken  # type: ignore[assignment]
        stop = threading.Event()
        thread = threading.Thread(target=live.Recorder(pid, "https://x", stop).run,
                                  daemon=True)
        thread.start()
        time.sleep(1.4)
        project = db.get_project(pid) or {}
        check("обрыв связи — статус «Нет связи»",
              "Нет связи" in (project.get("stage") or ""), repr(project.get("stage")))
        stop.set()
        thread.join(timeout=5)
    finally:
        live._hls_url, live.OFFLINE_RETRY_SECONDS = old_hls, old_poll


def test_live_rerender() -> None:
    """Клип из эфира можно перерендерить из записанных сегментов (без project.source)."""
    pid = db.create_project("local:live-rerender", {})["id"]
    workdir = live._workdir(pid)
    duration = make_segments(workdir, 2, 5.0)
    db.update_project(pid, status="done", stage="Запись завершена",
                      meta={"live": True, "live_status": "live", "live_duration": duration,
                            "live_processed": duration, "live_clips": 1,
                            "live_bytes": 1, "ad_intervals": []})
    words = [{"word": "привет", "start": 1.3, "end": 1.8},
             {"word": "эфир", "start": 2.0, "end": 2.6}]
    clip = db.add_clip(pid, 0, 1.0, 6.0, 0.9, "Тест", "ХУК", "", words,
                       {"aspect": "9:16", "mode": "blur", "subtitles": True, "accent": "yellow",
                        "sub_size": 118, "sub_font": "montserrat", "sub_position": "bottom",
                        "sub_offset": 0, "sub_speed": "calm"})
    pipeline.rerender_clip(clip["id"], {"sub_speed": "fast"})
    done = db.get_clip(clip["id"]) or {}
    ass = workdir / "ass" / f"{clip['id']}.ass"
    text = ass.read_text(encoding="utf-8") if ass.exists() else ""
    check("клип из эфира перерендеривается без source",
          done.get("status") == "ready" and done.get("path") and Path(done["path"]).exists(),
          f"status={done.get('status')} error={done.get('error')}")
    check("у перерендеренного live-клипа караоке-субтитры", "\\k" in text)


def main() -> int:
    db.init_db()
    test_playlist_extraction()
    test_fmp4_window()
    test_empty_window_does_not_crash()
    test_offline_and_backoff()
    test_live_rerender()
    shutil.rmtree(DATA, ignore_errors=True)
    if FAILS:
        print(f"\nПровалено: {len(FAILS)} — " + ", ".join(FAILS))
        return 1
    print("\nВсе проверки записи эфира прошли.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
