# -*- coding: utf-8 -*-
"""Curve-editor undo/redo history and the NVML sleep guard.

The guard is measured, not speculative: on the reference machine any NVML
client activity pins an idle dGPU at P0 (~30 W) and the EC never reports it
asleep while polling continues; with the app stopped it powers down within
100 s. The gate below must make zero NVML calls in every idle path."""
import sys
import tempfile
import unittest
from pathlib import Path
from queue import Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from gui.curve_history import CurveEditHistory  # noqa: E402
from core.wmi_interface import WMIWorker  # noqa: E402


class CurveEditHistoryTests(unittest.TestCase):
    def test_push_undo_redo_roundtrip(self):
        history = CurveEditHistory()
        history.push("cpu", [[40, 0], [80, 80]])
        snapshot = history.undo(lambda ct: [[10, 10]])
        self.assertEqual(snapshot, ("cpu", [[40, 0], [80, 80]]))
        self.assertFalse(history.can_undo)
        self.assertTrue(history.can_redo)
        snapshot = history.redo(lambda ct: [[99, 99]])
        self.assertEqual(snapshot, ("cpu", [[10, 10]]))
        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)

    def test_push_clears_redo_chain(self):
        history = CurveEditHistory()
        history.push("cpu", [[40, 0]])
        history.undo(lambda ct: [[10, 10]])
        self.assertTrue(history.can_redo)
        history.push("gpu", [[50, 20]])
        self.assertFalse(history.can_redo)

    def test_interleaved_curve_types(self):
        history = CurveEditHistory()
        history.push("cpu", [[40, 0]])
        history.push("gpu", [[60, 30]])
        snapshot = history.undo(lambda ct: {"cpu": [[1, 1]], "gpu": [[2, 2]]}[ct])
        self.assertEqual(snapshot, ("gpu", [[60, 30]]))
        snapshot = history.undo(lambda ct: {"cpu": [[1, 1]], "gpu": [[2, 2]]}[ct])
        self.assertEqual(snapshot, ("cpu", [[40, 0]]))

    def test_snapshots_are_independent_copies(self):
        history = CurveEditHistory()
        points = [[40, 0]]
        history.push("cpu", points)
        points.append([90, 90])
        snapshot = history.undo(lambda ct: [])
        self.assertEqual(snapshot[1], [[40, 0]])

    def test_limit_and_clear(self):
        history = CurveEditHistory(limit=3)
        for i in range(5):
            history.push("cpu", [[i, i]])
        for _ in range(5):
            history.undo(lambda ct: [])
        history.clear()
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)


class _FakeGetObj:
    """The WMI getter surface the sensor sweep touches."""

    def __init__(self, gpu_temp1=60, gpu_temp2=0):
        self.gpu_temp1 = gpu_temp1
        self.gpu_temp2 = gpu_temp2

    def getCpuTemp(self):
        return 55

    def getGpuTemp1(self):
        return self.gpu_temp1

    def getGpuTemp2(self):
        return self.gpu_temp2

    def getRpm1(self):
        return 1200

    def getRpm2(self):
        return 1200


def make_worker(gpu_temp1, gpu_temp2, power_w):
    worker = WMIWorker(Queue(), "GB_WMIACPI_Get", "GB_WMIACPI_Set")
    worker._wmi_get_obj = _FakeGetObj(gpu_temp1, gpu_temp2)
    worker._nvml_ok = True
    worker._nvml_handle = object()
    calls = []

    def fake_read():
        calls.append(1)
        return (power_w, 0)

    worker._read_nvml_gpu = fake_read
    return worker, calls


class NvmlSleepGuardTests(unittest.TestCase):
    def test_asleep_gpu_makes_no_nvml_calls(self):
        worker, calls = make_worker(0, 0, 15.0)
        data = worker._get_core_sensors()
        self.assertTrue(data["gpu_asleep"])
        self.assertNotIn("gpu_power_w", data)
        self.assertEqual(calls, [])
        # the guard stays re-armed for the post-wake reading
        self.assertIsNone(worker._nvml_last_power_w)
        self.assertFalse(worker._nvml_idle_paused)

    def test_poll_disabled_makes_no_nvml_calls(self):
        worker, calls = make_worker(60, 0, 15.0)
        worker._nvml_poll_enabled = False
        worker._get_core_sensors()
        self.assertEqual(calls, [])

    def test_idle_reading_pauses_then_resumes_on_load_temp(self):
        worker, calls = make_worker(60, 0, 15.0)
        data = worker._get_core_sensors()
        self.assertEqual(data["gpu_power_w"], 15.0)  # first read...
        worker._get_core_sensors()
        worker._get_core_sensors()
        self.assertEqual(len(calls), 1)  # ...then nothing while cool
        # genuine load: die heats past the resume threshold
        worker._wmi_get_obj.gpu_temp1 = 70
        data = worker._get_core_sensors()
        self.assertEqual(data["gpu_power_w"], 15.0)
        self.assertEqual(len(calls), 2)

    def test_busy_gpu_keeps_being_polled(self):
        worker, calls = make_worker(60, 0, 80.0)
        worker._get_core_sensors()
        worker._get_core_sensors()
        self.assertEqual(len(calls), 2)  # above the skip floor: every cycle

    def test_pinned_idle_reading_still_pauses(self):
        # pinned idle draws 31-34 W on this machine - above the 30 W offset
        # floor, but the skip threshold is 40 W so it must still pause
        worker, calls = make_worker(60, 0, 34.0)
        worker._get_core_sensors()
        worker._get_core_sensors()
        self.assertEqual(len(calls), 1)

    def test_sleep_cycle_rearms_after_idle_pause(self):
        worker, calls = make_worker(60, 0, 15.0)
        worker._get_core_sensors()  # read -> paused
        worker._get_core_sensors()  # paused, no call
        worker._wmi_get_obj.gpu_temp1 = 0
        worker._wmi_get_obj.gpu_temp2 = 0
        worker._get_core_sensors()  # asleep: no call, re-arm
        self.assertIsNone(worker._nvml_last_power_w)
        worker._wmi_get_obj.gpu_temp1 = 60
        data = worker._get_core_sensors()  # awake again: fresh reading
        self.assertIn("gpu_power_w", data)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
