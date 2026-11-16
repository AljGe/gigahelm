# -*- coding: utf-8 -*-
"""Charge automation: EC drift detection + reapply, resume re-verify, and
power-source profile switching."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

# QTimer.singleShot / timers need an event dispatcher; CI runs offscreen.
_APP = QApplication.instance() or QApplication([])

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.app_services import AppServices  # noqa: E402
from config.settings import (  # noqa: E402
    CHARGE_POLICY_CUSTOM, WMIInternalSignal,
)

CURVE = [[50, 0], [60, 15], [70, 25], [80, 40], [85, 60], [90, 80], [95, 100]]
PROFILES = {
    "Silent": {"fan_mode": "auto", "cpu_fan_table": CURVE,
               "battery_charge_policy": "bios"},
    "Limited": {"fan_mode": "auto", "cpu_fan_table": CURVE,
                "battery_charge_policy": "custom", "battery_charge_threshold": 80},
}


class FakeWMI:
    """Charge-focused WMIInterface double: EC state is scriptable and charge
    writes are captured instead of sent to the EC."""

    def __init__(self, charge_policy=4, charge_threshold=80):
        self.running = True
        self.charge_policy = charge_policy
        self.charge_threshold = charge_threshold
        self.charge_writes = []
        self.charge_reads = 0

    @property
    def is_running(self):
        return self.running

    def stop(self, join_timeout=None):
        self.running = False

    def start(self, init_timeout=15.0):
        self.running = True
        return True

    def set_nvml_poll_enabled(self, enabled: bool) -> None:
        pass

    def submit_async(self, signal, params=None, on_done=None):
        if signal == "_get_charge_status":
            self.charge_reads += 1
            on_done({"charge_policy": self.charge_policy,
                     "charge_threshold": self.charge_threshold}, None)
        elif signal is WMIInternalSignal.APPLY_CHARGE_POLICY:
            self.charge_writes.append(dict(params))
            self.charge_policy = int(params.get("policy", 0))
            threshold = params.get("threshold")
            if threshold is not None:
                self.charge_threshold = int(float(threshold))
            on_done({"applied": True}, None)
        elif signal == "_get_core_sensors":
            on_done({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True,
                     "fan1_rpm": 1200, "fan2_rpm": 1200}, None)
        else:
            raise AssertionError(f"unexpected signal {signal!r}")
        return True

    def execute_method(self, name, **kwargs):
        return 0


class ChargeAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gigahelm-charge-")
        self.state = AppState(path_manager=PathManager(
            base_dir=self.tmp, executable_path="python.exe", main_script_path=""))
        self.svc = AppServices(state=self.state)
        self.state.load_profiles_from_config(PROFILES, ["Silent", "Limited"])
        self.state.set_active_profile_name("Limited")

    def _clear_writes_and_throttle(self):
        self.svc.wmi_interface.charge_writes.clear()
        self.svc.wmi_interface.charge_reads = 0
        self.svc._last_charge_write_ms = 0.0

    def test_drift_is_detected_and_reapplied(self):
        """EC dropped the charge setting (policy 0 instead of custom 4)."""
        self.svc.wmi_interface = FakeWMI(charge_policy=0, charge_threshold=100)
        self._clear_writes_and_throttle()
        self.svc._verify_charge_state()
        self.assertEqual(len(self.svc.wmi_interface.charge_writes), 1)
        write = self.svc.wmi_interface.charge_writes[0]
        self.assertEqual(float(write["policy"]), 4.0)
        self.assertEqual(float(write["threshold"]), 80.0)

    def test_matching_ec_state_writes_nothing(self):
        self.svc.wmi_interface = FakeWMI(charge_policy=4, charge_threshold=80)
        self._clear_writes_and_throttle()
        self.svc._verify_charge_state()
        self.assertEqual(self.svc.wmi_interface.charge_writes, [])
        self.assertEqual(self.svc.wmi_interface.charge_reads, 1)

    def test_unreadable_ec_state_writes_nothing(self):
        """A dead charge getter must not trigger blind rewrites every minute."""
        self.svc.wmi_interface = FakeWMI(charge_policy=-1, charge_threshold=-1)
        self._clear_writes_and_throttle()
        self.svc._verify_charge_state()
        self.assertEqual(self.svc.wmi_interface.charge_writes, [])

    def test_threshold_drift_alone_reapplies(self):
        self.svc.wmi_interface = FakeWMI(charge_policy=4, charge_threshold=100)
        self._clear_writes_and_throttle()
        self.svc._verify_charge_state()
        self.assertEqual(len(self.svc.wmi_interface.charge_writes), 1)

    def test_recover_after_resume_triggers_charge_verify(self):
        fake = FakeWMI(charge_policy=4, charge_threshold=80)
        self.svc.wmi_interface = fake
        self.svc.recover_after_resume()
        self.assertGreaterEqual(fake.charge_reads, 1)

    def test_power_source_switches_bound_profile(self):
        self.state.set_profile_on_battery("Silent")
        self.svc.wmi_interface = FakeWMI(charge_policy=4, charge_threshold=80)
        self.svc._last_charge_write_ms = 0.0
        self.svc.on_power_source_changed(on_ac=False)
        self.assertEqual(self.state.get_active_profile_name(), "Silent")

    def test_unbound_power_source_keeps_profile(self):
        self.state.set_active_profile_name("Limited")
        self.svc.wmi_interface = FakeWMI(charge_policy=4, charge_threshold=80)
        self.svc._last_charge_write_ms = 0.0
        self.svc.on_power_source_changed(on_ac=True)
        self.assertEqual(self.state.get_active_profile_name(), "Limited")

    def test_bindings_persist_through_state_roundtrip(self):
        self.state.set_profile_on_ac("Limited")
        self.assertEqual(self.state.get_profile_on_ac(), "Limited")
        self.state.set_profile_on_ac("")
        self.assertEqual(self.state.get_profile_on_ac(), "")


if __name__ == "__main__":
    unittest.main()
