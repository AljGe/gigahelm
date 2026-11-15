# -*- coding: utf-8 -*-
"""BIOS-revert watchdog gate logic (EC write and mutex probe are injected)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.bios_watchdog import run_bios_revert_watchdog


class BiosWatchdogGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.reverts = []

    def tearDown(self):
        self.tmp.cleanup()

    def write_mode(self, mode):
        with open(os.path.join(self.base, 'last_mode.state'), 'w') as f:
            f.write(mode)

    def read_mode(self):
        try:
            with open(os.path.join(self.base, 'last_mode.state'), 'r') as f:
                return f.read().strip()
        except OSError:
            return ""

    def _gate(self, probe):
        return run_bios_revert_watchdog(
            self.base,
            probe_running=probe,
            revert=lambda: self.reverts.append(1),
        )

    def test_reverts_when_owners_absent(self):
        self.write_mode("auto")
        self.assertEqual(self._gate(probe=lambda: False), 0)
        self.assertEqual(self.reverts, [1])
        self.assertEqual(self.read_mode(), "bios")

    def test_reverts_custom_mode_too(self):
        self.write_mode("custom")
        self.assertEqual(self._gate(probe=lambda: False), 0)
        self.assertEqual(self.read_mode(), "bios")

    def test_skips_when_app_running(self):
        self.write_mode("auto")
        self.assertEqual(self._gate(probe=lambda: True), 0)
        self.assertEqual(self.reverts, [])
        self.assertEqual(self.read_mode(), "auto")

    def test_skips_when_not_owner(self):
        self.write_mode("bios")
        self.assertEqual(self._gate(probe=lambda: False), 0)
        self.assertEqual(self.reverts, [])
        self.assertEqual(self.read_mode(), "bios")

    def test_skips_when_file_missing(self):
        self.assertEqual(self._gate(probe=lambda: False), 0)
        self.assertEqual(self.reverts, [])

    def test_failed_revert_keeps_ownership(self):
        # A failed EC write must leave last_mode.state claiming ownership so the next tick retries.
        self.write_mode("auto")

        def boom():
            raise RuntimeError("WMI down")

        code = run_bios_revert_watchdog(self.base, probe_running=lambda: False, revert=boom)
        self.assertEqual(code, 1)
        self.assertEqual(self.read_mode(), "auto")

    def test_log_written(self):
        self.write_mode("auto")
        self._gate(probe=lambda: False)
        log = os.path.join(self.base, 'bios_watchdog.log')
        self.assertTrue(os.path.exists(log))
        with open(log, encoding='utf-8') as f:
            self.assertIn("reverted", f.read())


if __name__ == "__main__":
    unittest.main()
