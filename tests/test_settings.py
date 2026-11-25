"""Тесты пользовательских настроек: энкодер и папки сохранения."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viralcut import settings


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-settings-"))
        self.file = self.tmp / "settings.json"
        self.patch = mock.patch.object(settings, "SETTINGS_FILE", self.file)
        self.patch.start()
        for key in ("VIRALCUT_HW_ENCODE",):
            os.environ.pop(key, None)

    def tearDown(self):
        self.patch.stop()
        os.environ.pop("VIRALCUT_HW_ENCODE", None)

    def test_defaults(self):
        conf = settings.get()
        self.assertEqual(conf["encoder"], "auto")
        self.assertTrue(conf["ask_folder"])
        self.assertEqual(conf["download_dir"], "")

    def test_update_encoder_and_folders(self):
        folder = self.tmp / "clips"
        folder.mkdir()
        conf = settings.update({"encoder": "cpu", "clips_dir": str(folder)})
        self.assertEqual(conf["encoder"], "cpu")
        self.assertEqual(conf["clips_dir"], str(folder))
        self.assertEqual(settings.encoder_mode(), "cpu")

    def test_invalid_encoder_ignored(self):
        settings.update({"encoder": "gpu"})
        conf = settings.update({"encoder": "quantum"})
        self.assertEqual(conf["encoder"], "gpu")

    def test_env_override(self):
        settings.update({"encoder": "auto"})
        os.environ["VIRALCUT_HW_ENCODE"] = "0"
        self.assertEqual(settings.encoder_mode(), "cpu")
        os.environ["VIRALCUT_HW_ENCODE"] = "1"
        self.assertEqual(settings.encoder_mode(), "gpu")

    def test_missing_folder_is_dropped(self):
        conf = settings.update({"download_dir": str(self.tmp / "nope")})
        self.assertEqual(conf["download_dir"], "")


class EncoderChoiceTest(unittest.TestCase):
    def test_cpu_mode_disables_hardware(self):
        from viralcut import render

        with mock.patch.object(render, "_encoder_mode", return_value="cpu"):
            render._HW_CACHE.clear()
            self.assertIsNone(render.hw_encoder())
        render._HW_CACHE.clear()

    def test_cached_does_not_probe(self):
        from viralcut import render

        render._HW_CACHE.clear()
        self.assertIsNone(render.hw_cached())
        render._HW_CACHE["value"] = "h264_videotoolbox"
        self.assertEqual(render.hw_cached(), "h264_videotoolbox")
        render._HW_CACHE.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
