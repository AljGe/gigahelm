# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest.mock import patch

from tools import bios_watchdog


class WatchdogDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_last_mode(self, value):
        with open(os.path.join(self.base_dir, "last_mode.state"), "w", encoding="ascii") as handle:
            handle.write(value)

    def test_bios_mode_skips(self):
        self._write_last_mode("bios")
        with patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_not_called()

    def test_emergency_mode_skips(self):
        self._write_last_mode("emergency")
        with patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_not_called()

    def test_no_instance_reverts(self):
        self._write_last_mode("auto")
        with patch.object(bios_watchdog, "_instance_running", return_value=False), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_called_once_with(self.base_dir)

    def test_running_with_fresh_heartbeat_skips(self):
        self._write_last_mode("auto")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=True), \
             patch.object(bios_watchdog, "_heartbeat_age", return_value=2.0), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_not_called()

    def test_running_with_stale_heartbeat_reverts(self):
        self._write_last_mode("custom")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=True), \
             patch.object(bios_watchdog, "_heartbeat_age", return_value=120.0), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_called_once_with(self.base_dir)

    def test_missing_heartbeat_with_instance_reverts(self):
        self._write_last_mode("custom")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=True), \
             patch.object(bios_watchdog, "_heartbeat_age", return_value=None), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_called_once_with(self.base_dir)

    def test_revert_failure_returns_one(self):
        self._write_last_mode("auto")
        with patch.object(bios_watchdog, "_instance_running", return_value=False), \
             patch.object(bios_watchdog, "_revert_to_bios", side_effect=RuntimeError("no WMI")):
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 1)

    def test_foreign_holder_skips(self):
        self._write_last_mode("auto")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=False), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_not_called()

    def test_local_holder_stale_heartbeat_reverts(self):
        self._write_last_mode("custom")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=True), \
             patch.object(bios_watchdog, "_heartbeat_age", return_value=120.0), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_called_once_with(self.base_dir)

    def test_holder_check_failure_falls_back(self):
        self._write_last_mode("custom")
        with patch.object(bios_watchdog, "_instance_running", return_value=True), \
             patch.object(bios_watchdog, "_local_holder_running", return_value=None), \
             patch.object(bios_watchdog, "_heartbeat_age", return_value=120.0), \
             patch.object(bios_watchdog, "_revert_to_bios") as revert:
            self.assertEqual(bios_watchdog.run_bios_revert_watchdog(self.base_dir), 0)
            revert.assert_called_once_with(self.base_dir)


if __name__ == "__main__":
    unittest.main()
