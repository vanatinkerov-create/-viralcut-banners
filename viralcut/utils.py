"""Мелкие утилиты: запуск ffmpeg, зондирование медиа, работа с временем."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import ffmpeg

# На Windows дочерние ffmpeg/yt-dlp не должны открывать чёрные консольные окна.
NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def subprocess_kwargs() -> Dict[str, Any]:
    """Флаги запуска дочерних процессов (без окна на Windows + низкий приоритет)."""
    from . import resources

    kwargs = resources.subprocess_kwargs()
    if NO_WINDOW and "creationflags" not in kwargs:
        kwargs["creationflags"] = NO_WINDOW
    return kwargs


class MediaError(Exception):
    pass


def run(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    from . import resources

    return subprocess.run(resources.wrap_nice(cmd), capture_output=True, text=True,
                          timeout=timeout, errors="replace", **subprocess_kwargs())


_TIME_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _to_seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def probe(path: Path) -> Dict[str, Any]:
    """Достаёт длительность, размеры, fps и наличие звука из вывода ffmpeg."""
    proc = run([ffmpeg(), "-hide_banner", "-i", str(path)])
    text = proc.stderr or ""
    info: Dict[str, Any] = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False}
    m = _TIME_RE.search(text)
    if m:
        info["duration"] = _to_seconds(*m.groups())

    vm = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if vm:
        info["width"], info["height"] = int(vm.group(1)), int(vm.group(2))
    fm = re.search(r"(\d+(?:\.\d+)?)\s+fps", text)
    if fm:
        info["fps"] = float(fm.group(1))
    info["has_audio"] = "Audio:" in text

    # Поворот из метаданных (вертикальные видео с телефона)
    rm = re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", text)
    if rm:
        angle = abs(float(rm.group(1))) % 360
        if 75 <= angle <= 105:
            info["width"], info["height"] = info["height"], info["width"]
            info["rotation"] = 90
    if info["width"] == 0 or info["height"] == 0:
        raise MediaError("Не удалось прочитать видеофайл (повреждён или это не видео).")
    return info


def audio_tracks(path: Path) -> List[Dict[str, Any]]:
    """Звуковые дорожки файла: порядок, язык, каналы — по выводу ffmpeg."""
    try:
        proc = run([ffmpeg(), "-hide_banner", "-i", str(path)], timeout=30)
        text = (proc.stderr or "") + (proc.stdout or "")
    except Exception:  # noqa: BLE001
        return []
    tracks: List[Dict[str, Any]] = []
    order = 0
    for line in text.splitlines():
        m = re.search(r"Stream #0:(\d+)(?:\[[^\]]+\])?(?:\(([a-z]{2,3})\))?: Audio: ([^,]+)", line)
        if not m:
            continue
        title = ""
        mt = re.search(r"\(title: ([^)]+)\)", line)
        if mt:
            title = mt.group(1).strip()
        channels, stereo = 0, 1 if "stereo" in line else 2
        mch = re.search(r"(\d+(?:\.\d+)?) (?:ch|stereo|mono)", line)
        if mch:
            channels = float(mch.group(1))
        tracks.append({"index": order, "stream": int(m.group(1)),
                       "language": (m.group(2) or "").lower(),
                       "title": title, "channels": channels or 2})
        order += 1
    return tracks


def media_duration(path: Path) -> float:
    """Длительность любого медиа, включая аудио без видеодорожки."""
    proc = run([ffmpeg(), "-hide_banner", "-i", str(path)])
    m = _TIME_RE.search(proc.stderr or "")
    return _to_seconds(*m.groups()) if m else 0.0


def extract_audio(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vn",
           "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    proc = run(cmd)
    if proc.returncode != 0 or not dst.exists():
        raise MediaError(f"Не удалось извлечь аудиодорожку: {(proc.stderr or '')[-300:]}")
    return dst


def extract_asr_audio(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    """Аудио для распознавания речи: без гула, шипения и перепадов громкости.

    Распознавание ошибается в первую очередь на «грязном» звуке: низкочастотный
    гул, шипение и тихие реплики. Поэтому перед Whisper прогоняем:
    срез ниже 70 Гц и выше 7.8 кГц (речь живёт между ними), мягкое
    шумоподавление и выравнивание громкости по фразам.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg(), "-y", "-v", "error", "-i", str(src), "-vn",
            "-ac", "1", "-ar", str(sample_rate)]
    filters = "highpass=f=70,lowpass=f=7800,afftdn=nr=10:nf=-40,dynaudnorm=f=200:g=10"
    proc = run(base + ["-af", filters, "-c:a", "pcm_s16le", str(dst)])
    if proc.returncode != 0 or not dst.exists():
        # старые сборки ffmpeg без afftdn/dynaudnorm — хотя бы срезы и моно
        proc = run(base + ["-af", "highpass=f=70,lowpass=f=7800",
                           "-c:a", "pcm_s16le", str(dst)])
    if proc.returncode != 0 or not dst.exists():
        proc = run(base + ["-c:a", "pcm_s16le", str(dst)])
    if proc.returncode != 0 or not dst.exists():
        raise MediaError(f"Не удалось извлечь аудиодорожку: {(proc.stderr or '')[-300:]}")
    return dst


def read_audio_mono(src: Path, sample_rate: int = 16000) -> Any:
    """Возвращает numpy-массив float32 [-1, 1] с аудио."""
    import numpy as np

    cmd = [ffmpeg(), "-v", "error", "-i", str(src), "-vn", "-ac", "1",
           "-ar", str(sample_rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True, **subprocess_kwargs())
    if proc.returncode != 0 or not proc.stdout:
        raise MediaError("Не удалось прочитать аудио.")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def fmt_ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


def ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def parse_vtt(path: Path) -> List[Dict[str, Any]]:
    """Простой парсер WebVTT в сегменты с приблизительными таймингами слов."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n"))
    segs: List[Dict[str, Any]] = []
    ts = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() and not ln.startswith(("WEBVTT", "NOTE", "Kind:", "Language:"))]
        stamp = None
        text_lines = []
        for ln in lines:
            m = ts.search(ln)
            if m:
                g = list(map(int, m.groups()))
                stamp = (g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                         g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000)
            elif stamp:
                text_lines.append(re.sub(r"<[^>]+>", "", ln).strip())
        if not stamp or not text_lines:
            continue
        text = " ".join(text_lines).strip()
        words_raw = text.split()
        if not words_raw:
            continue
        span = max(0.05, stamp[1] - stamp[0])
        step = span / len(words_raw)
        words = [{"word": w, "start": stamp[0] + i * step, "end": stamp[0] + (i + 1) * step}
                 for i, w in enumerate(words_raw)]
        segs.append({"start": stamp[0], "end": stamp[1], "text": text, "words": words})
    # склейка дублей (автосубтитры YouTube дают «плывущие» строки)
    out: List[Dict[str, Any]] = []
    for s in segs:
        if out and s["text"] == out[-1]["text"]:
            out[-1]["end"] = s["end"]
            continue
        out.append(s)
    return out
