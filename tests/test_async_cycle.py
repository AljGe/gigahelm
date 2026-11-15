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
        # kick to 40 from standstill, then -5 per cycle down to the curve target
        self.assertEqual(trajectory, [40, 35, 30, 25, 20, 17, 17])
        duties = [a["duty_raw"] for a in self.svc.wmi_interface.applies]
        self.assertEqual(len(duties), 6)  # one write per actual change, none for the hold
        self.assertEqual(duties[0], 91)   # raw(40)
        self.assertEqual(duties[3], 57)   # min-spin floor clamps 20% -> raw(25)
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


if __name__ == "__main__":
    unittest.main()
