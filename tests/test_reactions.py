"""Детекторы смеха/аплодисментов и смены сцен.

Синтетика вместо стока: «смех» — шум 800–4000 Гц с огибающей 5 Гц, «аплодисменты» —
широкополосный шум, речь — тон 440 Гц. Так тест не зависит от сети и лицензий.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from viralcut import analyze, config

SR = 16000


def band_noise(rng: np.random.Generator, n: int, low: int, high: int) -> np.ndarray:
    spec = np.fft.rfft(rng.normal(size=n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spec[(freqs < low) | (freqs > high)] = 0
    out = np.fft.irfft(spec, n)
    return out / (np.abs(out).max() + 1e-9)


def write_audio(path: Path, signal: np.ndarray) -> Path:
    """WAV → m4a (AAC), как в реальной дорожке проекта."""
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())
    out = path.with_suffix(".m4a")
    subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-i", str(wav),
                    "-c:a", "aac", "-b:a", "128k", str(out)], check=True)
    return out


def mixed_audio(path: Path) -> Path:
    rng = np.random.default_rng(7)
    t = np.arange(int(6.0 * SR)) / SR
    signal = np.zeros_like(t)
    signal[:2 * SR] = 0.25 * np.sin(2 * np.pi * 440 * t[:2 * SR])   # «речь»
    envelope = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 5 * np.arange(3 * SR) / SR))
    signal[2 * SR:3 * SR] = 0.35 * band_noise(rng, 3 * SR, 800, 4000)[:SR] * envelope[:SR]
    signal[4 * SR:] = 0.30 * band_noise(rng, 2 * SR, 200, 7000)      # аплодисменты
    return write_audio(path, signal)


class ReactionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="vc-react-"))
        cls.media = mixed_audio(cls.tmp / "mix")
        cls.reactions = analyze.audio_reactions(cls.media)

    def test_laughter_found_at_its_own_time(self):
        laughter = self.reactions["laughter"]
        self.assertTrue(laughter, f"смех не найден: {self.reactions}")
        seg = laughter[0]
        self.assertLess(abs(seg["start"] - 2.0), 0.5, f"не та граница: {seg}")
        self.assertGreater(seg["end"] - seg["start"], 0.6)

    def test_applause_found_at_its_own_time(self):
        applause = self.reactions["applause"]
        self.assertTrue(applause, f"аплодисменты не найдены: {self.reactions}")
        self.assertLess(abs(applause[0]["start"] - 4.0), 0.5, f"не та граница: {applause[0]}")

    def test_speech_is_not_a_reaction(self):
        for kind in ("laughter", "applause"):
            for seg in self.reactions[kind]:
                overlap = analyze._overlap_seconds(0.0, 2.0, [seg])
                self.assertLess(overlap, 0.4, f"{kind} ошибочно накрыл речь: {seg}")

    def test_missing_audio_does_not_break(self):
        empty = analyze.audio_reactions(self.tmp / "нет-такого.mp4")
        self.assertEqual(empty, {"laughter": [], "applause": []})


class SceneChangesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="vc-scene-"))
        cls.cut = cls.tmp / "cut.mp4"
        graph = ("color=c=red:s=640x360:r=25:d=2[a];"
                 "color=c=green:s=640x360:r=25:d=2[b];"
                 "color=c=blue:s=640x360:r=25:d=2[c];"
                 "[a][b][c]concat=n=3:v=1:a=0[v]")
        subprocess.run(
            [config.ffmpeg(), "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=size=10x10:rate=25:duration=6",
             "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264",
             "-preset", "veryfast", "-g", "25", "-keyint_min", "25",
             "-sc_threshold", "0", str(cls.cut)],
            check=True,
        )
        cls.smooth = cls.tmp / "smooth.mp4"
        subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc2=size=640x360:rate=25:duration=6",
                        "-c:v", "libx264", "-preset", "veryfast", "-g", "25",
                        str(cls.smooth)], check=True)

    def test_finds_cuts(self):
        cuts = analyze.scene_changes(self.cut)
        self.assertGreaterEqual(len(cuts), 2, f"склейки не найдены: {cuts}")
        self.assertTrue(any(abs(t - 2.0) < 0.4 for t in cuts), f"нет склейки на 2 с: {cuts}")
        self.assertTrue(any(abs(t - 4.0) < 0.4 for t in cuts), f"нет склейки на 4 с: {cuts}")

    def test_smooth_motion_is_not_a_cut(self):
        self.assertEqual(analyze.scene_changes(self.smooth), [],
                         "плавное движение не должно считаться сменой сцены")

    def test_without_opencv_returns_empty(self):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "cv2" or name.startswith("cv2."):
                raise ImportError("нет OpenCV")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=fake_import):
            self.assertEqual(analyze.scene_changes(self.cut), [])


class ScoringTest(unittest.TestCase):
    def _sents(self):
        raw = [{"start": 0.0, "end": 12.0, "text": "Мы запустили продукт за неделю.",
                "words": [{"word": "Мы", "start": 0.0, "end": 0.2},
                          {"word": "запустили", "start": 0.3, "end": 0.8},
                          {"word": "продукт", "start": 0.9, "end": 1.4},
                          {"word": "за", "start": 1.5, "end": 1.6},
                          {"word": "неделю.", "start": 1.7, "end": 2.2}]}]
        return analyze.build_sentences(raw)

    def test_laughter_raises_score(self):
        sents = self._sents()
        base, base_parts = analyze._score_window(sents, 20.0, [], -30.0, 0.25)
        funny, funny_parts = analyze._score_window(
            sents, 20.0, [], -30.0, 0.25,
            reactions={"laughter": [{"start": 0.2, "end": 2.0, "score": 0.9}], "applause": []})
        self.assertGreater(funny, base)
        self.assertGreater(funny_parts["reaction"], 0.0)
        self.assertEqual(base_parts["reaction"], 0.0)

    def test_scene_start_adds_bonus(self):
        sents = self._sents()
        base, _ = analyze._score_window(sents, 20.0, [], -30.0, 0.25)
        scored, parts = analyze._score_window(sents, 20.0, [], -30.0, 0.25,
                                              scenes=[0.0, 6.0])
        self.assertGreater(scored, base)
        self.assertGreater(parts["visual"], 0.0)

    def test_reactions_do_not_break_search(self):
        tokens = "Почему это работает ? Секрет в одном .".split()
        words = [{"word": w, "start": 0.1 + 1.2 * i, "end": 0.1 + 1.2 * i + 0.7}
                 for i, w in enumerate(tokens)]
        raw = [{"start": words[0]["start"], "end": words[-1]["end"],
                "text": " ".join(tokens), "words": words}]
        with mock.patch.object(analyze, "cached_energy_curve", return_value=([], -30.0)), \
             mock.patch.object(analyze, "audio_reactions",
                               return_value={"laughter": [], "applause": []}), \
             mock.patch.object(analyze, "scene_changes", return_value=[]):
            clips = analyze.find_segments(raw, Path("dummy.mp4"), 30.0, count=2,
                                         min_dur=8, max_dur=40, target_dur=18)
        self.assertTrue(clips)


if __name__ == "__main__":
    unittest.main(verbosity=2)
