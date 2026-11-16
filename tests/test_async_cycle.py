# -*- coding: utf-8 -*-
"""Async control cycle: ramp trajectory, on-change writes + keepalive,
BIOS mid-flight race guard, failure surfacing, watchdog rebuild."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.path_manager import PathManager
from core.state import AppState
from core.app_services import AppServices, FLAG_REASSERT_EVERY_CYCLES
from config.settings import WMIInternalSignal, FAN_MODE_AUTO, FAN_MODE_BIOS
from gui.qt import QApplication

# QTimer.isActive() needs an event dispatcher; tests run offscreen.
_APP = QApplication.instance() or QApplication([])

SILENT_CPU = [[50, 0], [60, 15], [70, 25], [80, 44], [85, 59], [90, 77], [95, 100]]


class FakeWMI:
    def __init__(self, temps):
        self.running = True
        self.temps = temps
        self.applies = []
        self.sync_calls = []

    @property
    def is_running(self):
        return self.running

    def submit_async(self, sig, params=None, on_done=None):
        if sig == "_get_temperatures":
            on_done(dict(self.temps), None)
        elif sig == WMIInternalSignal.APPLY_FAN_CONTROL:
            self.applies.append(dict(params))
            on_done({"applied": True}, None)
        else:
            raise AssertionError(f"unexpected signal {sig!r}")
        return True

    def execute_method(self, name, **kwargs):
        self.sync_calls.append((name, kwargs))
        return 0

    def stop(self, join_timeout=None):
        self.running = False

    def start(self, init_timeout=15.0):
        self.running = True
        return True


class AsyncCycleTests(unittest.TestCase):
    def setUp(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.state = AppState(path_manager=PathManager(base_dir=base, executable_path="python.exe", main_script_path=""))
        self.svc = AppServices(state=self.state)
        self.state.load_profiles_from_config({"Silent": {"fan_mode": FAN_MODE_AUTO, "cpu_fan_table": SILENT_CPU}}, ["Silent"])
        self.state.set_active_profile_name("Silent")

    def test_ramp_and_on_change_writes(self):
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        trajectory = []
        for _ in range(7):
            self.svc._perform_control_cycle()
            trajectory.append(self.state.get_applied_fan_speed_percent())
        # soft kick to max(target,30) from standstill, then -5 per cycle down to the curve target
        self.assertEqual(trajectory, [30, 25, 20, 17, 17, 17, 17])
        duties = [a["duty_raw"] for a in self.svc.wmi_interface.applies]
        self.assertEqual(len(duties), 4)  # one write per actual change, none for the holds
        self.assertEqual(duties[0], 69)   # raw(30)
        self.assertEqual(duties[1], 57)   # min-spin floor clamps 25% -> raw(25)
        self.assertTrue(all(a["reassert_flags"] for a in self.svc.wmi_interface.applies))

    def test_keepalive_reassert_after_idle_cycles(self):
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        for _ in range(7):
            self.svc._perform_control_cycle()
        self.svc.wmi_interface.applies.clear()
        for _ in range(FLAG_REASSERT_EVERY_CYCLES):
            self.svc._perform_control_cycle()
        self.assertEqual(len(self.svc.wmi_interface.applies), 1)

    def test_heat_override(self):
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 92.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.svc._perform_control_cycle()
        self.assertEqual(self.state.get_applied_fan_speed_percent(), 86)
        self.assertEqual(self.svc.wmi_interface.applies[-1]["duty_raw"], 196)

    def test_spin_up_rate_limited_below_bypass_thresholds(self):
        # 80 C -> curve target 44 (< 60%, < 85 C): climb is capped at +5 per cycle.
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 80.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.state.set_applied_fan_speed_percent(20)
        self.svc._kick_armed = False
        self.svc._perform_control_cycle()
        self.assertEqual(self.state.get_applied_fan_speed_percent(), 25)

    def test_spin_up_instant_for_hot_curve_target(self):
        # 87 C -> curve target well above 60%: the rate limit is bypassed (jump straight to target).
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 87.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.state.set_applied_fan_speed_percent(20)
        self.svc._kick_armed = False
        self.svc._perform_control_cycle()
        target = self.state.get_auto_fan_target_speed_percent()
        self.assertGreaterEqual(target, 60)
        self.assertEqual(self.state.get_applied_fan_speed_percent(), target)

    def test_temperature_spike_does_not_churn_target(self):
        # A one-cycle +8 C spike is median-filtered: the governing target must not move.
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        for _ in range(4):
            self.svc._perform_control_cycle()
        self.assertEqual(self.state.get_auto_fan_target_speed_percent(), 17)
        self.svc.wmi_interface.temps = {"cpu_temp": 70.0, "gpu_temp": -1.0, "gpu_asleep": True}
        self.svc._perform_control_cycle()
        self.assertEqual(self.state.get_auto_fan_target_speed_percent(), 17)

    def test_bios_midflight_race_corrects_flags(self):
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.state.set_applied_fan_mode(FAN_MODE_BIOS)
        self.svc._pending_cycle = {"target": 80, "percent": 80}
        self.svc._on_cycle_applied({"applied": True}, None)
        set_names = [name for name, _ in self.svc.wmi_interface.sync_calls if name.startswith("Set")]
        self.assertTrue(set_names)
        self.assertEqual(self.state.get_applied_fan_mode(), FAN_MODE_BIOS)

    def test_failure_surfaces_wmi_error(self):
        class Broken(FakeWMI):
            def submit_async(self, sig, params=None, on_done=None):
                on_done(None, RuntimeError("COM hang"))
                return True
        self.svc.wmi_interface = Broken(None)
        self.svc._perform_control_cycle()
        self.assertNotEqual(self.state.get_controller_status_message(), "")
        self.assertFalse(self.state.get_gpu_asleep())

    def test_watchdog_rebuilds_interface(self):
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.svc._cycle_in_flight = True
        self.svc._on_cycle_watchdog()
        self.assertFalse(self.svc._cycle_in_flight)
        self.assertTrue(self.svc.wmi_interface.running)

    def test_wmi_recovery_restarts_controller_loop(self):
        # recover_after_resume() stops the timer and returns early when the WMI
        # rebuild fails; a later _recover_wmi_if_due() must restart the control
        # loop (timer running + a live EC write), not just fire one orphan cycle.
        self.svc.wmi_interface = FakeWMI({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True})
        self.svc.controller_timer.stop()  # state left behind by a failed resume recovery
        self.svc._recover_wmi_if_due()
        self.assertTrue(self.svc.controller_timer.isActive())
        self.assertTrue(self.svc.wmi_interface.applies)  # one live control cycle ran


if __name__ == "__main__":
    unittest.main()
