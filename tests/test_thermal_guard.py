# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fan_target import THERMAL_GUARDS  # noqa: E402
from core.thermal_guard import (  # noqa: E402
    DUTY_RAW_MAX, force_duty_percent,
)


class ForceDutyPercentTests(unittest.TestCase):
    """The independent guard must mirror select_fan_target's guard floors."""

    def test_cool_temp_never_forces(self):
        self.assertIsNone(force_duty_percent(75, 0))
        self.assertIsNone(force_duty_percent(50, 0))
        self.assertIsNone(force_duty_percent(None, 0))
        self.assertIsNone(force_duty_percent(-1, 0))  # unreadable temp

    def test_floor_boundary_is_inclusive(self):
        self.assertEqual(force_duty_percent(80.0, 0), 40)
        self.assertEqual(force_duty_percent(85.0, 0), 60)
        self.assertEqual(force_duty_percent(90.0, 0), 80)

    def test_hot_and_below_floor_forces(self):
        self.assertEqual(force_duty_percent(91, 0), 80)      # experiment failure case
        self.assertEqual(force_duty_percent(91, 57), 80)     # EC min-spin floor
        self.assertEqual(force_duty_percent(82, 57), 40)
        self.assertEqual(force_duty_percent(86, 114), 60)

    def test_hot_and_at_floor_spares(self):
        self.assertIsNone(force_duty_percent(91, 182))       # 80 % already applied
        self.assertIsNone(force_duty_percent(83, 100))       # 44 % >= floor 40 %
        self.assertIsNone(force_duty_percent(96, 227))       # 100 %

    def test_unreadable_duty_forces_floor_while_hot(self):
        self.assertEqual(force_duty_percent(91, None), 80)
        self.assertEqual(force_duty_percent(91, -1), 80)

    def test_thresholds_match_fan_target(self):
        self.assertEqual(THERMAL_GUARDS, [(80.0, 40), (85.0, 60), (90.0, 80)])
        self.assertEqual(DUTY_RAW_MAX, 227.0)

    def test_raw_conversion_matches_percent_to_raw(self):
        from core.fan_duty import percent_to_raw
        # one degree below each floor's raw value must force that floor
        for temp, percent in ((82, 40), (86, 60), (95, 80)):
            self.assertEqual(force_duty_percent(temp, percent_to_raw(percent) - 1),
                             percent)


if __name__ == "__main__":
    unittest.main()
