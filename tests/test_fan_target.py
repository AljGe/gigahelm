# -*- coding: utf-8 -*-
import unittest

from core.fan_target import select_fan_target
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


if __name__ == "__main__":
    unittest.main()
