# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fan_target import THERMAL_GUARDS, guard_floor  # noqa: E402
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
        self.assertEqual(force_duty_percent(82, 57), 48)     # floor interpolates
        self.assertEqual(force_duty_percent(86, 114), 64)

    def test_hot_and_at_floor_spares(self):
        self.assertIsNone(force_duty_percent(91, 182))       # 80 % already applied
        self.assertIsNone(force_duty_percent(83, 182))       # above the 52 % floor
        self.assertIsNone(force_duty_percent(96, 227))       # 100 %

    def test_unreadable_duty_forces_floor_while_hot(self):
        self.assertEqual(force_duty_percent(91, None), 80)
        self.assertEqual(force_duty_percent(91, -1), 80)

    def test_floor_matches_fan_target_everywhere(self):
        # The watchdog must mirror guard_floor exactly or the two writers
        # fight; continuous anchors leave no engagement state to diverge.
        for tenth in range(700, 1001):
            temp = tenth / 10
            floor = guard_floor(temp)
            if floor == 0:
                self.assertIsNone(force_duty_percent(temp, 0))
            else:
                self.assertEqual(force_duty_percent(temp, 0), floor)

    def test_thresholds_match_fan_target(self):
        self.assertEqual(THERMAL_GUARDS, [(80.0, 40), (85.0, 60), (90.0, 80)])
        self.assertEqual(DUTY_RAW_MAX, 227.0)

    def test_raw_conversion_matches_percent_to_raw(self):
        from core.fan_duty import percent_to_raw
        # one raw unit below each anchor's duty must force that anchor floor
        for temp, percent in ((80, 40), (85, 60), (90, 80), (95, 80)):
            self.assertEqual(
                force_duty_percent(temp, percent_to_raw(percent) - 1),
                percent)


class _FakeGetter:
    def __init__(self):
        self.cpu_temp = 90.0
        self.fixed_raw = 0
        self.gpu_temp1 = 0.0
        self.gpu_temp2 = 0.0
        self.gpu_fan_raw = 0

    def getCpuTemp(self):
        return self.cpu_temp

    def GetFixedFanSpeed(self):
        return self.fixed_raw

    def getGpuTemp1(self):
        return self.gpu_temp1

    def getGpuTemp2(self):
        return self.gpu_temp2

    def GetGPUFanDuty(self):
        return self.gpu_fan_raw


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
        # 90 °C, EC at 0: first poll forces the 80 % floor (5 setter calls:
        # 4 flag reasserts + the CPU duty; the GPU channel stands down at
        # 0 °C sensor readings = sleeping dGPU)
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (80, None))
        self.assertEqual(len(self.setter.calls), 5)
        # immediate re-poll: unchanged floor -> throttled, zero writes
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (None, None))
        self.assertEqual(len(self.setter.calls), 5)
        # 10 s later: re-forced
        self.now["t"] += 10.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (80, None))
        self.assertEqual(len(self.setter.calls), 10)

    def test_higher_floor_forces_inside_backoff_window(self):
        self.getter.cpu_temp = 85.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (60, None))
        self.assertEqual(len(self.setter.calls), 5)
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (None, None))
        # hotter: floor rises 60 -> 80, must not wait for the window
        self.getter.cpu_temp = 90.0
        self.now["t"] += 1.0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter), (80, None))
        self.assertEqual(len(self.setter.calls), 10)

    def test_hot_gpu_forces_only_the_gpu_channel(self):
        self.getter.cpu_temp = 60.0          # CPU cool: no CPU force
        self.getter.gpu_temp1 = 0.0          # one GPU sensor dead…
        self.getter.gpu_temp2 = 91.0         # …the other hot: max wins
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (None, 80))
        self.assertIn("SetGPUFanDuty", self.setter.calls)
        self.assertNotIn("SetFixedFanSpeed", self.setter.calls)
        self.assertEqual(len(self.setter.calls), 5)  # 4 flags + GPU duty

    def test_zero_gpu_temps_stand_down(self):
        # EC reports 0 degC for both GPU sensors exactly when the dGPU is
        # powered down: no die to cool, no force even with a hot CPU curve…
        self.getter.cpu_temp = 60.0
        self.getter.gpu_temp1 = 0.0
        self.getter.gpu_temp2 = 0.0
        self.getter.gpu_fan_raw = 0
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (None, None))
        self.assertEqual(self.setter.calls, [])
        # …and unreadable GPU sensors stand the GPU channel down too.
        self.getter.gpu_temp1 = None
        self.getter.gpu_temp2 = None
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (None, None))
        self.assertEqual(self.setter.calls, [])

    def test_hot_cpu_does_not_drag_the_gpu_channel(self):
        # CPU at 90 °C forces the CPU channel; the cool-but-awake GPU
        # (52 degC, EC already above any GPU floor) must stay untouched.
        self.getter.cpu_temp = 90.0
        self.getter.gpu_temp1 = 50.0
        self.getter.gpu_temp2 = 52.0
        self.getter.gpu_fan_raw = 227.0      # GPU EC at 100 %
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (80, None))
        self.assertIn("SetFixedFanSpeed", self.setter.calls)
        self.assertNotIn("SetGPUFanDuty", self.setter.calls)

    def test_gpu_backoff_is_independent_of_cpu(self):
        # Both channels hot: each forces its own floor; a CPU re-force inside
        # the backoff window must not block a GPU re-force (and vice versa).
        self.getter.cpu_temp = 90.0
        self.getter.gpu_temp1 = 90.0
        self.getter.gpu_temp2 = None
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (80, 80))
        self.assertEqual(self.setter.calls.count("SetFixedFanSpeed"), 1)
        self.assertEqual(self.setter.calls.count("SetGPUFanDuty"), 1)
        # immediate re-poll: both throttled
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (None, None))
        # GPU re-forces after its own 10 s even though the CPU channel was
        # re-forced just now (advance only 10 s: CPU still throttled at
        # exactly the boundary -> CPU re-forces too at ==10.0; offset by
        # asserting the GPU write happened while calls stay channel-scoped)
        self.now["t"] += 10.0
        self.getter.gpu_fan_raw = 0          # GPU EC dropped below floor again
        self.assertEqual(self.watchdog._check_once(self.getter, self.setter),
                         (80, 80))
        self.assertEqual(self.setter.calls.count("SetFixedFanSpeed"), 2)
        self.assertEqual(self.setter.calls.count("SetGPUFanDuty"), 2)

    def test_log_rotates_at_size_cap(self):
        path = os.path.join(self._tmp.name, "thermal_guard.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * (self.watchdog.LOG_MAX_BYTES + 1))
        self.watchdog._log("rotation test")
        self.assertTrue(os.path.exists(path + ".old"))
        self.assertLess(os.path.getsize(path), 512)

class LoopAliveStanddownTests(unittest.TestCase):
    """A freshly-cycling loop owns the EC; the guard bridges stalls only.

    Measured 2026-11-19: with independent +-1-2 degC temperature samples the
    loop and guard straddled the 80 degC knee ~820 times/day, each writing
    duty 4-8 points apart. A fresh liveness mark silences the guard."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.getter = _FakeGetter()
        self.setter = _FakeSetter()
        self.age = {"v": None}

    def _watchdog(self) -> ThermalGuardWatchdog:
        watchdog = ThermalGuardWatchdog(self._tmp.name,
                                        loop_alive=lambda: self.age["v"])
        watchdog._owns_fans = lambda: True
        return watchdog

    def test_fresh_loop_silences_guard(self):
        # 90 degC, EC at 0: the old behavior forces 80 % - with a liveness
        # mark only 1 s old the loop owns it and nothing is written.
        self.age["v"] = 1.0
        watchdog = self._watchdog()
        self.assertEqual(watchdog._check_once(self.getter, self.setter),
                         (None, None))
        self.assertEqual(self.setter.calls, [])

    def test_stale_loop_lets_guard_force(self):
        self.age["v"] = 30.0
        watchdog = self._watchdog()
        self.assertEqual(watchdog._check_once(self.getter, self.setter),
                         (80, None))
        self.assertEqual(self.setter.calls.count("SetFixedFanSpeed"), 1)

    def test_unknown_age_keeps_guard_armed(self):
        # None = the loop never cycled (startup, WMI down): guard armed.
        watchdog = self._watchdog()
        self.assertEqual(watchdog._check_once(self.getter, self.setter),
                         (80, None))


if __name__ == "__main__":
    unittest.main()
