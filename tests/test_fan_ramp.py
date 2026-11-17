# -*- coding: utf-8 -*-
import unittest

from core.fan_ramp import (
    DOWN_STEP_PERCENT, START_FROM_ZERO_PERCENT, UP_STEP_PERCENT,
    ZERO_RELEASE_TICKS, next_fan_percent, next_target,
)


class FanRampTests(unittest.TestCase):
    def test_small_start_uses_target_not_a_surge(self):
        self.assertEqual(next_fan_percent(0, 15), 15)
        self.assertEqual(next_fan_percent(0, 3), 3)

    def test_spin_down_starts(self):
        self.assertEqual(next_fan_percent(40, 15), 40 - DOWN_STEP_PERCENT)

    def test_spin_down_continues_by_five_until_target(self):
        applied = 35
        seen = []
        while applied != 15:
            applied = next_fan_percent(applied, 15)
            seen.append(applied)
            if applied != 15:
                self.assertEqual(applied % 5, 0)
        self.assertEqual(seen, [30, 25, 20, 15])

    def test_small_drop_converges(self):
        self.assertEqual(next_fan_percent(40, 36), 36)
        self.assertEqual(next_fan_percent(60, 55), 55)

    def test_zero_target_always_reaches_zero(self):
        applied = 1
        for _ in range(4):
            applied = next_fan_percent(applied, 0)
        self.assertEqual(applied, 0)

    def test_down_step_is_capped(self):
        self.assertEqual(next_fan_percent(100, 0), 100 - DOWN_STEP_PERCENT)

    def test_rise_jumps_to_target(self):
        self.assertEqual(next_fan_percent(20, 80), 80)

    def test_negative_applied_is_zero(self):
        self.assertEqual(next_fan_percent(-1, 60), 60)


class UpGlideTests(unittest.TestCase):
    """Curve-driven rises glide instead of jumping; a safety floor above the
    applied duty bypasses the glide so thermal headroom is never queued."""

    def test_default_call_jumps(self):
        # Custom mode / fresh sessions keep the immediate jump.
        self.assertEqual(next_fan_percent(20, 80), 80)

    def test_curve_rise_glide_steps(self):
        self.assertEqual(
            next_fan_percent(30, 44, 0, UP_STEP_PERCENT), 30 + UP_STEP_PERCENT)
        self.assertEqual(next_fan_percent(40, 44, 0, UP_STEP_PERCENT), 44)

    def test_glide_converges(self):
        applied = 25
        steps = 0
        while applied < 60:
            applied = next_fan_percent(applied, 60, 0, UP_STEP_PERCENT)
            steps += 1
            self.assertLess(steps, 10)
        self.assertEqual(applied, 60)

    def test_safety_floor_bypasses_glide(self):
        self.assertEqual(next_fan_percent(30, 44, 50, UP_STEP_PERCENT), 44)
        self.assertEqual(next_fan_percent(30, 35, 40, UP_STEP_PERCENT), 35)

    def test_floor_at_or_below_applied_still_glides(self):
        self.assertEqual(
            next_fan_percent(30, 44, 30, UP_STEP_PERCENT), 30 + UP_STEP_PERCENT)

    def test_downs_untouched_by_glide_arguments(self):
        self.assertEqual(
            next_fan_percent(40, 15, 0, UP_STEP_PERCENT),
            40 - DOWN_STEP_PERCENT)


class TargetHysteresisTests(unittest.TestCase):
    def test_first_target_passes_through(self):
        self.assertEqual(next_target(None, 37), (37, 0))

    def test_one_point_wobble_is_ignored(self):
        self.assertEqual(next_target(40, 39), (40, 0))
        self.assertEqual(next_target(40, 41), (40, 0))

    def test_two_point_change_applies(self):
        self.assertEqual(next_target(40, 38), (38, 0))
        self.assertEqual(next_target(40, 42), (42, 0))

    def test_starting_from_zero_needs_three_points(self):
        self.assertEqual(next_target(0, 1), (0, 0))
        self.assertEqual(next_target(0, 2), (0, 0))
        self.assertEqual(next_target(0, START_FROM_ZERO_PERCENT), (3, 0))


class ZeroReleaseDebounceTests(unittest.TestCase):
    """Integer EC temps flap ±1 degC at a curve knee; releasing to fan-off on
    the first zero raw target would cycle the fans between full stop and the
    25 % minimum-spin duty (audible start surge every flap)."""

    def test_running_fan_holds_through_short_zero_runs(self):
        target, streak = next_target(25, 0)
        self.assertEqual((target, streak), (25, 1))
        target, streak = next_target(25, 0, zero_streak=streak)
        self.assertEqual((target, streak), (25, 2))

    def test_sustained_zero_releases_after_three_ticks(self):
        target, streak = next_target(25, 0, zero_streak=ZERO_RELEASE_TICKS - 1)
        self.assertEqual((target, streak), (0, ZERO_RELEASE_TICKS))

    def test_positive_target_resets_the_streak(self):
        self.assertEqual(next_target(25, 12, zero_streak=2), (12, 0))

    def test_zero_while_already_off_is_sticky(self):
        self.assertEqual(next_target(0, 0, zero_streak=7), (0, 8))

    def test_fresh_target_resets_the_streak(self):
        self.assertEqual(next_target(None, 0, zero_streak=2), (0, 0))


if __name__ == "__main__":
    unittest.main()
