# -*- coding: utf-8 -*-
import glob
import json
import os
import sys
import tempfile
import unittest

from core.path_manager import PathManager
from core.profile_manager import ProfileManager
from core.state import AppState


class ConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = PathManager(self.temp_dir.name, sys.executable, __file__)
        self.state = AppState(self.paths)
        self.manager = ProfileManager(self.state)

    def test_corrupt_config_is_preserved_and_defaults_load(self):
        with open(self.paths.control_config, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.manager.load_config()
        backups = glob.glob(self.paths.control_config + ".corrupt-*")
        self.assertEqual(len(backups), 1)
        self.assertIn("Tuned AC", self.state.get_profile_names())
        self.assertIn("Tuned Battery", self.state.get_profile_names())
        self.assertIn("corrupt", self.state.get_controller_status_message().lower())

    def test_save_keeps_previous_config_as_backup(self):
        self.manager.load_config()
        # save_config() is debounced/background since the perf pass; the
        # deterministic flush is save_config_sync().
        self.manager.save_config_sync()
        self.manager.save_config_sync()
        self.assertTrue(os.path.exists(self.paths.control_config + ".bak"))
        with open(self.paths.control_config, "r", encoding="utf-8") as handle:
            json.load(handle)


if __name__ == "__main__":
    unittest.main()
