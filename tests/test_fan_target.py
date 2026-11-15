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


class ControllerHysteresisTests(unittest.TestCase):
    """Temperature jitter around a curve knee must not churn the target."""

    def setUp(self):
        from core.auto_temp_controller import AutoTemperatureController
        self.ctl = AutoTemperatureController()
        self.ctl.update_curves(SILENT_CPU, DEFAULT_GPU)

    def test_downward_churn_blocked_during_jitter(self):
        # Park at 86 C (steep [85,59]-[90,77] region), then jitter below the anchor:
        # hysteresis must block every down-step, so the target never moves.
        parked = self.ctl.theoretical_target(86.0, -1.0)
        targets = [self.ctl.theoretical_target(t, -1.0) for t in (84, 85, 84, 85, 84)]
        self.assertEqual(targets, [parked] * 5)

    def test_drop_beyond_hysteresis_releases(self):
        self.ctl.theoretical_target(63.0, -1.0)
        held = self.ctl.theoretical_target(62.0, -1.0)
        # 3 C drop < hysteresis 5: still held
        self.assertEqual(self.ctl.theoretical_target(60.0, -1.0), held)
        # 8 C drop below the anchor: released to the cooler curve value
        self.assertLess(self.ctl.theoretical_target(55.0, -1.0), held)

    def test_rise_applies_immediately(self):
        low = self.ctl.theoretical_target(62.0, -1.0)
        self.assertGreater(self.ctl.theoretical_target(80.0, -1.0), low)


if __name__ == "__main__":
    unittest.main()
