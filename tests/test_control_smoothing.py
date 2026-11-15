# -*- coding: utf-8 -*-
"""Temperature conditioning: median spike filter, EMA, dT/dt predictive boost."""
import os
import sys
import unittest
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.control_smoothing import smooth_and_predict


def feed(samples, interval_s=1.5):
    raw_hist = deque(maxlen=4)
    smooth_hist = deque(maxlen=7)
    out = [smooth_and_predict(raw_hist, smooth_hist, s, interval_s) for s in samples]
    return out, raw_hist, smooth_hist


class ControlSmoothingTests(unittest.TestCase):
    def test_first_sample_passes_through(self):
        out, _, _ = feed([55.0])
        self.assertEqual(out, [55.0])

    def test_median_kills_single_sample_spike(self):
        out, _, _ = feed([60.0, 60.0, 60.0, 60.0, 90.0])
        # median(60, 60, 90) = 60, EMA of a constant stays constant
        self.assertAlmostEqual(out[-1], 60.0, places=6)

    def test_ema_converges_without_overshoot(self):
        out, _, smooth_hist = feed([60.0, 70.0, 70.0, 70.0, 70.0, 70.0])
        # The EMA itself (smooth_hist) rises monotonically toward the new level;
        # the returned value is smooth + dT/dt boost, so it may wobble but never
        # exceeds smooth + 12 and never undershoots the EMA.
        smooth = list(smooth_hist)
        self.assertTrue(all(a <= b for a, b in zip(smooth, smooth[1:])), "EMA must rise monotonically")
        self.assertLessEqual(smooth[-1], 70.0)
        self.assertGreater(smooth[-1], 60.0)
        self.assertGreaterEqual(out[-1], smooth[-1])
        self.assertLessEqual(out[-1] - smooth[-1], 12.0)

    def test_fast_rise_arms_predictive_boost(self):
        samples = [55.0, 57.0, 59.0, 61.0, 63.0, 65.0]
        out, _, smooth_hist = feed(samples)
        # By sample 6 the smoothed slope exceeds 0.7 C/s -> returned value = smooth + boost.
        self.assertGreater(out[-1], smooth_hist[-1])
        self.assertLessEqual(out[-1] - smooth_hist[-1], 12.0)

    def test_no_boost_when_flat(self):
        out, _, smooth_hist = feed([60.0] * 7)
        self.assertAlmostEqual(out[-1], smooth_hist[-1], places=6)

    def test_no_boost_when_already_hot(self):
        # Above the 85 C arm temperature the curve is steep enough on its own.
        out, _, smooth_hist = feed([84.0, 86.0, 88.0, 90.0])
        self.assertAlmostEqual(out[-1], smooth_hist[-1], places=6)

    def test_raw_history_respects_maxlen(self):
        out, raw_hist, _ = feed([50.0] * 10)
        self.assertEqual(len(raw_hist), 4)
        self.assertEqual(list(raw_hist), [50.0, 50.0, 50.0, 50.0])


if __name__ == "__main__":
    unittest.main()
