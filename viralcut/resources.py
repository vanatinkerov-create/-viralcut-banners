"""Ограничители ресурсов: сколько ядер отдаём рендеру и сколько рендеров крутим.

Приложение живёт на ноутбуках, и «занять все 8 ядер на 40 минут» — плохой
сценарий: интерфейс дёргается, вентиляторы воют, батарея садится. Поэтому здесь
в одном месте считаются:
  * число одновременных рендеров (по умолчанию 1–3, в зависимости от ядер);
  * потоки, которые отдаём каждому ffmpeg;
  * приоритет процесса (nice на macOS/Linux, BELOW_NORMAL на Windows).

Всё переопределяется переменными окружения:
`VIRALCUT_RENDER_WORKERS`, `VIRALCUT_FFMPEG_THREADS`, `VIRALCUT_NICE`.
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Dict, List


def cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except Exception:
        return max(1, os.cpu_count() or 4)


def _env_int(name: str, default: int, low: int = 1, high: int = 64) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        return default


def render_workers() -> int:
    """Сколько клипов рендерить параллельно.

    Оставляем ядро-полтора системе и интерфейсу: на 8 ядрах это 2 рендера,
    на 4 — один, на 16 — три (больше не нужно, память и диск кончаются раньше).
    """
    default = max(1, min(3, (cpu_count() - 1) // 3))
    return _env_int("VIRALCUT_RENDER_WORKERS", default, 1, 6)


def ffmpeg_threads() -> int:
    """Потоки одному ffmpeg: его доля от оставшихся ядер."""
    workers = render_workers()
    default = max(1, (cpu_count() - 1) // max(1, workers))
    return _env_int("VIRALCUT_FFMPEG_THREADS", default, 1, 32)


def nice_level() -> int:
    """На сколько «понизить» приоритет дочерних процессов (POSIX)."""
    return _env_int("VIRALCUT_NICE", 10, 0, 19)


def whisper_threads() -> int:
    """Распознавание: половина ядер, чтобы окно оставалось отзывчивым."""
    return _env_int("VIRALCUT_WHISPER_THREADS", max(1, cpu_count() // 2), 1, 32)


def low_priority() -> bool:
    return os.environ.get("VIRALCUT_LOW_PRIORITY", "1").strip().lower() not in (
        "0", "false", "no", "off")


def wrap_nice(cmd: List[str]) -> List[str]:
    """Запускает команду через `nice` (macOS/Linux), не трогая Popen.

    `preexec_fn` в многопоточном процессе небезопасен (fork без exec), а рендер
    идёт из потоков — поэтому приоритет ставим внешней утилитой.
    """
    if sys.platform == "win32" or not low_priority():
        return cmd
    level = nice_level()
    if level <= 0 or shutil.which("nice") is None:
        return cmd
    return ["nice", "-n", str(level)] + cmd


def spawn_kwargs() -> Dict[str, Any]:
    """Флаги запуска дочернего процесса: на Windows без окна консоли.

    Каждый Popen без CREATE_NO_WINDOW открывает мигающее чёрное окно — именно
    это видели на Windows 10, когда виджет каждые 2 секунды опрашивал нагрузку.
    """
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def subprocess_kwargs() -> Dict[str, Any]:
    """Аргументы Popen: без окна на Windows и с пониженным приоритетом."""
    import subprocess as _sp

    flags = 0
    if sys.platform == "win32":
        flags |= getattr(_sp, "CREATE_NO_WINDOW", 0)
        if low_priority():
            flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    return {"creationflags": flags} if flags else {}


def summary() -> Dict[str, Any]:
    return {
        "cpu": cpu_count(),
        "render_workers": render_workers(),
        "ffmpeg_threads": ffmpeg_threads(),
        "whisper_threads": whisper_threads(),
        "nice": nice_level() if low_priority() and sys.platform != "win32" else 0,
    }
