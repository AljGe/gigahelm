# -*- coding: utf-8 -*-
"""numpy-free spectral scorer for the whistle-sweep recordings.

Synthesized-fixture cases pin the three contract metrics: a pure tone
scores high tonality with a correct peak, two tones ~50 Hz apart resolve as
the twin-fan beat split, and seeded noise stays low. These guard the ranking
the floor sweep prints - a broken FFT or floor estimate would silently
recommend the wrong park duty."""
import array
import math
import random
import sys
import tempfile
import unittest
import wave
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1] / "tools" / "bench"
sys.path.insert(0, str(BENCH_DIR))

import acoustic_analysis as aa  # noqa: E402

RATE = 16000


def _write_wav(path, samples):
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        ints = array.array("h", (max(-32767, min(32767, int(v * 32767.0)))
                                 for v in samples))
        wav.writeframes(ints.tobytes())


def _signal(freqs, seconds, amp=0.3, noise=0.004, seed=7):
    """Tones at freqs plus a small seeded floor (real recordings have one;
    an exact-zero floor would explode the prominence denominator)."""
    rnd = random.Random(seed)
    out = []
    for i in range(int(seconds * RATE)):
        v = amp * sum(math.sin(2.0 * math.pi * f * i / RATE) for f in freqs)
        out.append(v + rnd.uniform(-noise, noise))
    return out


class WavRoundTripTests(unittest.TestCase):
    def test_read_wav_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "t.wav")
            _write_wav(path, _signal((1000.0,), 0.5))
            samples, rate = aa.read_wav_mono(path)
            self.assertEqual(rate, RATE)
            self.assertEqual(len(samples), int(0.5 * RATE))


class TonalMetricTests(unittest.TestCase):
    def test_pure_tone_scores_high_with_correct_peak(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tone.wav")
            _write_wav(path, _signal((3900.0,), 2.0))
            m = aa.analyze_recording(path)
            self.assertGreater(m["tonality_db"], 20.0)
            self.assertLess(m["tonality_db"], 120.0)
            self.assertAlmostEqual(m["peak_hz"], 3900.0, delta=10.0)
            self.assertIsNone(m["split_hz"])

    def test_twin_tones_resolve_as_beat_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "beat.wav")
            _write_wav(path, _signal((3900.0, 3950.0), 2.0))
            m = aa.analyze_recording(path)
            self.assertIsNotNone(m["split_hz"])
            self.assertAlmostEqual(m["split_hz"], 50.0, delta=12.0)

    def test_far_apart_tones_are_not_reported_as_beat(self):
        # 500 Hz apart: resolved, benign - twin_split must return None.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "far.wav")
            _write_wav(path, _signal((3900.0, 4400.0), 2.0))
            m = aa.analyze_recording(path)
            self.assertIsNone(m["split_hz"])

    def test_noise_stays_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "noise.wav")
            _write_wav(path, _signal((), 2.0, amp=0.0, noise=0.2))
            m = aa.analyze_recording(path)
            self.assertLess(m["tonality_db"], 12.0)

    def test_louder_tone_raises_band_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = str(Path(tmp) / "q.wav")
            loud = str(Path(tmp) / "l.wav")
            _write_wav(quiet, _signal((3900.0,), 2.0, amp=0.1))
            _write_wav(loud, _signal((3900.0,), 2.0, amp=0.3))
            mq = aa.analyze_recording(quiet)
            ml = aa.analyze_recording(loud)
            self.assertGreater(ml["band_db"], mq["band_db"] + 3.0)


class RankingTests(unittest.TestCase):
    def test_rank_steps_by_tonality_then_band(self):
        steps = {
            "27": {"tonality_db": 18.0, "band_db": 50.0},
            "25": {"tonality_db": 26.0, "band_db": 48.0},
            "29": {"tonality_db": 18.0, "band_db": 47.0},
        }
        ranked = aa.rank_steps(steps)
        self.assertEqual([label for label, _ in ranked], ["29", "27", "25"])


class GuardTests(unittest.TestCase):
    def test_non_pow2_block_rejected(self):
        with self.assertRaises(ValueError):
            aa.psd_blocks([0.0] * 5000, RATE, block=1000)

    def test_short_input_rejected(self):
        with self.assertRaises(ValueError):
            aa.psd_blocks([0.0] * 100, RATE, block=4096)

    def test_short_recording_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "tiny.wav")
            _write_wav(path, _signal((3900.0,), 0.1))
            with self.assertRaises(ValueError):
                aa.analyze_recording(path)


if __name__ == "__main__":
    unittest.main()
