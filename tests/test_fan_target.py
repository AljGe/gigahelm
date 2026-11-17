# -*- coding: utf-8 -*-
import unittest

from core.fan_target import guard_floor, select_fan_target, select_per_fan_targets
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


class SelectPerFanTargetsTests(unittest.TestCase):
    """Split control: each channel follows its own die; no shared max."""

    def test_sleeping_gpu_channel_is_zero(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 30, 50, 65.0, TEMP_READ_ERROR_VALUE,
            gpu_asleep=True)
        self.assertEqual(cpu, 30)
        self.assertEqual(gpu, 0)

    def test_hot_cpu_does_not_lift_gpu_channel(self):
        # CPU at 91 °C demands the 80 % guard floor; a cool GPU stays low.
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 5, 91.0, 45.0)
        self.assertEqual(cpu, 80)
        self.assertEqual(gpu, 5)

    def test_hot_gpu_does_not_lift_cpu_channel(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 5, 45.0, 91.0)
        self.assertEqual(cpu, 5)
        self.assertEqual(gpu, 80)

    def test_explicit_floors_apply_per_channel(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 10, 10, 60.0, 60.0,
            cpu_floor=45, gpu_floor=None)
        self.assertEqual(cpu, 45)
        self.assertEqual(gpu, 10)

    def test_cpu_sensor_error_forces_eighty_on_cpu_only(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 5, TEMP_READ_ERROR_VALUE, 45.0)
        self.assertEqual(cpu, 80)
        self.assertEqual(gpu, 5)

    def test_gpu_sensor_error_floors_gpu_at_forty(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 5, 60.0, TEMP_READ_ERROR_VALUE)
        self.assertGreaterEqual(gpu, 40)
        self.assertEqual(cpu, 5)

    def test_gpu_sensor_error_with_hot_cpu_curve_still_floors_gpu(self):
        # GPU base above 40 stays; the floor lifts a low base.
        _, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 55, 60.0, TEMP_READ_ERROR_VALUE)
        self.assertEqual(gpu, 55)
        _, gpu = select_per_fan_targets(
            FAN_MODE_AUTO, 0, 5, 5, 60.0, TEMP_READ_ERROR_VALUE)
        self.assertEqual(gpu, 40)

    def test_custom_mode_drives_both_channels(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_CUSTOM, 30, 0, 0, 60.0, 45.0)
        self.assertEqual(cpu, 30)
        self.assertEqual(gpu, 30)

    def test_custom_mode_still_gets_guards(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_CUSTOM, 10, 0, 0, 85.0, 90.0)
        self.assertEqual(cpu, 60)
        self.assertEqual(gpu, 80)

    def test_output_is_clipped_per_channel(self):
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_CUSTOM, 150, 0, 0, 60.0, 60.0)
        self.assertEqual(cpu, 100)
        self.assertEqual(gpu, 100)
        cpu, gpu = select_per_fan_targets(
            FAN_MODE_CUSTOM, -10, 0, 0, 60.0, 60.0)
        self.assertEqual(cpu, 0)
        self.assertEqual(gpu, 0)


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
