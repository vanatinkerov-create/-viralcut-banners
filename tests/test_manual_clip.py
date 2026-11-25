"""Нарезка по таймкоду: разбор времени и подрезка тишины."""
import unittest
from pathlib import Path
from unittest import mock

from viralcut import pipeline


class TimecodeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(pipeline.parse_timecode("12:40"), 760.0)
        self.assertEqual(pipeline.parse_timecode("1:02:03"), 3723.0)
        self.assertEqual(pipeline.parse_timecode("760"), 760.0)
        self.assertEqual(pipeline.parse_timecode("0:07.5"), 7.5)

    def test_invalid(self):
        self.assertIsNone(pipeline.parse_timecode(""))
        self.assertIsNone(pipeline.parse_timecode("abc"))


class TrimSilenceTest(unittest.TestCase):
    def test_trims_edges_not_more_than_limit(self):
        curve = [float(i) for i in range(200)]
        with mock.patch.object(pipeline.analyze, "energy_curve", return_value=(curve, -30.0)):
            start, end = pipeline.trim_silence(Path("x.mp4"), 10.0, 40.0)
        self.assertGreaterEqual(start, 10.0)
        self.assertLessEqual(start, 13.0)
        self.assertLessEqual(end, 40.0)
        self.assertGreaterEqual(end, 37.0)

    def test_keeps_short_clip(self):
        curve = [0.0] * 10
        with mock.patch.object(pipeline.analyze, "energy_curve", return_value=(curve, -30.0)):
            start, end = pipeline.trim_silence(Path("x.mp4"), 10.0, 12.0)
        self.assertEqual((start, end), (10.0, 12.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
