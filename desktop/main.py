"""Точка входа настольного ViralCut для macOS и Windows.

Поднимает локальный сервер в фоновом потоке и открывает окно WebView
(WKWebView на macOS, EdgeChromium/WebView2 на Windows).
Данные (проекты, модель, БД) хранятся в папке приложения:
macOS — ~/Library/Application Support/ViralCut, Windows — %APPDATA%\\ViralCut.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import faulthandler
import sys
import tarfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Dict

APP_NAME = "ViralCut"

# при запуске из исходников (python desktop/main.py) пакет viralcut лежит рядом
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    import msvcrt  # type: ignore[import-not-found]
else:
    import fcntl  # type: ignore[import-not-found]


def _windows_downloads() -> Path | None:
    """Системная папка «Загрузки» на Windows (учитывает OneDrive-редирект)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                         (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        path_ptr = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr)) == 0 and path_ptr.value:
            value = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return Path(value)
    except Exception:
        return None
    return None


def _linux_downloads() -> Path | None:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("XDG_DOWNLOAD_DIR"):
                raw = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                if raw:
                    return Path(raw)
    except Exception:
        return None
    return None


def downloads_dir() -> Path:
    """Папка «Загрузки» по правилам ОС, а не догадка про ~/Downloads."""
    found = None
    if IS_WINDOWS:
        found = _windows_downloads()
    elif IS_LINUX:
        found = _linux_downloads()
    folder = found or (Path.home() / "Downloads")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    return folder


class DesktopApi:
    """Нативные действия для окна: сохранение файлов и Finder."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def _downloads(self) -> Path:
        folder = downloads_dir() / "ViralCut"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def _ask_enabled() -> bool:
        return os.environ.get("VIRALCUT_ASK_SAVE", "1") != "0"

    def _ask_target(self, suggested: str):
        """Диалог «Сохранить как»: сразу в системной папке «Загрузки».

        Возвращает Path, None (пользователь закрыл диалог) или "unavailable",
        когда окно ещё не готово/платформа не умеет диалог — тогда сохраняем
        в Загрузки/ViralCut без вопроса.
        """
        try:
            import webview

            windows = getattr(webview, "windows", None) or []
            if not windows:
                return "unavailable"
            chosen = windows[0].create_file_dialog(
                webview.SAVE_DIALOG,
                directory=str(self._downloads()),
                save_filename=suggested,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"save dialog failed: {exc}", flush=True)
            return "unavailable"
        if not chosen:
            return None
        value = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
        return Path(str(value))

    @staticmethod
    def _safe(name: str) -> str:
        import re

        value = re.sub(r"[^\w\- ]+", "", name or "clip", flags=re.UNICODE).strip()
        return (value[:70] or "clip").strip()

    @staticmethod
    def _unique(path: Path) -> Path:
        if not path.exists():
            return path
        for i in range(2, 1000):
            candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
            if not candidate.exists():
                return candidate
        return path

    def _reveal(self, path: Path) -> None:
        try:
            if IS_WINDOWS:
                subprocess.Popen(["explorer", f"/select,{path}"])
            elif IS_MACOS:
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception:
            pass

    def choose_folder(self, current: str = "") -> Dict[str, Any]:
        """Диалог выбора папки: пользователь сам решает, куда сохранять."""
        try:
            import webview

            windows = getattr(webview, "windows", None) or []
            if not windows:
                return {"ok": False, "unavailable": True}
            start = current if current and Path(current).is_dir() else str(Path.home())
            chosen = windows[0].create_file_dialog(webview.FOLDER_DIALOG, directory=start)
        except Exception as exc:  # noqa: BLE001
            print(f"folder dialog failed: {exc}", flush=True)
            return {"ok": False, "unavailable": True}
        if not chosen:
            return {"ok": False, "cancelled": True}
        value = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
        return {"ok": True, "path": str(value)}

    def save_source(self, project_id: str) -> Dict[str, Any]:
        """Сохранить скачанный исходник в выбранную папку."""
        from viralcut import db

        project = db.get_project(project_id)
        if not project:
            return {"ok": False, "error": "Проект не найден"}
        source = project.get("source")
        if not source or not Path(source).exists():
            return {"ok": False, "error": "Исходник ещё не скачан"}
        src = Path(source)
        name = self._safe(project.get("title") or src.stem) + src.suffix
        target = self._ask_target(name)
        if target is None:
            return {"ok": False, "cancelled": True}
        if target == "unavailable":
            return {"ok": False, "error": "Диалог сохранения недоступен"}
        dest = target if target.suffix else target.with_suffix(src.suffix)
        dest = self._unique(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self._reveal(dest)
        return {"ok": True, "path": str(dest), "folder": str(dest.parent)}

    def save_clip(self, clip_id: str) -> Dict[str, Any]:
        from viralcut import db

        clip = db.get_clip(clip_id)
        if not clip or not clip.get("path"):
            return {"ok": False, "error": "Клип не найден"}
        src = Path(clip["path"])
        if not src.exists():
            return {"ok": False, "error": "Файл клипа не найден на диске"}
        name = f"{clip.get('idx', 0) + 1:02d}_{self._safe(clip.get('title') or 'clip')}.mp4"
        from viralcut import settings as app_settings

        conf = app_settings.get()
        preset_dir = conf.get("clips_dir") or ""
        ask = self._ask_enabled() and (conf.get("ask_folder", True) or not preset_dir)
        # VIRALCUT_ASK_SAVE=0 — сохранять молча (пакетный экспорт).
        target = self._ask_target(name) if ask else "unavailable"
        if target is None:
            return {"ok": False, "cancelled": True}
        if target == "unavailable":
            base = Path(preset_dir) if preset_dir else self._downloads()
            base.mkdir(parents=True, exist_ok=True)
            dest = self._unique(base / name)
        else:
            dest = target if target.suffix.lower() == ".mp4" else target.with_suffix(".mp4")
            dest = self._unique(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self._reveal(dest)
        return {"ok": True, "path": str(dest), "folder": str(dest.parent)}

    def save_zip(self, project_id: str) -> Dict[str, Any]:
        from viralcut import db, pipeline
        from viralcut.config import PROJECTS_DIR

        project = db.get_project(project_id)
        if not project:
            return {"ok": False, "error": "Проект не найден"}
        archive = PROJECTS_DIR / project_id / f"viralcut_{project_id}.zip"
        try:
            pipeline.export_zip(project_id, archive)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:300]}
        name = f"viralcut_{self._safe(project.get('title') or 'clips')}.zip"
        target = self._ask_target(name) if self._ask_enabled() else "unavailable"
        if target is None:
            return {"ok": False, "cancelled": True}
        if target == "unavailable":
            base = Path(preset_dir) if preset_dir else self._downloads()
            base.mkdir(parents=True, exist_ok=True)
            dest = self._unique(base / name)
        else:
            dest = target if target.suffix.lower() == ".zip" else target.with_suffix(".zip")
            dest = self._unique(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, dest)
        self._reveal(dest)
        return {"ok": True, "path": str(dest), "folder": str(dest.parent)}

    def open_url(self, url: str) -> Dict[str, Any]:
        """Открывает внешнюю ссылку в системном браузере (Telegram-канал и т.п.)."""
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            return {"ok": False, "error": "Некорректная ссылка"}
        try:
            import webbrowser

            webbrowser.open(url, new=2, autoraise=True)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    def open_project_folder(self, project_id: str) -> Dict[str, Any]:
        from viralcut.config import PROJECTS_DIR

        folder = PROJECTS_DIR / project_id / "clips"
        if not folder.exists():
            folder = PROJECTS_DIR / project_id
        try:
            if IS_WINDOWS:
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif IS_MACOS:
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(folder)}


def default_data_dir() -> Path:
    env = os.environ.get("VIRALCUT_DATA")
    if env:
        return Path(env).expanduser().resolve()
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return root / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / APP_NAME


def free_port(start: int = 8765, attempts: int = 40) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Не удалось найти свободный порт")


def wait_health(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200 and json.loads(resp.read().decode()).get("ok"):
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _node_name() -> str:
    return "node.exe" if IS_WINDOWS else "node"


def _vendor_ready(path: Path) -> bool:
    return ((path / _node_name()).exists()
            and (path / "pot" / "build" / "main.js").exists())


def _extract_vendor(archive: Path, dest: Path) -> None:
    """Распаковывает vendor.tar.gz (Node + PO-провайдер) атомарно."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / "vendor.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(tmp, filter="data")  # Python 3.12+
        except TypeError:
            tar.extractall(tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    node = dest / _node_name()
    if node.exists() and not IS_WINDOWS:
        node.chmod(0o755)


def vendor_dir(data_dir: Path) -> Path:
    """Возвращает папку с Node и PO-провайдером, распаковывая архив при первом запуске.

    Из исходников папка `desktop/vendor` обычно уже есть; если её нет (например,
    распаковали только архив тестовой сборки), распаковываем `vendor.tar.gz`
    в папку данных — так Windows-тест из исходников и упакованное приложение
    ведут себя одинаково.
    """
    source_vendor = Path(__file__).resolve().parent / "vendor"
    if not getattr(sys, "frozen", False):
        if _vendor_ready(source_vendor):
            return source_vendor
        dest = data_dir / "runtime" / "vendor"
        if _vendor_ready(dest):
            return dest
        archive = Path(__file__).resolve().parent / "vendor.tar.gz"
        if archive.is_file():
            try:
                _extract_vendor(archive, dest)
            except Exception as exc:  # noqa: BLE001 — без Node просто нет HD
                print(f"vendor не распакован: {exc}", flush=True)
                return source_vendor
        return dest

    dest = data_dir / "runtime" / "vendor"
    if _vendor_ready(dest):
        return dest

    exe = Path(sys.executable).resolve()
    candidates = [
        Path(getattr(sys, "_MEIPASS", exe.parent)) / "bundle" / "vendor.tar.gz",
        Path(getattr(sys, "_MEIPASS", exe.parent)) / "vendor.tar.gz",
        exe.parent.parent / "Frameworks" / "bundle" / "vendor.tar.gz",
        exe.parent.parent / "Resources" / "bundle" / "vendor.tar.gz",
        exe.parent.parent / "Resources" / "vendor.tar.gz",
    ]
    archive = next((c for c in candidates if c.is_file()), None)
    if archive is None:
        return dest
    try:
        _extract_vendor(archive, dest)
    except Exception as exc:  # noqa: BLE001
        print(f"vendor не распакован: {exc}", flush=True)
    return dest


def seed_ejs_cache(data_dir: Path, vendor: Path) -> None:
    """Кладём solver для YouTube JS-челленджей в кэш, чтобы не качать его при первом видео."""
    src = vendor / "ejs" / "challenge-solver"
    dst = data_dir / "cache" / "yt-dlp" / "challenge-solver"
    if src.exists() and not (dst / "lib.json").exists():
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"ejs cache seed failed: {exc}", flush=True)


def start_pot_provider(data_dir: Path, vendor: Path, log) -> "subprocess.Popen | None":
    """Поднимает локальный PO-token провайдер (нужен для HD с YouTube)."""
    node = vendor / _node_name()
    server = vendor / "pot" / "build" / "main.js"
    if not node.exists() or not server.exists():
        print("POT provider not bundled — HD may be unavailable", flush=True)
        return None
    try:
        port = free_port(4416)
        env = os.environ.copy()
        env["XDG_CACHE_HOME"] = str(data_dir / "cache")
        extra: dict = {}
        if IS_WINDOWS:
            extra["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [str(node), str(server), "--port", str(port)],
            cwd=str(vendor / "pot"), env=env, stdout=log, stderr=log, **extra,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1) as resp:
                    if resp.status == 200:
                        os.environ["VIRALCUT_POT_URL"] = f"http://127.0.0.1:{port}"
                        os.environ["VIRALCUT_NODE"] = str(node)
                        print(f"POT provider: http://127.0.0.1:{port}", flush=True)
                        return proc
            except Exception:
                time.sleep(0.2)
        print("POT provider did not start in time", flush=True)
        proc.terminate()
    except Exception as exc:  # noqa: BLE001
        print(f"POT provider failed: {exc}", flush=True)
    return None


def setup_paths(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VIRALCUT_DATA"] = str(data_dir)
    os.environ.setdefault("XDG_CACHE_HOME", str(data_dir / "cache"))
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if str(bundle) not in sys.path:
            sys.path.insert(0, str(bundle))
        bundled_model = bundle / "models" / "whisper-small"
        if (bundled_model / "model.bin").is_file():
            os.environ["VIRALCUT_WHISPER_DIR"] = str(bundled_model)


def _acquire_instance_lock(data_dir: Path):
    """Межплатформенная блокировка одного экземпляра на папку данных."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "instance.lock", "a+", encoding="utf-8")
    try:
        if IS_WINDOWS:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        try:
            handle.close()
        except Exception:
            pass
        return None


# Дескриптор для faulthandler держим живым: закрытый файл = потерянный дамп.
_CRASH_FILES: List[Any] = []


def _prepare_log(data_dir: Path, max_bytes: int = 8 * 1024 * 1024) -> Path:
    """Путь к app.log с ротацией: длинный лог не должен расти бесконечно.

    Ротация делается до открытия файла: приложение дописывает лог, а старый
    сдвигается в app.log.1 — так предыдущая авария не теряется.
    """
    log_path = data_dir / "app.log"
    try:
        if log_path.is_file() and log_path.stat().st_size > max_bytes:
            backup = data_dir / "app.log.1"
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
            log_path.replace(backup)
    except OSError:
        pass
    return log_path


def _install_crash_logging(log_path: Path, log: Any) -> None:
    """faulthandler + перехват исключений потоков: пишем всё в app.log.

    faulthandler пишет в отдельный НЕбуферизованный файл: при нативном падении
    буфер текстового лога может не успеть сброситься на диск, и дамп стека
    потеряется. Поэтому `-version`-лог и дамп живут в одном файле, но через
    разные дескрипторы.
    """
    crash_file = None
    try:
        crash_file = open(log_path, "ab", buffering=0)  # noqa: SIM115 — живёт до выхода
        _CRASH_FILES.append(crash_file)
        faulthandler.enable(file=crash_file, all_threads=True)
    except Exception:
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            pass

    def _sig(name: str) -> None:
        # register() есть только на Unix; на Windows дампы даёт enable().
        # Регистрируем ТОЛЬКО сигналы штатного завершения: фатальные (SEGV/ABRT)
        # должен писать enable() — он добавляет заголовок «Fatal Python error»,
        # по которому разбор лога отличает краш от «приложение закрыли снаружи».
        if not hasattr(faulthandler, "register") or not crash_file:
            return
        try:
            value = getattr(signal, name, None)
            if value is None:
                return
            faulthandler.register(value, file=crash_file, all_threads=True, chain=True)
        except Exception:
            pass

    # SIGTERM/SIGINT: приложение закрывают снаружи — в лог попадёт стек потоков
    for name in ("SIGTERM", "SIGINT"):
        _sig(name)

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        print("=== uncaught exception (main thread) ===", file=log, flush=True)
        traceback.print_exception(exc_type, exc_value, exc_tb, file=log)
        print(f"=== {APP_NAME} stop after uncaught exception ===", file=log, flush=True)
        try:
            log.flush()
        except Exception:
            pass

    def _thread_excepthook(args: Any) -> None:
        print(f"=== uncaught exception in thread {getattr(args.thread, 'name', '?')} ===",
              file=log, flush=True)
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=log)
        try:
            log.flush()
        except Exception:
            pass

    sys.excepthook = _excepthook  # type: ignore[assignment]
    try:
        threading.excepthook = _thread_excepthook  # type: ignore[assignment]
    except Exception:
        pass


def _webview2_version() -> str:
    """Версия WebView2 Runtime из реестра Windows (пустая строка — не найден)."""
    if not IS_WINDOWS:
        return ""
    try:
        import winreg  # type: ignore[import-not-found]

        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        candidates = [
            (winreg.HKEY_LOCAL_MACHINE,
             rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE,
             rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER,
             rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
        ]
        for root, path in candidates:
            try:
                with winreg.OpenKey(root, path) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                    if value:
                        return str(value)
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def _fatal_dialog(text: str) -> None:
    """Показать ошибку пользователю. На Windows — MessageBox (прототипы явные).

    Без argtypes/restype ctypes мог бы испортить стек на 64-битной Windows —
    ровно та ошибка, из-за которой падал мониторинг GPU.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, text[:1500], f"{APP_NAME} — ошибка", 0x10)
    except Exception:  # noqa: BLE001
        pass


def _startup_selfcheck() -> None:
    """Пишет в app.log всё, что нужно для разбора проблем на чужой машине.

    Строки с префиксом [selfcheck] пользователь присылает вместе с логом: по ним
    сразу видно, чем собран ffmpeg, есть ли ffprobe, где модель и как читается GPU.
    """
    def emit(text: str) -> None:
        print(f"[selfcheck] {text}", flush=True)

    try:
        from viralcut import config as vc_config

        binary = vc_config.ffmpeg()
        emit(f"ffmpeg: {binary}")
        try:
            proc = subprocess.run(
                [binary, "-hide_banner", "-version"], capture_output=True, text=True,
                timeout=20, creationflags=(0x08000000 if IS_WINDOWS else 0))
            first = (proc.stdout or "").splitlines()[0] if proc.stdout else "нет вывода"
            emit(f"ffmpeg-version: {first}")
        except Exception as exc:  # noqa: BLE001
            emit(f"ffmpeg-version: ОШИБКА {exc}")
        emit(f"ffprobe: {vc_config.ffprobe() or 'нет'}")
    except Exception as exc:  # noqa: BLE001
        emit(f"ffmpeg: ОШИБКА {exc}")

    emit(f"model: {os.environ.get('VIRALCUT_WHISPER_DIR') or 'скачается при первом запуске'}")
    try:
        from viralcut import system_stats

        gpu = system_stats.gpu()
        source = gpu.get("source") or ("ioreg" if IS_MACOS else "none")
        emit(f"gpu: {gpu.get('name') or '?'} | {gpu.get('percent')}% | источник: {source}")
    except Exception as exc:  # noqa: BLE001
        emit(f"gpu: ОШИБКА {exc}")

    if IS_WINDOWS:
        version = _webview2_version()
        emit(f"webview2: {version or 'не найден в реестре'}")
    try:
        import webview

        emit(f"webview: {getattr(webview, '__version__', '?')}")
    except Exception as exc:  # noqa: BLE001
        emit(f"webview: НЕ ЗАГРУЖАЕТСЯ {exc}")


def _startup_error_text(exc: Exception, data_dir: Path) -> str:
    """Понятный текст ошибки запуска вместо голого трейсбека."""
    message = str(exc)
    low = message.lower()
    log = data_dir / "app.log"
    if "webview2" in low or "edgechromium" in low or "webview" in low or "microsoft.web" in low:
        return ("Не удалось открыть окно: не установлен WebView2 Runtime.\n"
                "Скачайте «Microsoft Edge WebView2 Runtime» с сайта Microsoft "
                "и запустите приложение снова.\n\n"
                f"Подробности в журнале:\n{log}")
    if "python.runtime" in low or "pythonnet" in low or "clr" in low:
        return ("Не удалось загрузить оболочку окна (pythonnet).\n"
                "Переустановите приложение или запустите install_windows.bat.\n\n"
                f"Подробности в журнале:\n{log}")
    return (f"Не удалось запустить {APP_NAME}.\n\n{message[:600]}\n\n"
            f"Подробности в журнале:\n{log}")


def main() -> None:
    data_dir = default_data_dir()
    setup_paths(data_dir)
    # один экземпляр на папку данных
    lock = _acquire_instance_lock(data_dir)
    if lock is None:
        try:
            print("ViralCut уже запущен — второй экземпляр не нужен", flush=True)
        except Exception:
            pass
        return

    log_path = _prepare_log(data_dir)
    log = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stdout = log  # type: ignore[assignment]
    sys.stderr = log  # type: ignore[assignment]
    print(f"\n=== {APP_NAME} start {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
    _install_crash_logging(log_path, log)
    try:
        from viralcut._version import __version__ as app_version
    except Exception:  # noqa: BLE001 — версия не критична для запуска
        app_version = "unknown"
    print(f"version: {app_version}", flush=True)
    print(f"data: {data_dir}", flush=True)
    print(f"platform: {sys.platform}", flush=True)
    print(f"frozen: {getattr(sys, 'frozen', False)}", flush=True)
    print(f"log: {log_path}", flush=True)
    _startup_selfcheck()

    pot_proc = None
    server = None
    clean_exit = False

    def shutdown(*_args: Any) -> None:
        nonlocal pot_proc
        try:
            if server is not None:
                server.should_exit = True
        except Exception:
            pass
        if pot_proc is not None:
            try:
                pot_proc.terminate()
                pot_proc.wait(timeout=3)
            except Exception:
                try:
                    pot_proc.kill()
                except Exception:
                    pass
            pot_proc = None

    try:
        vendor = vendor_dir(data_dir)
        seed_ejs_cache(data_dir, vendor)
        pot_proc = start_pot_provider(data_dir, vendor, log)

        import uvicorn

        from viralcut.main import app as fastapi_app

        port = int(os.environ.get("VIRALCUT_PORT") or free_port())
        config = uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="viralcut-http", daemon=True)
        thread.start()

        if not wait_health(port):
            raise RuntimeError("Сервер не запустился за 40 секунд")

        print(f"server: http://127.0.0.1:{port}", flush=True)
        url = f"http://127.0.0.1:{port}/"

        if os.environ.get("VIRALCUT_HEADLESS"):
            print("headless mode", flush=True)
            while True:
                time.sleep(1)

        import webview

        window = webview.create_window(
            APP_NAME,
            url,
            width=1380,
            height=900,
            min_size=(1024, 720),
            background_color="#07070a",
            js_api=DesktopApi(data_dir),
        )
        try:
            window.events.closing += shutdown
        except Exception:
            pass
        auto_close = os.environ.get("VIRALCUT_AUTOCLOSE")
        if auto_close:
            threading.Timer(float(auto_close), window.destroy).start()
        webview.start()
        shutdown()
        time.sleep(0.3)
        clean_exit = True
    except Exception as exc:  # noqa: BLE001
        print(traceback.format_exc(), flush=True)
        try:
            log.flush()
        except Exception:
            pass
        _fatal_dialog(_startup_error_text(exc, data_dir))
        raise
    finally:
        shutdown()
        try:
            if clean_exit:
                print(f"=== {APP_NAME} stop {time.strftime('%Y-%m-%d %H:%M:%S')} ===",
                      flush=True)
            else:
                print(f"=== {APP_NAME} exit without stop marker {time.strftime('%Y-%m-%d %H:%M:%S')} ===",
                      flush=True)
            log.flush()
        except Exception:
            pass
        if clean_exit and getattr(sys, "frozen", False):
            # pywebview/WebKit на macOS иногда падает уже после закрытия окна;
            # аккуратно завершаем процесс, не давая системе показать «ошибку».
            try:
                log.flush()
            except Exception:
                pass
            os._exit(0)


if __name__ == "__main__":
    main()
