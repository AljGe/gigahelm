# -*- coding: utf-8 -*-
"""Sustained-hot balloon: fires once after sustained cycles at/above the
threshold, re-arms when sensors cool, ignores a sleeping dGPU's error value."""
import os
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
from core.app_services import AppServices  # noqa: E402
from config.settings import HOT_ALERT_SUSTAINED_CYCLES, HOT_ALERT_TEMP_C  # noqa: E402

CURVE = [[40, 0], [50, 20], [60, 40], [70, 60], [80, 80]]


class FakeWMI:
    def __init__(self, temps):
        self.running = True
        self.temps = temps

    @property
    def is_running(self):
        return self.running

    def stop(self, join_timeout=None):
        self.running = False

    def start(self, init_timeout=15.0):
        self.running = True
        return True

    def submit_async(self, signal, params=None, on_done=None):
        if signal == "_get_core_sensors":
            on_done(dict(self.temps), None)
        else:
            # charge policy / apply writes: accept and succeed
            on_done({"applied": True}, None)
        return True

    def execute_method(self, name, **kwargs):
        return 0


class HotAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gigahelm-hot-")
        self.state = AppState(path_manager=PathManager(
            base_dir=self.tmp, executable_path="python.exe", main_script_path=""))
        self.svc = AppServices(state=self.state)
        self.state.load_profiles_from_config(
            {"Silent": {"fan_mode": "auto", "cpu_fan_table": CURVE}}, ["Silent"])
        self.state.set_active_profile_name("Silent")
        self.state.set_controller_status_message("")
        self.svc.wmi_interface = FakeWMI(
            {"cpu_temp": 60.0, "gpu_temp": 60.0, "gpu_asleep": False,
             "fan1_rpm": 1200, "fan2_rpm": 1200})
        self.svc._last_wmi_recovery_attempt = 0.0

    def _cycle(self):
        self.svc.controller_timer.stop()
        self.svc._perform_control_cycle()

    def _status(self):
        return self.state.get_controller_status_message()

    def test_sustained_heat_alerts_once(self):
        self.svc.wmi_interface.temps = {
            "cpu_temp": 96.0, "gpu_temp": 96.0, "gpu_asleep": False,
            "fan1_rpm": 4000, "fan2_rpm": 4000}
        for _ in range(HOT_ALERT_SUSTAINED_CYCLES):
            self._cycle()
        self.assertIn("96", self._status())
        # further hot cycles stay silent (no repeat balloon)
        self._cycle()
        self.assertIn("96", self._status())
        self.assertEqual(self.svc._hot_alert_active, True)

    def test_cooling_rearms_and_realerts(self):
        self.svc.wmi_interface.temps = {
            "cpu_temp": HOT_ALERT_TEMP_C + 1.0, "gpu_temp": 60.0, "gpu_asleep": False,
            "fan1_rpm": 4000, "fan2_rpm": 4000}
        for _ in range(HOT_ALERT_SUSTAINED_CYCLES):
            self._cycle()
        self.assertTrue(self.svc._hot_alert_active)
        self.svc.wmi_interface.temps = {
            "cpu_temp": 70.0, "gpu_temp": 70.0, "gpu_asleep": False,
            "fan1_rpm": 2000, "fan2_rpm": 2000}
        self._cycle()
        self.assertFalse(self.svc._hot_alert_active)
        # next excursion alerts again
        self.svc.wmi_interface.temps = {
            "cpu_temp": HOT_ALERT_TEMP_C + 2.0, "gpu_temp": 70.0, "gpu_asleep": False,
            "fan1_rpm": 4000, "fan2_rpm": 4000}
        for _ in range(HOT_ALERT_SUSTAINED_CYCLES):
            self._cycle()
        self.assertTrue(self.svc._hot_alert_active)
        self.assertIn(str(int(HOT_ALERT_TEMP_C + 2.0)), self._status())

    def test_asleep_gpu_excluded_and_transient_spikes_ignored(self):
        # sleeping dGPU's error value must not trigger; a short spike below
        # the sustained-cycle count must not fire either
        self.svc.wmi_interface.temps = {
            "cpu_temp": 60.0, "gpu_temp": -1.0, "gpu_asleep": True,
            "fan1_rpm": 1200, "fan2_rpm": 1200}
        self._cycle()
        self.assertFalse(self.svc._hot_alert_active)
        self.svc.wmi_interface.temps = {
            "cpu_temp": HOT_ALERT_TEMP_C + 5.0, "gpu_temp": -1.0, "gpu_asleep": True,
            "fan1_rpm": 4000, "fan2_rpm": 4000}
        for _ in range(HOT_ALERT_SUSTAINED_CYCLES - 1):
            self._cycle()
        self.assertFalse(self.svc._hot_alert_active)
        self.assertEqual(self._status(), "")


if __name__ == "__main__":
    unittest.main()
