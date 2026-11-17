# -*- coding: utf-8 -*-
import unittest

from core.fan_target import guard_floor, select_fan_target
from config.settings import (
    FAN_MODE_AUTO, FAN_MODE_CUSTOM, TEMP_READ_ERROR_VALUE,
)


class FanTargetTests(unittest.TestCase):
    def test_auto_uses_curve_target(self):
        self.assertEqual(select_fan_target(FAN_MODE_AUTO, 0, 35, 65, 60), 35)

    def test_custom_uses_fixed_speed(self):
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, 30, 90, 65, 60), 30)

    def test_custom_still_gets_thermal_guards(self):
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, 10, 0, 80, 60), 40)
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, 10, 0, 85, 60), 60)
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, 10, 0, 90, 60), 80)

    def test_gpu_guard_raises_shared_duty(self):
        self.assertEqual(select_fan_target(FAN_MODE_AUTO, 0, 5, 60, 86), 60)

    def test_both_sensors_failed_forces_eighty(self):
        self.assertEqual(
            select_fan_target(
                FAN_MODE_AUTO, 0, 0,
                TEMP_READ_ERROR_VALUE, TEMP_READ_ERROR_VALUE,
            ),
            80,
        )

    def test_one_sensor_failed_floors_at_forty(self):
        self.assertEqual(
            select_fan_target(FAN_MODE_AUTO, 0, 5, TEMP_READ_ERROR_VALUE, 60),
            40,
        )

    def test_sleeping_gpu_is_not_a_failure(self):
        self.assertEqual(
            select_fan_target(FAN_MODE_AUTO, 0, 15, 60, TEMP_READ_ERROR_VALUE, gpu_asleep=True),
            15,
        )

    def test_output_is_clamped(self):
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, 150, 0, 60, 60), 100)
        self.assertEqual(select_fan_target(FAN_MODE_CUSTOM, -10, 0, 60, 60), 0)


class GuardFloorTests(unittest.TestCase):
    """Engage/release hysteresis for the thermal guards (see guard_floor)."""

    def test_cool_temp_has_no_floor(self):
        self.assertEqual(guard_floor(79.9), 0)
        self.assertEqual(guard_floor(TEMP_READ_ERROR_VALUE), 0)

    def test_engages_at_threshold(self):
        self.assertEqual(guard_floor(80.0), 40)
        self.assertEqual(guard_floor(85.0), 60)
        self.assertEqual(guard_floor(90.0), 80)

    def test_engaged_floor_holds_inside_release_band(self):
        # 78.5 degC is below every threshold, but the 40 % floor engaged at
        # 80 degC must hold until 77 degC.
        self.assertEqual(guard_floor(78.5, engaged_floor=40), 40)
        self.assertEqual(guard_floor(77.0, engaged_floor=40), 40)
        self.assertEqual(guard_floor(76.9, engaged_floor=40), 0)

    def test_higher_engagement_releases_at_its_own_threshold(self):
        # Release band is [threshold - 3, threshold): 82.4 degC still holds
        # the engaged 60 % floor over the freshly engaged 40 %; below 82 the
        # engagement releases and 40 % takes over.
        self.assertEqual(guard_floor(82.5, engaged_floor=60), 60)
        self.assertEqual(guard_floor(82.4, engaged_floor=60), 60)
        self.assertEqual(guard_floor(81.9, engaged_floor=60), 40)
        self.assertEqual(guard_floor(87.5, engaged_floor=80), 80)
        self.assertEqual(guard_floor(87.4, engaged_floor=80), 80)
        self.assertEqual(guard_floor(86.9, engaged_floor=80), 60)

    def test_reengagement_while_engaged_takes_the_higher_floor(self):
        self.assertEqual(guard_floor(91.0, engaged_floor=40), 80)


class FloorAwareSelectionTests(unittest.TestCase):
    def test_stateless_path_matches_legacy_guards(self):
        self.assertEqual(
            select_fan_target(FAN_MODE_CUSTOM, 10, 0, 79, 60), 10)
        self.assertEqual(
            select_fan_target(FAN_MODE_CUSTOM, 10, 0, 80, 60), 40)

    def test_explicit_floors_are_applied(self):
        self.assertEqual(
            select_fan_target(FAN_MODE_AUTO, 0, 20, 78.5, 60, cpu_floor=40), 40)
        self.assertEqual(
            select_fan_target(FAN_MODE_AUTO, 0, 20, 60, 79.5, gpu_floor=40), 40)

    def test_floors_do_not_override_fault_targets(self):
        # A dead CPU sensor keeps the FAULT_TARGET_ONE floor regardless.
        self.assertEqual(
            select_fan_target(
                FAN_MODE_AUTO, 0, 5, TEMP_READ_ERROR_VALUE, 60, cpu_floor=0),
            40,
        )


if __name__ == "__main__":
    unittest.main()
