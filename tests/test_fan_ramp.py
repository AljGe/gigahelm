# -*- coding: utf-8 -*-
import unittest

from core.fan_ramp import next_fan_percent


class FanRampTests(unittest.TestCase):
    def test_kick_off_zero_enters_above_spin_floor(self):
        # Soft kick: enter at max(target, 30) instead of a loud fixed 40.
        self.assertEqual(next_fan_percent(0, 15, True, False), (30, False, False))
        self.assertEqual(next_fan_percent(0, 39, True, False), (39, False, False))

    def test_spin_down_starts(self):
        self.assertEqual(next_fan_percent(40, 15, False, False), (35, False, True))

    def test_spin_down_continues_by_five_until_target(self):
        self.assertEqual(next_fan_percent(35, 15, False, True), (30, False, True))
        applied, kick, spinning = 35, False, True
        seen = []
        while applied != 15:
            applied, kick, spinning = next_fan_percent(applied, 15, kick, spinning)
            seen.append(applied)
            self.assertFalse(kick)
            if applied != 15:
                self.assertTrue(spinning)
                self.assertEqual(applied % 5, 0)
        self.assertEqual(seen, [30, 25, 20, 15])
        self.assertFalse(spinning)

    def test_small_drop_holds_until_spin_down_starts(self):
        self.assertEqual(next_fan_percent(40, 36, False, False), (40, False, False))

    def test_rise_jumps_to_target_without_rate_limit(self):
        self.assertEqual(next_fan_percent(20, 80, False, True), (80, False, False))

    def test_rise_is_rate_limited_when_capped(self):
        self.assertEqual(next_fan_percent(20, 60, False, True, 5), (25, False, False))
        self.assertEqual(next_fan_percent(25, 60, False, True, 5), (30, False, False))

    def test_rate_limit_boundary_gap_reaches_instantly(self):
        # Gap equal to the step is not "larger than the step" -> instant.
        self.assertEqual(next_fan_percent(20, 25, False, True, 5), (25, False, False))

    def test_zero_rate_limit_means_instant(self):
        self.assertEqual(next_fan_percent(20, 60, False, True, 0), (60, False, False))

    def test_negative_applied_is_zero(self):
        self.assertEqual(next_fan_percent(-1, 60, True, False), (60, False, False))


if __name__ == "__main__":
    unittest.main()
