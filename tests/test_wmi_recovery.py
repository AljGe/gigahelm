# -*- coding: utf-8 -*-
"""WMI recovery must restart the controller loop.

``recover_after_resume()`` stops ``controller_timer`` and returns early when
the first WMI rebuild after wake fails; ``_recover_wmi_if_due()`` is then the
only chance to bring the loop back. It must restart the timer (not just fire
one orphan cycle), otherwise the EC sits at the last written duty until the
user touches fan mode. Regression test for the stuck-after-resume bug.
"""
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

# QTimer.isActive() needs an event dispatcher; CI runs offscreen.
_APP = QApplication.instance() or QApplication([])

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.app_services import AppServices  # noqa: E402
import core.wmi_interface as wmi_module  # noqa: E402
from config.settings import WMIInternalSignal, FAN_MODE_AUTO  # noqa: E402

CURVE = [[50, 0], [60, 15], [70, 25], [80, 40], [85, 60], [90, 80], [95, 100]]


class FakeWMI:
    """Just enough of WMIInterface for one async control cycle."""

    def __init__(self):
        self.running = True
        self.sensor_reads = 0
        self.applies = []

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
        if signal == "_get_core_sensors":
            self.sensor_reads += 1
            on_done({"cpu_temp": 62.0, "gpu_temp": -1.0, "gpu_asleep": True,
                     "fan1_rpm": 1200, "fan2_rpm": 1200}, None)
        elif signal is WMIInternalSignal.APPLY_FAN_CONTROL:
            self.applies.append(dict(params))
            on_done({"applied": True}, None)
        else:
            raise AssertionError(f"unexpected signal {signal!r}")
        return True


class WmiRecoveryTests(unittest.TestCase):
    def setUp(self):
        # temp base dir: heartbeat/state writes must not dirty the repo
        self.tmp = tempfile.mkdtemp(prefix="gigahelm-test-")
        self.state = AppState(path_manager=PathManager(
            base_dir=self.tmp, executable_path="python.exe", main_script_path=""))
        self.svc = AppServices(state=self.state)
        # Hermetic at elevation: a real worker start succeeds when EC WMI is
        # available, latching _cycle_in_flight without an event loop to drain
        # it - which would skip every fake-driven cycle below.
        patcher = patch.object(wmi_module.WMIInterface, "start",
                               return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state.load_profiles_from_config(
            {"Silent": {"fan_mode": FAN_MODE_AUTO, "cpu_fan_table": CURVE}},
            ["Silent"])
        self.state.set_active_profile_name("Silent")

    def test_recovery_restarts_controller_loop(self):
        self.svc.wmi_interface = FakeWMI()
        # State left behind by a failed recover_after_resume(): timer stopped.
        self.svc.controller_timer.stop()
        # The failed resume recovery consumed the 30 s throttle marker; the
        # next due recovery happens later, so clear it.
        self.svc._last_wmi_recovery_attempt = 0.0
        self.svc._recover_wmi_if_due()
        self.assertTrue(self.svc.controller_timer.isActive())
        # One live control cycle ran: sensor read happened, duty was written.
        self.assertGreaterEqual(self.svc.wmi_interface.sensor_reads, 1)
        self.assertTrue(self.svc.wmi_interface.applies)


if __name__ == "__main__":
    unittest.main()
