# -*- coding: utf-8 -*-
"""Fan-target selection matrix: asleep dGPU drops the 40% floor, sensor
failures keep the safe-side floors."""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.path_manager import PathManager
from core.state import AppState
from core.app_services import AppServices

SILENT_CPU = [[50, 0], [60, 15], [70, 25], [80, 44], [85, 59], [90, 77], [95, 100]]
DEFAULT_GPU = [[40, 0], [55, 15], [65, 25], [70, 40], [75, 60], [80, 80], [85, 100]]


class FanTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        state = AppState(path_manager=PathManager(base_dir=base, executable_path="python.exe", main_script_path=""))
        cls.svc = AppServices(state=state)
        cls.svc.auto_temp_controller.update_curves(SILENT_CPU, DEFAULT_GPU)

    def target(self, cpu, gpu, asleep):
        return self.svc._select_fan_target("auto", cpu, gpu, asleep)

    def test_gpu_asleep_uses_cpu_curve_without_floor(self):
        t = self.target(55, -1.0, True)
        self.assertLess(t, 40)

    def test_gpu_read_error_keeps_40_floor(self):
        self.assertEqual(self.target(55, -1.0, False), 40)

    def test_cpu_dead_with_asleep_gpu_is_80(self):
        self.assertEqual(self.target(-1.0, -1.0, True), 80)

    def test_both_read_errors_are_80(self):
        self.assertEqual(self.target(-1.0, -1.0, False), 80)

    def test_cpu_dead_with_valid_gpu_is_40(self):
        self.assertEqual(self.target(-1.0, 70.0, False), 40)

    def test_hot_cpu_overrides_asleep(self):
        self.assertGreaterEqual(self.target(95, -1.0, True), 80)

    def test_silent_curve_honored_when_asleep(self):
        t = self.target(62, -1.0, True)
        self.assertTrue(0 < t < 40)


if __name__ == "__main__":
    unittest.main()
