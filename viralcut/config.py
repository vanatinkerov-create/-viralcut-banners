"""Конфигурация и общие пути приложения."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

DATA_DIR = Path(os.environ.get("VIRALCUT_DATA", ROOT / "data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
MODELS_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "viralcut.db"

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Размер модели Whisper: tiny / base / small / medium / large-v3
WHISPER_MODEL = os.environ.get("VIRALCUT_WHISPER", "small")
WHISPER_LOCAL_DIR = os.environ.get("VIRALCUT_WHISPER_DIR", "")
WHISPER_DEVICE = os.environ.get("VIRALCUT_DEVICE", "auto")
WHISPER_COMPUTE = os.environ.get("VIRALCUT_COMPUTE", "int8")

MAX_WORKERS = int(os.environ.get("VIRALCUT_WORKERS", "1"))

_ffmpeg_cache: Optional[str] = None
_ffmpeg_dir_cache: Optional[Path] = None
_ffprobe_cache: Optional[str] = None


def ffmpeg() -> str:
    """Путь к ffmpeg: env override -> imageio-ffmpeg (статическая сборка) -> PATH."""
    global _ffmpeg_cache
    if _ffmpeg_cache:
        return _ffmpeg_cache
    env = os.environ.get("VIRALCUT_FFMPEG")
    if env and Path(env).exists():
        _ffmpeg_cache = env
        return env
    try:
        import imageio_ffmpeg

        _ffmpeg_cache = imageio_ffmpeg.get_ffmpeg_exe()
        return _ffmpeg_cache
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg не найден. Установите ffmpeg или пакет imageio-ffmpeg.")
    _ffmpeg_cache = found
    return found


def ffmpeg_bin_dir() -> Path:
    """Папка, где ffmpeg и ffprobe лежат под своими штатными именами.

    yt-dlp вешает `ffmpeg_location` на папку/файл, но *частичное* скачивание
    (download_ranges) он разрешает только если может запустить команду `ffmpeg`
    из PATH: проверка FFmpegFD.available() не смотрит на ffmpeg_location.
    Поэтому здесь мы создаём рядом с данными канонические имена:
      data/bin/ffmpeg  (на Windows ffmpeg.exe)
      data/bin/ffprobe (если он вообще есть)
    Ссылка проверяется запуском `-version`: битая/чужая ссылка (например, после
    обновления venv или переноса .app) пересоздаётся, а если символические
    ссылки недоступны — копированием бинарника.
    """
    global _ffmpeg_dir_cache
    if _ffmpeg_dir_cache is not None:
        return _ffmpeg_dir_cache
    bin_dir = DATA_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _expose_binary(bin_dir, "ffmpeg", Path(ffmpeg()))
    probe = ffprobe()
    if probe:
        _expose_binary(bin_dir, "ffprobe", Path(probe))
    _ffmpeg_dir_cache = bin_dir
    return bin_dir


def ffprobe() -> Optional[str]:
    """Путь к ffprobe: env override -> рядом с ffmpeg -> PATH. Может отсутствовать."""
    global _ffprobe_cache
    if _ffprobe_cache is not None:
        return _ffprobe_cache or None
    env = os.environ.get("VIRALCUT_FFPROBE")
    if env and Path(env).exists():
        _ffprobe_cache = env
        return env
    try:
        target = Path(ffmpeg())
        sibling = target.with_name(
            target.name.replace("ffmpeg", "ffprobe", 1) if "ffmpeg" in target.name else target.name)
        for candidate in (sibling, target.with_name("ffprobe")):
            if candidate != target and candidate.exists():
                _ffprobe_cache = str(candidate)
                return _ffprobe_cache
    except Exception:  # noqa: BLE001 — нет ffmpeg, значит нет и ffprobe
        pass
    found = shutil.which("ffprobe")
    _ffprobe_cache = found or ""
    return found


def _expose_binary(bin_dir: Path, name: str, target: Path) -> Path:
    """Кладёт бинарник в bin_dir под каноническим именем и проверяет запуск."""
    link = bin_dir / (name + ".exe" if sys.platform.startswith("win") else name)
    if _binary_usable(link, target):
        return link
    try:
        link.unlink(missing_ok=True)
    except OSError:
        pass
    # сначала символическая ссылка (дёшево), потом жёсткая ссылка (Windows без
    # прав на symlink), и только затем копия 80-МБ бинарника
    try:
        link.symlink_to(target)
    except OSError:
        try:
            os.link(target, link)
        except OSError:
            try:
                shutil.copy2(target, link)
            except OSError:
                return link
    try:
        link.chmod(0o755)
    except OSError:
        pass
    return link


def _binary_usable(link: Path, target: Path) -> bool:
    """Ссылка ведёт на нужный бинарник и он реально запускается."""
    if not link.exists():
        return False
    try:
        if link.is_symlink():
            if Path(os.readlink(link)) != target:
                return False
        elif link.resolve() != target.resolve():
            # копия: размер и права должны совпадать с оригиналом
            if link.stat().st_size != target.stat().st_size:
                return False
    except OSError:
        return False
    return _runs(link)


def _runs(path: Path) -> bool:
    """Проверка «бинарник живой»: `-version` без окна консоли на Windows."""
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run([str(path), "-hide_banner", "-version"],
                              capture_output=True, timeout=20, **kwargs)
    except Exception:  # noqa: BLE001 — таймаут/OSError = не работает
        return False
    text = (proc.stdout or b"") + (proc.stderr or b"")
    return proc.returncode == 0 and b"version" in text.lower()


def ensure_path() -> None:
    """Добавляет папку с ffmpeg в PATH — yt-dlp ищет его там."""
    bin_dir = str(ffmpeg_bin_dir())
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
