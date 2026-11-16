# -*- coding: utf-8 -*-
"""Factory profile set: a config with no profiles regenerates the tuned
defaults, so an accidental reset loses nothing a restart can't restore."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.profile_manager import ProfileManager  # noqa: E402
from config.settings import DEFAULT_PROFILES, AC_CPU_FAN_TABLE, AC_GPU_FAN_TABLE, BATT_CPU_FAN_TABLE


class DefaultProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gigahelm-defprofiles-")
        self.addCleanup(self.tmp.cleanup)
        self.paths = PathManager(self.tmp.name, sys.executable, __file__)
        self.state = AppState(self.paths)
        self.manager = ProfileManager(self.state)

    def test_fresh_config_regenerates_tuned_profiles(self):
        self.manager.load_config()
        names = self.state.get_profile_names()
        self.assertIn("Tuned AC", names)
        self.assertIn("Tuned Battery", names)
        ac = self.state.get_profile_names() and self._profile("Tuned AC")
        self.assertEqual(ac.get_value("cpu_fan_table"), AC_CPU_FAN_TABLE)
        self.assertEqual(ac.get_value("battery_charge_policy"), "bios")
        batt = self._profile("Tuned Battery")
        self.assertEqual(batt.get_value("cpu_fan_table"), BATT_CPU_FAN_TABLE)
        self.assertEqual(batt.get_value("battery_charge_policy"), "custom")
        self.assertEqual(batt.get_value("battery_charge_threshold"), 80)

    def _profile(self, name):
        self.state.set_active_profile_name(name)
        return self.state.get_active_profile()

    def test_tuned_tables_survive_save_reload(self):
        self.manager.load_config()
        state2 = AppState(self.paths)
        manager2 = ProfileManager(state2)
        manager2.load_config()
        state2.set_active_profile_name("Tuned AC")
        self.assertEqual(
            state2.get_active_profile().get_value("cpu_fan_table"),
            AC_CPU_FAN_TABLE)

    def test_default_profile_tables_are_independent(self):
        # two profiles overriding the same module-level table must not alias
        self.assertIsNot(
            DEFAULT_PROFILES["Tuned AC"]["gpu_fan_table"],
            DEFAULT_PROFILES["Tuned Battery"]["gpu_fan_table"])
        self.assertIsNot(
            DEFAULT_PROFILES["Tuned AC"]["cpu_fan_table"],
            DEFAULT_PROFILES["Tuned Battery"]["cpu_fan_table"])
        self.assertIsNot(
            DEFAULT_PROFILES["Tuned AC"]["gpu_fan_table"],
            AC_GPU_FAN_TABLE)


if __name__ == "__main__":
    unittest.main()
