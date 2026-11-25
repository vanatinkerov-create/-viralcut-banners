"""Разбор app.log: падения, дампы faulthandler и «последний этап»."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from viralcut import diagnostics

START = "=== ViralCut start 2026-11-20 09:55:01 ==="
STOP = "=== ViralCut stop 2026-11-20 10:30:00 ==="

CLEAN_LOG = "\n".join([
    START,
    "data: C:\\Users\\ivan\\AppData\\Roaming\\ViralCut",
    "platform: win32",
    "frozen: True",
    "server: http://127.0.0.1:8765",
    "[api] Пересборка в 1080p: проект abc",
    "[render] Рендер: 10 из 10 готово",
    STOP,
])

CRASH_LOG = "\n".join([
    START,
    "frozen: True",
    "[api] Пересборка в 1080p: проект abc",
    "[render] cmd: ffmpeg -y -i source.mp4 ...",
    "Fatal Python error: Segmentation fault",
    "",
    "Current thread 0x00001234 (most recent call first):",
    '  File "viralcut\\render.py", line 120 in _run_encode',
    '  File "viralcut\\pipeline.py", line 700 in _render_one',
    "Windows fatal exception: access violation",
])

FATAL_OLD_SESSION = "\n".join([
    START,
    "platform: win32",
    "Fatal Python error: Aborted",
    "",
    STOP,
    START,
    "platform: win32",
    "[render] Рендер: 2 из 10 готово",
    "Traceback (most recent call last):",
    '  File "viralcut\\render.py", line 100 in render_clip',
    "RuntimeError: Не удалось отрендерить клип: ffmpeg exited",
])


def load_desktop_main():
    path = Path(__file__).resolve().parent.parent / "desktop" / "main.py"
    spec = importlib.util.spec_from_file_location("vc_desktop_main", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SummarizeTest(unittest.TestCase):
    def test_clean_exit(self):
        summary = diagnostics.summarize(CLEAN_LOG)
        self.assertTrue(summary["clean_exit"])
        self.assertFalse(summary["crash"])
        self.assertFalse(summary["fatal_dump"])
        self.assertIn("аварий", summary["advice"].lower())

    def test_native_crash_detected_with_stage(self):
        summary = diagnostics.summarize(CRASH_LOG)
        self.assertTrue(summary["fatal_dump"], "дамп faulthandler не распознан")
        self.assertTrue(summary["crash"])
        self.assertFalse(summary["clean_exit"])
        self.assertIn("Пересбор", summary["last_stage"])
        self.assertIn("нативн", summary["advice"])

    def test_crash_in_old_session_is_not_blamed_on_last(self):
        summary = diagnostics.summarize(FATAL_OLD_SESSION)
        self.assertTrue(summary["fatal_dump"])
        self.assertFalse(summary["clean_exit"])
        # последняя сессия оборвалась на рендере — именно это и важно показать
        self.assertIn("Рендер", summary["last_stage"])

    def test_traceback_extracted(self):
        summary = diagnostics.summarize(FATAL_OLD_SESSION)
        self.assertTrue(any("RuntimeError" in item for item in summary["tracebacks"]),
                        f"трейсбек потерян: {summary['tracebacks']}")

    def test_stage_prefers_human_line(self):
        summary = diagnostics.summarize(CRASH_LOG)
        self.assertNotIn("cmd: ffmpeg", summary["last_stage"])

    def test_fresh_log_is_running_not_crashed(self):
        running_log = "\n".join([
            START,
            "frozen: True",
            "[api] Пересборка в 1080p: проект abc",
            "[stage] Рендер 3 из 10 готово",
        ])
        summary = diagnostics.summarize(running_log, log_age=5.0)
        self.assertFalse(summary["crash"], "работающее приложение не должно считаться упавшим")
        self.assertTrue(summary["running"])
        self.assertIn("ещё работает", summary["advice"])

    def test_old_log_without_stop_is_crash(self):
        log = CLEAN_LOG.replace(STOP, "").replace("frozen: True", "frozen: True\nversion: 1.5.8")
        summary = diagnostics.summarize(log, log_age=3600.0)
        self.assertTrue(summary["crash"])
        self.assertEqual(summary["version"], "1.5.8")

    def test_log_without_version_is_legacy(self):
        legacy = "\n".join([START, "frozen: True", "download ok: 137+140"])
        summary = diagnostics.summarize(legacy, log_age=3600.0)
        self.assertFalse(summary["crash"], "по старому логу нельзя судить о падении")
        self.assertTrue(summary["legacy_log"])
        self.assertIn("старой версии", summary["advice"])

    def test_signal_dump_is_not_a_crash(self):
        # faulthandler.register(SIGTERM) пишет стек без заголовка «Fatal Python error»
        signal_log = "\n".join([
            START,
            "version: 1.5.8",
            "[stage] Рендер: 1 из 1 готово",
            "Thread 0x000000016ded3000 (most recent call first):",
            '  File "threading.py", line 359 in wait',
            "Current thread 0x00000001f4801300 (most recent call first):",
            '  File "main.py", line 625 in main',
        ])
        summary = diagnostics.summarize(signal_log, log_age=3600.0)
        self.assertTrue(summary["thread_dump"])
        self.assertFalse(summary["fatal_dump"])
        self.assertTrue(summary["signal_exit"])
        self.assertFalse(summary["crash"])
        self.assertFalse(summary["running"])
        self.assertIn("сигнал", summary["advice"])

    def test_fatal_dump_beats_legacy(self):
        legacy = "\n".join([START, "Fatal Python error: Aborted"])
        summary = diagnostics.summarize(legacy, log_age=3600.0)
        self.assertTrue(summary["crash"])
        self.assertTrue(summary["fatal_dump"])

    def test_empty_log(self):
        summary = diagnostics.summarize("")
        self.assertEqual(summary["sessions"], 0)
        self.assertFalse(summary["crash"])


class ReportTest(unittest.TestCase):
    def test_report_contains_path_and_verdict(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc-diag-"))
        log = tmp / "app.log"
        log.write_text(CRASH_LOG, encoding="utf-8")
        result = diagnostics.report(log, tail_lines=5)
        self.assertEqual(result["log_path"], str(log))
        self.assertIn("app.log", result["report"])
        self.assertIn("Вердикт", result["report"])
        self.assertEqual(len(result["tail"]), 5)

    def test_missing_log_is_explained(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc-diag-"))
        result = diagnostics.report(tmp / "нет.log")
        self.assertIsNone(result["log_path"])
        self.assertIn("не найден", result["report"])

    def test_tail_limited_to_last_lines(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc-diag-"))
        log = tmp / "app.log"
        log.write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")
        result = diagnostics.report(log, tail_lines=10)
        self.assertEqual(result["tail"][-1], "line 199")


class ReportSystemInfoTest(unittest.TestCase):
    def test_system_block_in_report(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc-diag-"))
        log = tmp / "app.log"
        log.write_text(CLEAN_LOG, encoding="utf-8")
        result = diagnostics.report(log, tail_lines=0, system={
            "version": "1.5.8", "platform": "win32", "frozen": True,
            "hw": "h264_nvenc", "model": True,
            "ffmpeg_path": r"C:\ViralCut\bin\ffmpeg.exe", "ffprobe": "",
        })
        self.assertIn("версия 1.5.8", result["report"])
        self.assertIn("win32", result["report"])
        self.assertIn("h264_nvenc", result["report"])
        self.assertIn("ffmpeg.exe", result["report"])
        self.assertIn("ffprobe: нет", result["report"])
        self.assertEqual(result["system"]["version"], "1.5.8")

    def test_report_without_system_still_works(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc-diag-"))
        log = tmp / "app.log"
        log.write_text(CLEAN_LOG, encoding="utf-8")
        self.assertNotIn("Система:", diagnostics.report(log, tail_lines=0)["report"])


class DesktopLogHelpersTest(unittest.TestCase):
    def test_rotates_long_log(self):
        main = load_desktop_main()
        tmp = Path(tempfile.mkdtemp(prefix="vc-log-"))
        (tmp / "app.log").write_text("x" * 64, encoding="utf-8")
        path = main._prepare_log(tmp, max_bytes=32)
        self.assertEqual(path, tmp / "app.log")
        self.assertTrue((tmp / "app.log.1").is_file())

    def test_vendor_archive_extracts_for_source_run(self):
        import tarfile

        main = load_desktop_main()
        tmp = Path(tempfile.mkdtemp(prefix="vc-vendor-"))
        src = tmp / "src"
        (src / "pot" / "build").mkdir(parents=True)
        # на Windows бинарник называется node.exe — проверяем то же имя, что и код
        node_name = main._node_name()
        (src / node_name).write_text("#!/bin/sh\n", encoding="utf-8")
        (src / "pot" / "build" / "main.js").write_text("// pot\n", encoding="utf-8")
        archive = tmp / "vendor.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(src / node_name, arcname=node_name)
            tar.add(src / "pot", arcname="pot")

        dest = tmp / "runtime" / "vendor"
        self.assertFalse(main._vendor_ready(dest))
        main._extract_vendor(archive, dest)
        self.assertTrue(main._vendor_ready(dest))
        if not main.IS_WINDOWS:
            self.assertTrue((dest / node_name).stat().st_mode & 0o111,
                            "node должен остаться исполняемым")

    def test_installs_faulthandler_and_excepthook(self):
        import faulthandler

        main = load_desktop_main()
        tmp = Path(tempfile.mkdtemp(prefix="vc-log-"))
        log_path = tmp / "app.log"
        log = open(log_path, "a", buffering=1, encoding="utf-8")
        old_hook = sys.excepthook
        try:
            main._install_crash_logging(log_path, log)
            self.assertTrue(faulthandler.is_enabled())
            self.assertIsNot(sys.excepthook, old_hook)
            sys.excepthook(ZeroDivisionError, ZeroDivisionError("тест"), None)
            log.flush()
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("uncaught exception", text)
            self.assertIn("ZeroDivisionError: тест", text)
            self.assertIn("stop after uncaught exception", text)
        finally:
            sys.excepthook = old_hook
            log.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
