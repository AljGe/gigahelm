# -*- coding: utf-8 -*-
"""Settings surface: generic profile-value writes persist, and the fan card's
feed-forward checkbox reflects and drives the active profile."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.profile_manager import ProfileManager  # noqa: E402
from core.settings_manager import SettingsManager  # noqa: E402
from gui.FanControlPanel import FanControlPanel  # noqa: E402


def _fresh_state(tmp):
    paths = PathManager(tmp.name, sys.executable, __file__)
    state = AppState(paths)
    return paths, state, ProfileManager(state)


class SettingsSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gigahelm-settings-")
        self.addCleanup(self.tmp.cleanup)
        self.paths, self.state, self.manager = _fresh_state(self.tmp)
        self.manager.load_config()
        self.settings = SettingsManager(self.state, self.manager)

    def _reload_from_disk(self):
        # save_config() is debounced+background now; flush deterministically.
        self.manager.save_config_sync()
        _, state, manager = _fresh_state(self.tmp)
        manager.load_config()
        return state

    def test_set_profile_value_persists_feedforward(self):
        active = self.state.get_active_profile()
        self.assertIsNotNone(active)
        self.assertTrue(active.get_value("gpu_power_feedforward", True))
        self.manager.set_profile_value("gpu_power_feedforward", False)
        reloaded = self._reload_from_disk().get_active_profile()
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.get_value("gpu_power_feedforward"), False)

    def test_gui_interval_persists(self):
        # Global app setting since the migration; no longer a profile value.
        self.settings.set_gui_update_interval_ms(500)
        reloaded = self._reload_from_disk()
        self.assertEqual(reloaded.get_gui_update_interval_ms(), 500)

    def test_legacy_profile_interval_migrates_to_global_setting(self):
        # A pre-migration config stores the interval only inside a profile.
        self.manager.save_config_sync()
        with open(self.paths.control_config, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        config.pop("gui_update_interval_ms", None)
        legacy_profile = config["active_profile_name"]
        config["profiles"][legacy_profile]["gui_update_interval_ms"] = 250
        with open(self.paths.control_config, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        # Fresh process: load_config must migrate the profile value (no
        # flush here - that would rewrite a modern config over the legacy one).
        paths = PathManager(self.tmp.name, sys.executable, __file__)
        state = AppState(paths)
        fresh_manager = ProfileManager(state)
        fresh_manager.load_config()
        self.assertEqual(state.get_gui_update_interval_ms(), 250)
        # The next save hoists the migrated value to the top-level key.
        fresh_manager.save_config_sync()
        with open(self.paths.control_config, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["gui_update_interval_ms"], 250)

    def test_invalid_interval_values_are_ignored(self):
        self.settings.set_gui_update_interval_ms(333)
        self.assertEqual(self.state.get_gui_update_interval_ms(), 1000)
        self.settings.set_gui_update_interval_ms(1000)  # no change, no save

    def test_notifications_flag_defaults_on_and_persists_off(self):
        self.assertTrue(self.state.get_notifications_enabled())
        self.settings.set_notifications_enabled(False)
        self.assertFalse(self.state.get_notifications_enabled())
        self.assertFalse(self._reload_from_disk().get_notifications_enabled())
        # setting the same value again must not rewrite the config
        self.settings.set_notifications_enabled(False)


class FeedforwardCheckboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gigahelm-ff-")
        self.addCleanup(self.tmp.cleanup)
        self.paths, self.state, self.manager = _fresh_state(self.tmp)
        self.manager.load_config()
        self.panel = FanControlPanel(self.state, self.manager)

    def test_checkbox_reflects_and_writes_profile(self):
        self.assertTrue(self.panel.ff_checkbox.isChecked())
        self.panel.ff_checkbox.setChecked(False)
        active = self.state.get_active_profile()
        self.assertEqual(active.get_value("gpu_power_feedforward"), False)
        self.manager.save_config_sync()
        with open(self.paths.control_config, "r", encoding="utf-8") as handle:
            self.assertIn("gpu_power_feedforward", handle.read())

    def test_checkbox_refreshes_on_profile_activation(self):
        second = self.state.get_active_profile().__class__()
        self.state.add_profile("Second", second)
        # give the second profile a stored off value
        self.manager.activate_profile("Second")
        self.manager.set_profile_value("gpu_power_feedforward", False)
        # back to the first profile: checkbox shows its on value
        first_name = self.state.get_profile_names()[0]
        self.manager.activate_profile(first_name)
        self.assertTrue(self.panel.ff_checkbox.isChecked())
        # to the second: persisted off value must win over the stale UI state
        self.manager.activate_profile("Second")
        self.assertFalse(self.panel.ff_checkbox.isChecked())

    def test_checkbox_disabled_outside_auto(self):
        self.panel._apply_mode("bios")
        self.assertFalse(self.panel.ff_checkbox.isEnabled())
        self.panel._apply_mode("auto")
        self.assertTrue(self.panel.ff_checkbox.isEnabled())


if __name__ == "__main__":
    unittest.main()
