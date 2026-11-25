"""Вырезание пауз: план склейки доходит до рендера, слова и кроп едут вместе."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viralcut import analyze, config, pipeline, reframe, render, subtitles


def make_paused_source(path: Path) -> Path:
    """9 секунд с речью-тоном и двумя паузами: 2–4 с и 6–7.5 с."""
    graph = ("sine=frequency=440:duration=2[a0];"
             "anullsrc=r=16000:cl=mono,atrim=duration=2[a1];"
             "sine=frequency=440:duration=2[a2];"
             "anullsrc=r=16000:cl=mono,atrim=duration=1.5[a3];"
             "sine=frequency=440:duration=1.5[a4];"
             "[a0][a1][a2][a3][a4]concat=n=5:v=0:a=1[a]")
    subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=640x360:rate=30:duration=9",
         "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


class CutSpansTest(unittest.TestCase):
    def test_single_span_is_not_cut(self):
        self.assertEqual(render._cut_spans([{"start": 1.0, "end": 9.0}], 1.0, 9.0), [])

    def test_merges_tiny_gaps_and_drops_slivers(self):
        spans = [
            {"start": 0.0, "end": 5.0},
            {"start": 5.01, "end": 8.0},   # шов меньше 0.03 с — склеиваем
            {"start": 8.5, "end": 8.52},   # огрызок 0.02 с — выбрасываем
            {"start": 8.6, "end": 12.0},
        ]
        clean = render._cut_spans(spans, 0.0, 12.0)
        self.assertEqual(clean, [{"start": 0.0, "end": 8.0}, {"start": 8.6, "end": 12.0}])

    def test_rejects_plan_that_eats_most_of_clip(self):
        spans = [{"start": 0.0, "end": 1.0}, {"start": 11.0, "end": 12.0}]
        self.assertEqual(render._cut_spans(spans, 0.0, 12.0), [])


class PauseSpansTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-pause-"))
        self.src = make_paused_source(self.tmp / "paused.mp4")

    def test_pipeline_plan_removes_both_pauses(self):
        spans = pipeline._pause_spans(self.src, 0.0, 9.0)
        self.assertGreaterEqual(len(spans), 3, f"паузы не найдены: {spans}")
        total = sum(s["end"] - s["start"] for s in spans)
        self.assertLess(total, 8.0, "вырезанные паузы должны уменьшить длительность")

    def test_render_cuts_pauses_and_keeps_duration(self):
        spans = pipeline._pause_spans(self.src, 0.0, 9.0)
        out = self.tmp / "cut.mp4"
        result = render.render_clip(self.src, 0.0, 9.0, out, aspect="9:16",
                                    mode="blur", spans=spans)
        info = render.probe(out)
        self.assertAlmostEqual(info["duration"], result["duration"], delta=0.6)
        self.assertLess(info["duration"], 8.5, "клип должен стать короче исходных 9 с")
        self.assertGreater(info["duration"], 4.5, "нельзя выбросить речь вместе с паузами")

    def test_subtitles_follow_cut_timeline(self):
        spans = pipeline._pause_spans(self.src, 0.0, 9.0)
        words = [{"word": "до", "start": 0.5, "end": 1.0},
                 {"word": "в_паузе", "start": 2.5, "end": 3.0},
                 {"word": "после", "start": 4.5, "end": 5.0}]
        moved = analyze.remap_words(words, spans)
        self.assertEqual([w["word"] for w in moved], ["до", "после"])
        out_len = spans[-1]["output"] + (spans[-1]["end"] - spans[-1]["start"])
        ass = subtitles.build_ass(moved, 0.0, out_len, speed="calm")
        self.assertNotIn("в_паузе", ass.upper())


class RemapCropPathTest(unittest.TestCase):
    def test_points_inside_pauses_are_dropped(self):
        path = {"times": [0.0, 1.0, 2.0, 3.0, 4.0],
                "x": [10.0, 20.0, 30.0, 40.0, 50.0],
                "y": [1.0, 2.0, 3.0, 4.0, 5.0],
                "crop_w": 100, "crop_h": 100, "source": [200, 200]}
        spans = [{"start": 0.0, "end": 1.5}, {"start": 3.5, "end": 4.0}]
        out = reframe.remap_crop_path(path, spans, 0.0)
        self.assertIsNotNone(out)
        self.assertEqual(out["times"], [0.0, 1.0, 2.0])
        self.assertEqual(out["x"], [10.0, 20.0, 50.0])
        self.assertAlmostEqual(out["times"][-1], 2.0, places=2)

    def test_too_few_points_falls_back(self):
        path = {"times": [2.0], "x": [10.0], "y": [1.0],
                "crop_w": 100, "crop_h": 100, "source": [200, 200]}
        self.assertIsNone(reframe.remap_crop_path(path, [{"start": 0.0, "end": 1.0}], 0.0))


class RenderOneIntegrationTest(unittest.TestCase):
    """_render_one обязан передать spans в рендер и пересчитать субтитры."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vc-render-one-"))
        self.source = self.tmp / "source.mp4"
        self.source.write_bytes(b"x")  # содержимое не читается: всё замокано

    def test_spans_and_remapped_subtitles_reach_renderer(self):
        curve = [-30.0] * 40
        for i in range(8, 16):
            curve[i] = -55.0  # 2 секунды тишины в окне 0–10 с
        clip = {
            "id": "c1", "idx": 0, "start": 0.0, "end": 10.0,
            "words": [{"word": "раз", "start": 0.5, "end": 0.9},
                      {"word": "пауза", "start": 2.5, "end": 3.0},
                      {"word": "два", "start": 5.0, "end": 5.4}],
            "hook": "", "title": "Тест",
        }
        settings = {"subtitles": True, "mode": "crop", "aspect": "9:16",
                    "cut_pauses": True, "uppercase": False, "show_hook": False,
                    "sub_size": 118, "sub_font": "montserrat",
                    "sub_position": "bottom", "sub_offset": 0, "sub_speed": "calm"}
        captured = {}

        def fake_render(source, start, end, out_path, **kwargs):
            captured.update(kwargs)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"0" * 20_000)
            return {"path": out_path, "duration": 8.0, "width": 1080, "height": 1920}

        with mock.patch.object(analyze, "cached_energy_curve", return_value=(curve, -30.0)), \
             mock.patch.object(render, "render_clip", side_effect=fake_render), \
             mock.patch.object(render, "make_thumb", return_value=None), \
             mock.patch.object(render, "make_preview", return_value=None), \
             mock.patch.object(pipeline.db, "update_clip") as update:
            pipeline._render_one(self.source, self.tmp, clip, settings, None, {})

        spans = captured.get("spans")
        self.assertTrue(spans and len(spans) > 1, f"рендер без spans: {captured}")
        ass_text = Path(captured["ass_path"]).read_text(encoding="utf-8")
        self.assertIn("РАЗ", ass_text.upper())
        self.assertNotIn("ПАУЗА", ass_text.upper())
        update.assert_called_once()


class OptionTest(unittest.TestCase):
    def test_cut_pauses_survives_normalization(self):
        opts = pipeline.normalize_options({"cut_pauses": True})
        self.assertTrue(opts["cut_pauses"])
        self.assertFalse(pipeline.normalize_options({})["cut_pauses"])

    def test_clip_settings_keep_flag(self):
        opts = pipeline.normalize_options({"cut_pauses": True})
        self.assertTrue(pipeline._clip_settings(opts)["cut_pauses"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
