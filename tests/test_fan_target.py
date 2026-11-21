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
        # Continuous floor: 86 degC sits 1 degC into the 85-90 segment
        # (60 % -> 80 %), i.e. 64 % at 4 points per degC.
        self.assertEqual(select_fan_target(FAN_MODE_AUTO, 0, 5, 60, 86), 64)

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
    """Continuous thermal-guard floor (see guard_floor)."""

    def test_cool_temp_has_no_floor(self):
        self.assertEqual(guard_floor(79.9), 0)
        self.assertEqual(guard_floor(TEMP_READ_ERROR_VALUE), 0)

    def test_anchors_are_inclusive(self):
        self.assertEqual(guard_floor(80.0), 40)
        self.assertEqual(guard_floor(85.0), 60)
        self.assertEqual(guard_floor(90.0), 80)

    def test_interpolates_between_anchors(self):
        # 4 duty points per degC on both segments.
        self.assertEqual(guard_floor(82.5), 50)
        self.assertEqual(guard_floor(83.0), 52)
        self.assertEqual(guard_floor(87.5), 70)
        self.assertEqual(guard_floor(89.0), 76)

    def test_flat_at_top_anchor(self):
        self.assertEqual(guard_floor(91.0), 80)
        self.assertEqual(guard_floor(120.0), 80)

    def test_never_below_the_old_stepwise_tier(self):
        # The 2026-11-19 night run measured the stepwise tiers pumping
        # 60<->80 % at the ~89-91 degC ceiling. Continuous floors may only
        # ever add cooling between the old tiers, never remove it.
        def old_tier(t: float) -> int:
            return 40 if t < 85 else 60 if t < 90 else 80

        for tenth in range(800, 1001):
            temp = tenth / 10
            self.assertGreaterEqual(guard_floor(temp), old_tier(temp),
                                    msg=f"at {temp} degC")

    def test_monotonic_in_temperature(self):
        previous = 0
        for tenth in range(700, 1001):
            value = guard_floor(tenth / 10)
            self.assertGreaterEqual(value, previous)
            previous = value


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
