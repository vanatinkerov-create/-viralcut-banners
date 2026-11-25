"""Разбор app.log: что случилось перед закрытием приложения.

На Windows пользователь видит «нажал Пересобрать — окно закрылось», а лога нет:
питон-трейсбек уходит в app.log, но при фатальном сбое (нативный крэш WebView2,
pythonnet, ffmpeg-драйвера) его никто не читает. Здесь собрано всё, чтобы по
файлу понять причину без отладчика:

  * сессии запуска и признаки аварийного завершения (нет маркера stop);
  * дампы faulthandler («Fatal Python error», «Thread 0x…», «Windows fatal
    exception») — это и есть тот самый лог падения;
  * последний этап работы (пересборка, рендер) до падения;
  * последние ошибки и трейсбеки;
  * короткий вердикт «что делать».

Модуль ничего не меняет на диске и работает на любой ОС — им можно разбирать
app.log, присланный с Windows.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_READ_BYTES = 4 * 1024 * 1024
TAIL_LINES = 40

START_MARK = re.compile(r"===\s*ViralCut start\s+([0-9:\- .]+)===")
STOP_MARK = re.compile(r"===\s*ViralCut (?:stop|exit)\b", re.I)
# Заголовки, которые пишет faulthandler.enable() при настоящем падении.
# «Current thread 0x…» сюда не входит: такой же дамп без заголовка пишет
# faulthandler.register() при SIGTERM/SIGINT, когда приложение просто закрывают.
FATAL_MARKERS = (
    "fatal python error",
    "windows fatal exception",
    "segmentation fault",
    "access violation",
    "stack overflow",
    "aborted (core dumped)",
    "fatal error",
)
DUMP_MARKERS = ("current thread 0x", "thread 0x")
TRACEBACK_MARK = "Traceback (most recent call last)"
VERSION_MARK = re.compile(r"^version:\s*(\S+)", re.M)
STAGE_MARK = re.compile(
    r"\[(?:api|render|youtube|section|download|pip|live|stage)\]\s*(.+)$")
STAGE_WORDS = (
    "Пересбор", "Повтор в 1080", "Рендер", "Скачивание", "Объединение",
    "Распознавание", "Нарезка", "Запуск", "Обновление",
)


def default_log_path(data_dir: Optional[Path] = None) -> Optional[Path]:
    """Где лежит app.log: data_dir -> VIRALCUT_DATA -> платформенная папка."""
    candidates: List[Path] = []
    if data_dir is not None:
        candidates.append(Path(data_dir) / "app.log")
    env = os.environ.get("VIRALCUT_DATA")
    if env:
        candidates.append(Path(env) / "app.log")
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        candidates.append(root / "ViralCut" / "app.log")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "ViralCut" / "app.log")
    else:
        candidates.append(Path(os.environ.get("XDG_DATA_HOME")
                               or (Path.home() / ".local" / "share")) / "ViralCut" / "app.log")
    for path in candidates:
        if path.is_file():
            return path
    return None


def read_log(path: Path, max_bytes: int = MAX_READ_BYTES) -> str:
    """Читает хвост лога: старые мегабайты для разбора не нужны."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # первую обрезанную строку выбрасываем
            raw = fh.read()
        return raw.decode("utf-8", "replace")
    except OSError:
        return ""


def split_sessions(text: str) -> List[Dict[str, Any]]:
    """Делит лог на запуски: каждый начинается с маркера ViralCut start."""
    sessions: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        match = START_MARK.search(line)
        if match:
            current = {"started_at": match.group(1) or "", "lines": []}
            sessions.append(current)
            continue
        if current is None:
            current = {"started_at": "", "lines": []}
            sessions.append(current)
        current["lines"].append(line)
    return sessions


def _has_fatal(lines: List[str]) -> bool:
    for line in lines:
        low = line.lower()
        if any(marker in low for marker in FATAL_MARKERS):
            return True
    return False


def _has_dump(lines: List[str]) -> bool:
    """Есть ли стек потоков в логе (дамп faulthandler, любой природы)."""
    for line in lines:
        low = line.lower()
        if any(marker in low for marker in DUMP_MARKERS):
            return True
    return False


def _tracebacks(lines: List[str]) -> List[str]:
    """Последние строки трейсбеков: тип ошибки и последняя строка стека."""
    traces: List[str] = []
    for index, line in enumerate(lines):
        if TRACEBACK_MARK in line:
            block = lines[index:index + 40]
            last = next((item.strip() for item in reversed(block) if item.strip()), "")
            if last and last not in traces:
                traces.append(last[:300])
    return traces[-3:]


def _error_lines(lines: List[str]) -> List[str]:
    errors: List[str] = []
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if not stripped:
            continue
        if (low.startswith("error") or " ошибка" in low or "failed:" in low
                or low.startswith("traceback") or "exception" in low):
            if stripped not in errors:
                errors.append(stripped[:300])
    return errors[-6:]


def _last_stage(lines: List[str]) -> str:
    """Последний понятный этап работы приложения перед падением.

    Сначала ищем человеческие этапы («Пересборка…», «Рендер: 3 из 10»), и только
    потом отладочные строки вида `[render] cmd: ffmpeg …` — они полезны, но
    пользователю ничего не говорят.
    """
    for line in reversed(lines):
        stripped = line.strip()
        if any(word in stripped for word in STAGE_WORDS):
            return stripped[:160]
    for line in reversed(lines):
        stripped = line.strip()
        match = STAGE_MARK.search(stripped)
        if match:
            return match.group(1)[:160]
    return ""


def _advice(crash: bool, fatal: bool, stage: str, errors: List[str],
            running: bool = False, legacy: bool = False,
            signal_exit: bool = False) -> str:
    if running:
        return ("Журнал обновлялся только что, аварий не найдено — приложение, похоже, "
                "ещё работает. Для разбора падения повторите после закрытия окна.")
    if fatal:
        return ("Приложение упало в нативном коде (faulthandler записал дамп). "
                "Смотрите последние строки «Thread 0x…»: если в стеке webview/pythonnet — "
                "это оболочка окна; если ffmpeg/avcodec — декодер. "
                "Пришлите этот дамп разработчику.")
    if signal_exit:
        return ("Приложение завершили сигналом извне (SIGTERM/SIGINT) или сняли из "
                "диспетчера задач — faulthandler оставил стек потоков, но это не "
                "нативное падение. Если закрытие было неожиданным, смотрите "
                "последний этап выше.")
    if legacy:
        return ("Лог от старой версии: маркеров остановки и версии в нём нет, поэтому "
                "отличить штатное закрытие от падения нельзя. Обновитесь до 1.5.8+ — "
                "новые логи содержат версию, маркеры запуска/остановки и дампы "
                "faulthandler.")
    if crash:
        return ("Запуск завершился без маркера остановки: приложение закрылось аварийно "
                "или его снял диспетчер задач. Последний этап — «%s». "
                "Повторите: faulthandler в 1.5.8+ пишет дамп в этот же лог."
                % (stage or "неизвестен"))
    if errors:
        return "Явного падения нет, но были ошибки: " + errors[-1]
    return "Аварий в логе не найдено."


def summarize(text: str, log_age: Optional[float] = None) -> Dict[str, Any]:
    """Короткий разбор лога: сессии, падения, последний этап, вердикт.

    log_age — сколько секунд назад файл менялся. Свежий лог без маркера stop и
    без дампа — это работающее приложение, а не падение: иначе кнопка
    «Разобрать журнал» пугала бы аварией при каждом открытии.
    """
    sessions = split_sessions(text)
    lines = text.splitlines()
    last = sessions[-1]["lines"] if sessions else []
    fatal = _has_fatal(lines)
    thread_dump = _has_dump(lines)
    # аварийным считаем запуск без маркера stop — но только у последней сессии:
    # прошлые запуски могли закрыть окно или приложение обновиться
    last_has_stop = any(STOP_MARK.search(line) for line in last)
    # старые сборки (до 1.5.8) маркеров не писали: по такому логу судить нельзя
    version_match = None
    for line in reversed(lines):
        version_match = VERSION_MARK.match(line.strip())
        if version_match:
            break
    # свежий лог без stop — это работающее приложение, а не падение
    running = bool(last) and not last_has_stop and not fatal and not thread_dump and (
        log_age is not None and log_age < 180)
    # дамп без фатального заголовка = приложение закрыто сигналом, а не упало
    signal_exit = bool(thread_dump and not fatal and not last_has_stop)
    legacy = (version_match is None and not fatal and not last_has_stop
              and not running and not signal_exit)
    crash = bool(last) and not last_has_stop and not legacy and not running and not signal_exit and (
        fatal or _looks_abrupt(last))
    stage = _last_stage(last) or _last_stage(lines)
    errors = _error_lines(lines)
    traces = _tracebacks(lines)
    return {
        "sessions": len(sessions),
        "started_at": sessions[-1]["started_at"] if sessions else "",
        "clean_exit": last_has_stop,
        "crash": crash,
        "running": running,
        "fatal_dump": fatal,
        "thread_dump": thread_dump,
        "signal_exit": signal_exit,
        "legacy_log": legacy,
        "version": version_match.group(1) if version_match else "",
        "last_stage": stage,
        "errors": errors,
        "tracebacks": traces,
        "advice": _advice(crash, fatal, stage, errors, running, legacy, signal_exit),
        "lines": len(lines),
    }


def _looks_abrupt(lines: List[str]) -> bool:
    """Лог обрывается посреди работы: есть этапы, но нет ни stop, ни конца."""
    if not lines:
        return False
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        return False
    if any(STOP_MARK.search(line) for line in meaningful):
        return False
    # после старта не было даже строки о готовности сервера — это падение на старте
    if len(meaningful) <= 2:
        return True
    return True


def _system_lines(system: Optional[Dict[str, Any]]) -> List[str]:
    """Что за сборка и с чем она собрана — по данным /api/health."""
    if not system:
        return []
    parts: List[str] = []
    version = system.get("version") or ""
    if version:
        parts.append(f"версия {version}")
    if system.get("platform"):
        parts.append(str(system["platform"]))
    if system.get("frozen") is not None:
        parts.append("сборка" if system.get("frozen") else "из исходников")
    if system.get("hw"):
        parts.append(f"энкодер {system['hw']}")
    if system.get("model") is not None:
        parts.append("модель на месте" if system.get("model") else "модель скачается")
    lines: List[str] = []
    if parts:
        lines.append("Система: " + " · ".join(parts))
    if system.get("ffmpeg_path"):
        lines.append(f"ffmpeg: {system['ffmpeg_path']}")
    if system.get("ffprobe"):
        lines.append(f"ffprobe: {system['ffprobe']}")
    else:
        lines.append("ffprobe: нет (не критично)")
    return lines


def format_report(summary: Dict[str, Any], tail: List[str], log_path: Optional[Path],
                  system: Optional[Dict[str, Any]] = None) -> str:
    """Текстовый отчёт для консоли и для письма разработчику."""
    out: List[str] = []
    out.append("=== ViralCut: разбор app.log ===")
    out.append(f"Файл: {log_path if log_path else 'не найден'}")
    out.extend(_system_lines(system))
    out.append(f"Запусков в логе: {summary['sessions']} · строк: {summary['lines']}")
    if summary.get("started_at"):
        out.append(f"Последний запуск: {summary['started_at']}")
    out.append(f"Штатное закрытие: {'да' if summary['clean_exit'] else 'нет'}")
    if summary.get("version"):
        out.append(f"Версия приложения: {summary['version']}")
    if summary.get("legacy_log"):
        out.append("Лог от старой версии: маркеров остановки нет, судить о падении нельзя")
    out.append(f"Аварийное завершение: {'да' if summary['crash'] else 'нет'}")
    if summary.get("running"):
        out.append("Похоже, приложение ещё работает (лог обновлялся только что)")
    if summary["fatal_dump"]:
        out.append("Дамп faulthandler: есть (нативное падение)")
    elif summary.get("thread_dump"):
        out.append("Стек потоков: есть (завершение сигналом, не падение)")
    if summary["last_stage"]:
        out.append(f"Последний этап: {summary['last_stage']}")
    if summary["tracebacks"]:
        out.append("Трейсбеки:")
        out.extend(f"  - {item}" for item in summary["tracebacks"])
    if summary["errors"]:
        out.append("Последние ошибки:")
        out.extend(f"  - {item}" for item in summary["errors"])
    out.append("")
    out.append(f"Вердикт: {summary['advice']}")
    if tail:
        out.append("")
        out.append(f"Последние {len(tail)} строк лога:")
        out.extend(tail)
    return "\n".join(out)


def report(path: Optional[Path] = None, tail_lines: int = TAIL_LINES,
           data_dir: Optional[Path] = None,
           system: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Полный разбор: где лог, что внутри, что показать пользователю."""
    log_path = Path(path) if path else default_log_path(data_dir)
    if log_path is None or not log_path.is_file():
        return {"log_path": None, "summary": None, "tail": [],
                "report": "app.log не найден. Запустите настольное приложение ViralCut "
                          "и повторите: файл появится в папке данных."}
    text = read_log(log_path)
    age: Optional[float] = None
    try:
        age = max(0.0, time.time() - log_path.stat().st_mtime)
    except OSError:
        age = None
    summary = summarize(text, log_age=age)
    tail = text.splitlines()[-max(1, int(tail_lines)):]
    return {"log_path": str(log_path), "summary": summary, "system": system or {},
            "tail": tail, "report": format_report(summary, tail, log_path, system)}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Разбор app.log приложения ViralCut: падения, этапы, ошибки.")
    parser.add_argument("path", nargs="?", help="путь к app.log (по умолчанию — папка данных)")
    parser.add_argument("--tail", type=int, default=TAIL_LINES,
                        help="сколько последних строк показать (по умолчанию 40)")
    args = parser.parse_args(argv)
    result = report(Path(args.path) if args.path else None, tail_lines=args.tail)
    print(result["report"])
    return 0 if result.get("log_path") else 2


if __name__ == "__main__":
    raise SystemExit(main())
