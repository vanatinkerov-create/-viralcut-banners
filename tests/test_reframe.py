"""Тесты кадрирования: лицо в кадре, границы окна, плавность и фолбэк без лиц."""
from __future__ import annotations

import unittest
from pathlib import Path

from viralcut import reframe

STOCK = Path("docs/promo/live-action/stock")


class CropPathTest(unittest.TestCase):
    def _path(self, name: str, duration: float = 6.0):
        src = STOCK / name
        if not src.exists():
            self.skipTest(f"нет файла {src}")
        return reframe.build_crop_path(src, 0.0, duration, aspect="9:16")

    def test_bounds_and_shape(self):
        path = self._path("52175-1080.mp4")
        self.assertIsNotNone(path)
        self.assertGreater(len(path["x"]), 3)
        self.assertEqual(len(path["x"]), len(path["y"]))
        self.assertEqual(len(path["x"]), len(path["times"]))
        src_w, src_h = path["source"]
        for x, y in zip(path["x"], path["y"]):
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + path["crop_w"], src_w + 0.5)
            self.assertLessEqual(y + path["crop_h"], src_h + 0.5)

    def test_detects_faces(self):
        path = self._path("52175-1080.mp4")
        self.assertGreater(path["coverage"], 0.6, "лица в этом клипе видны почти всегда")

    def test_smooth_on_multi_person_shot(self):
        path = self._path("2948-1080.mp4", duration=20.0)
        jumps = [abs(path["x"][i + 1] - path["x"][i]) for i in range(len(path["x"]) - 1)]
        self.assertLess(max(jumps), 120.0, "окно не должно перескакивать между людьми")

    def test_no_faces_center_fallback(self):
        path = self._path("308-1080.mp4")
        self.assertEqual(path["coverage"], 0.0)
        xs = set(path["x"])
        self.assertEqual(len(xs), 1, "без лиц окно стоит по центру")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PathStabilityTest(unittest.TestCase):
    """Путь окна не должен дёргаться и не должен быть «простынёй» для ffmpeg."""

    def _path(self, name: str, duration: float = 20.0):
        src = STOCK / name
        if not src.exists():
            self.skipTest(f"нет файла {src}")
        return reframe.build_crop_path(src, 0.0, duration, aspect="9:16")

    def test_path_is_compact_for_ffmpeg(self):
        path = self._path("2948-1080.mp4", duration=30.0)
        expr_x, expr_y = reframe.crop_expressions(path)
        self.assertLess(len(expr_x), 2000, "x-выражение длиннее лимита ffmpeg")
        self.assertLess(len(expr_y), 2000)
        self.assertLessEqual(len(path["x"]), 140)

    def test_no_ping_pong_between_speakers(self):
        path = self._path("2948-1080.mp4")
        span = max(path["x"]) - min(path["x"])
        self.assertLess(span, 300.0, "окно гуляет между людьми на всю ширину кадра")
        steps = [abs(path["x"][i + 1] - path["x"][i]) for i in range(len(path["x"]) - 1)]
        self.assertLess(max(steps), 120.0)

    def test_dense_enough_for_smoothness(self):
        path = self._path("52175-1080.mp4")
        gap = max(path["times"][i + 1] - path["times"][i] for i in range(len(path["times"]) - 1))
        self.assertLess(gap, 2.0, "слишком редкие keyframe — движение будет ступеньками")


class CoarsePathTest(unittest.TestCase):
    """Аварийный путь: редкие точки, если ffmpeg отверг длинное выражение."""

    def test_coarse_path_is_smaller(self):
        path = {
            "times": [i * 0.1 for i in range(400)],
            "x": [100 + i * 0.2 for i in range(400)],
            "y": [50 + i * 0.05 for i in range(400)],
            "crop_w": 608, "crop_h": 1080, "source": [1920, 1080],
        }
        coarse = reframe._coarse_path(path, max_points=18)
        self.assertLessEqual(len(coarse["times"]), 18)
        self.assertEqual(len(coarse["times"]), len(coarse["x"]))
        expr, _ = reframe.crop_expressions(coarse)
        self.assertLess(len(expr), 600)
