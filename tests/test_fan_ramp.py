# -*- coding: utf-8 -*-
import unittest

from core.fan_ramp import next_fan_percent, next_target


class FanRampTests(unittest.TestCase):
    def test_small_start_uses_target_not_a_surge(self):
        self.assertEqual(next_fan_percent(0, 15, True, False), (15, False, False))
        self.assertEqual(next_fan_percent(0, 3, True, False), (3, False, False))

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

    def test_small_drop_converges(self):
        self.assertEqual(next_fan_percent(40, 36, False, False), (36, False, False))
        self.assertEqual(next_fan_percent(60, 55, False, False), (55, False, False))

    def test_zero_target_always_reaches_zero(self):
        applied, kick, spinning = 1, False, False
        for _ in range(4):
            applied, kick, spinning = next_fan_percent(applied, 0, kick, spinning)
        self.assertEqual(applied, 0)

    def test_down_step_is_capped(self):
        self.assertEqual(next_fan_percent(100, 0, False, False), (95, False, True))

    def test_rise_jumps_to_target(self):
        self.assertEqual(next_fan_percent(20, 80, False, True), (80, False, False))

    def test_negative_applied_is_zero(self):
        self.assertEqual(next_fan_percent(-1, 60, True, False), (60, False, False))


class TargetHysteresisTests(unittest.TestCase):
    def test_first_target_passes_through(self):
        self.assertEqual(next_target(None, 37), 37)

    def test_one_point_wobble_is_ignored(self):
        self.assertEqual(next_target(40, 39), 40)
        self.assertEqual(next_target(40, 41), 40)

    def test_two_point_change_applies(self):
        self.assertEqual(next_target(40, 38), 38)
        self.assertEqual(next_target(40, 42), 42)

    def test_zero_always_applies(self):
        self.assertEqual(next_target(1, 0), 0)
        self.assertEqual(next_target(40, 0), 0)

    def test_starting_from_zero_needs_three_points(self):
        self.assertEqual(next_target(0, 1), 0)
        self.assertEqual(next_target(0, 2), 0)
        self.assertEqual(next_target(0, 3), 3)


if __name__ == "__main__":
    unittest.main()
