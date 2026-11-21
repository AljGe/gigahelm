# -*- coding: utf-8 -*-
"""Sanitization of untrusted profile data loaded from control_config.json.

A hand-edited or corrupted config must never put the app into a non-
converging state: unknown fan mode (zombie control loop), unmapped charge
policy (permanent charge-watch rewrite loop), or an empty/invalid fan curve
(silent 0 % fans). Every behavioral key is validated in ProfileState.from_dict;
anything invalid falls back to DEFAULT_PROFILE_SETTINGS.

Bare ProfileState() construction is safe without QApplication (same precedent
as tests/test_config_recovery.py).
"""
import unittest

from config.settings import DEFAULT_PROFILE_SETTINGS
from core.state import ProfileState


def _defaults(**overrides):
    data = {key: value for key, value in DEFAULT_PROFILE_SETTINGS.items()}
    data.update(overrides)
    return data


class ProfileValidationTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileState()

    def _load(self, overrides):
        self.profile.from_dict(_defaults(**overrides))

    # --- fan_mode -------------------------------------------------------
    def test_unknown_fan_mode_falls_back_to_default(self):
        self._load({"fan_mode": "turbo"})
        self.assertEqual(self.profile.get_fan_mode(), "auto")

    def test_valid_fan_mode_passes_through(self):
        self._load({"fan_mode": "custom"})
        self.assertEqual(self.profile.get_fan_mode(), "custom")

    # --- battery_charge_policy -------------------------------------------
    def test_unknown_charge_policy_falls_back_to_default(self):
        self._load({"battery_charge_policy": "eco"})
        self.assertEqual(self.profile.get_battery_charge_policy(), "bios")

    def test_valid_charge_policy_passes_through(self):
        self._load({"battery_charge_policy": "custom"})
        self.assertEqual(self.profile.get_battery_charge_policy(), "custom")

    # --- battery_charge_threshold ----------------------------------------
    def test_charge_threshold_above_max_clips(self):
        self._load({"battery_charge_threshold": 120})
        self.assertEqual(self.profile.get_battery_charge_threshold(), 100)

    def test_charge_threshold_below_min_clips(self):
        self._load({"battery_charge_threshold": 55})
        self.assertEqual(self.profile.get_battery_charge_threshold(), 60)

    def test_charge_threshold_string_rejected(self):
        self._load({"battery_charge_threshold": "80"})
        self.assertEqual(self.profile.get_battery_charge_threshold(), 80)

    # --- custom_fan_speed -------------------------------------------------
    def test_custom_fan_speed_above_max_clips(self):
        self._load({"custom_fan_speed": 130})
        self.assertEqual(self.profile.get_custom_fan_speed(), 100)

    def test_custom_fan_speed_below_min_clips(self):
        self._load({"custom_fan_speed": -5})
        self.assertEqual(self.profile.get_custom_fan_speed(), 0)

    # --- fan curves --------------------------------------------------------
    def test_garbage_cpu_table_falls_back_to_default(self):
        self._load({"cpu_fan_table": [["a", "b"], [80]]})
        self.assertEqual(
            [list(p) for p in self.profile.get_cpu_fan_table()],
            [list(p) for p in DEFAULT_PROFILE_SETTINGS["cpu_fan_table"]])

    def test_gpu_table_speed_clipped_and_kept(self):
        self._load({"gpu_fan_table": [[80, 40], [90, 110]]})
        self.assertEqual(self.profile.get_gpu_fan_table(), [[80, 40], [90, 100]])

    # --- gpu_power_feedforward ---------------------------------------------
    def test_feedforward_string_rejected(self):
        self._load({"gpu_power_feedforward": "false"})
        self.assertIs(self.profile.get_value("gpu_power_feedforward"), True)

    def test_feedforward_bool_passes_through(self):
        self._load({"gpu_power_feedforward": False})
        self.assertIs(self.profile.get_value("gpu_power_feedforward"), False)

    # --- anti_beat_detune / fan_load_assist --------------------------------
    def test_detune_string_rejected(self):
        self._load({"anti_beat_detune": "false"})
        self.assertIs(self.profile.get_value("anti_beat_detune"), True)

    def test_detune_bool_passes_through(self):
        self._load({"anti_beat_detune": False})
        self.assertIs(self.profile.get_value("anti_beat_detune"), False)

    def test_assist_string_rejected(self):
        self._load({"fan_load_assist": "false"})
        self.assertIs(self.profile.get_value("fan_load_assist"), True)

    def test_assist_bool_passes_through(self):
        self._load({"fan_load_assist": False})
        self.assertIs(self.profile.get_value("fan_load_assist"), False)

    def test_twin_wake_string_rejected(self):
        self._load({"fan_twin_wake": "false"})
        self.assertIs(self.profile.get_value("fan_twin_wake"), True)

    def test_twin_wake_bool_passes_through(self):
        self._load({"fan_twin_wake": False})
        self.assertIs(self.profile.get_value("fan_twin_wake"), False)

    # --- valid data round-trips unchanged -----------------------------------
    def test_valid_full_dict_round_trips(self):
        self.profile.from_dict(_defaults(
            fan_mode="custom",
            battery_charge_policy="custom",
            battery_charge_threshold=85,
            cpu_fan_table=[[60, 0], [80, 60]],
        ))
        self.assertEqual(self.profile.get_fan_mode(), "custom")
        self.assertEqual(self.profile.get_battery_charge_policy(), "custom")
        self.assertEqual(self.profile.get_battery_charge_threshold(), 85)
        self.assertEqual(self.profile.get_cpu_fan_table(), [[60, 0], [80, 60]])


if __name__ == "__main__":
    unittest.main()
