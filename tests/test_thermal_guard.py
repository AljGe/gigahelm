# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fan_target import THERMAL_GUARDS  # noqa: E402
from core.thermal_guard import (  # noqa: E402
    DUTY_RAW_MAX, ThermalGuardWatchdog, force_duty_percent,
)


class ForceDutyPercentTests(unittest.TestCase):
    """The independent guard must mirror select_fan_target's guard floors."""

    def test_cool_temp_never_forces(self):
        self.assertIsNone(force_duty_percent(75, 0))
        self.assertIsNone(force_duty_percent(50, 0))
        self.assertIsNone(force_duty_percent(None, 0))
        self.assertIsNone(force_duty_percent(-1, 0))  # unreadable temp

    def test_floor_boundary_is_inclusive(self):
        self.assertEqual(force_duty_percent(80.0, 0), 40)
        self.assertEqual(force_duty_percent(85.0, 0), 60)
        self.assertEqual(force_duty_percent(90.0, 0), 80)

    def test_hot_and_below_floor_forces(self):
        self.assertEqual(force_duty_percent(91, 0), 80)      # experiment failure case
        self.assertEqual(force_duty_percent(91, 57), 80)     # EC min-spin floor
        self.assertEqual(force_duty_percent(82, 57), 40)
        self.assertEqual(force_duty_percent(86, 114), 60)

    def test_hot_and_at_floor_spares(self):
        self.assertIsNone(force_duty_percent(91, 182))       # 80 % already applied
        self.assertIsNone(force_duty_percent(83, 100))       # 44 % >= floor 40 %
        self.assertIsNone(force_duty_percent(96, 227))       # 100 %

    def test_unreadable_duty_forces_floor_while_hot(self):
        self.assertEqual(force_duty_percent(91, None), 80)
        self.assertEqual(force_duty_percent(91, -1), 80)

    def test_thresholds_match_fan_target(self):
        self.assertEqual(THERMAL_GUARDS, [(80.0, 40), (85.0, 60), (90.0, 80)])
        self.assertEqual(DUTY_RAW_MAX, 227.0)

    def test_raw_conversion_matches_percent_to_raw(self):
        from core.fan_duty import percent_to_raw
        # one degree below each floor's raw value must force that floor
        for temp, percent in ((82, 40), (86, 60), (95, 80)):
            self.assertEqual(force_duty_percent(temp, percent_to_raw(percent) - 1),
                             percent)


class _FakeGetter:
    def __init__(self):
        self.cpu_temp = 90.0
        self.fixed_raw = 0

    def getCpuTemp(self):
        return self.cpu_temp

    def GetFixedFanSpeed(self):
        return self.fixed_raw


class _FakeSetter:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _write(**kwargs):
            self.calls.append(name)
        return _write


class WatchdogBackoffTests(unittest.TestCase):
    """Re-forcing the same floor is throttled; a higher floor never waits."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.watchdog = ThermalGuardWatchdog(self._tmp.name)
        self.watchdog._owns_fans = lambda: True
        self.getter = _FakeGetter()
        self.setter = _FakeSetter()
        self.now = {"t": 1000.0}
        patcher = patch("core.thermal_guard.time.monotonic",
                        lambda: self.now["t"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_floor_backoff_then_reforce_after_10s(self):
        # 90 °C, EC at 0: first poll forces the 80 % floor (6 setter calls)
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), 80)
        self.assertEqual(len(self.setter.calls), 6)
        # immediate re-poll: unchanged floor -> throttled, zero writes
        self.assertIsNone(self.watchdog._check_once(self.getter, self.setter))
        self.assertEqual(len(self.setter.calls), 6)
        # 10 s later: re-forced
        self.now["t"] += 10.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), 80)
        self.assertEqual(len(self.setter.calls), 12)

    def test_higher_floor_forces_inside_backoff_window(self):
        self.getter.cpu_temp = 85.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), 60)
        self.assertEqual(len(self.setter.calls), 6)
        self.assertIsNone(self.watchdog._check_once(self.getter, self.setter))
        # hotter: floor rises 60 -> 80, must not wait for the window
        self.getter.cpu_temp = 90.0
        self.now["t"] += 1.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), 80)
        self.assertEqual(len(self.setter.calls), 12)

    def test_log_rotates_at_size_cap(self):
        path = os.path.join(self._tmp.name, "thermal_guard.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * (self.watchdog.LOG_MAX_BYTES + 1))
        self.watchdog._log("rotation test")
        self.assertTrue(os.path.exists(path + ".old"))
        self.assertLess(os.path.getsize(path), 512)


if __name__ == "__main__":
    unittest.main()
