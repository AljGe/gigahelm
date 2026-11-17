# -*- coding: utf-8 -*-
"""GPU utilization display: the cycle publishes NVML util into state, and the
GPU card footer composes fan RPM plus utilization (RPM-only when unknown)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.profile_manager import ProfileManager  # noqa: E402
from core.settings_manager import SettingsManager  # noqa: E402
from core.app_services import AppServices  # noqa: E402
import core.wmi_interface as wmi_module  # noqa: E402
from gui.theme import ThemeManager  # noqa: E402
from gui.dashboard import Dashboard  # noqa: E402
from config.settings import WMIInternalSignal, RPM_READ_ERROR_VALUE  # noqa: E402
from tools.localization import tr  # noqa: E402

CURVE = [[40, 0], [50, 20], [60, 40], [70, 60]]


class FakeWMI:
    """Cycle double with scriptable NVML utilization."""

    def __init__(self, gpu_util=None, gpu_temp=45.0):
        self.running = True
        self.gpu_util = gpu_util
        self.gpu_temp = gpu_temp
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
            data = {"cpu_temp": 45.0, "gpu_temp": self.gpu_temp, "gpu_asleep": False,
                    "fan1_rpm": 1200, "fan2_rpm": 1200}
            if self.gpu_util is not None:
                data["gpu_util"] = self.gpu_util
            on_done(data, None)
        elif signal is WMIInternalSignal.APPLY_FAN_CONTROL:
            self.applies.append(dict(params))
            on_done({"applied": True}, None)
        elif signal == "_get_charge_status":
            on_done({"charge_policy": -1, "charge_threshold": -1}, None)
        else:
            raise AssertionError(f"unexpected signal {signal!r}")
        return True

    def execute_method(self, name, **kwargs):
        return 0


class UtilCycleTests(unittest.TestCase):
    def setUp(self):
        # Hermetic at elevation: pin the real interface's start to False so
        # profile activation cannot drive a live cycle (see
        # test_gpu_feedforward for the failure mode).
        patcher = patch.object(wmi_module.WMIInterface, "start",
                               return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.mkdtemp(prefix="gigahelm-util-")
        self.state = AppState(path_manager=PathManager(
            base_dir=self.tmp, executable_path="python.exe", main_script_path=""))
        self.svc = AppServices(state=self.state)
        self.state.load_profiles_from_config(
            {"Game": {"fan_mode": "auto", "cpu_fan_table": CURVE}}, ["Game"])
        self.state.set_active_profile_name("Game")
        self.svc.wmi_interface = FakeWMI()
        self.svc._last_wmi_recovery_attempt = 0.0

    def _cycle(self):
        self.svc.controller_timer.stop()
        self.svc._perform_control_cycle()

    def test_util_defaults_to_unknown(self):
        self.assertEqual(self.state.get_gpu_util(), -1)

    def test_cycle_publishes_util(self):
        self.svc.wmi_interface.gpu_util = 37
        self._cycle()
        self.assertEqual(self.state.get_gpu_util(), 37)

    def test_missing_util_reports_unknown(self):
        self.svc.wmi_interface.gpu_util = 60
        self._cycle()
        self.svc.wmi_interface.gpu_util = None
        self._cycle()
        self.assertEqual(self.state.get_gpu_util(), -1)

    def test_asleep_cycle_reports_unknown(self):
        self.svc.wmi_interface.gpu_util = 60
        self._cycle()
        self.svc.wmi_interface.gpu_util = None
        self.svc.wmi_interface.gpu_temp = 0.0
        self._cycle()
        self.assertEqual(self.state.get_gpu_util(), -1)


def make_dashboard(tmp_name):
    state = AppState(path_manager=PathManager(
        base_dir=tmp_name, executable_path="python.exe", main_script_path=""))
    manager = ProfileManager(state)
    manager.load_config()
    settings = SettingsManager(state, manager)
    return state, Dashboard(state, manager, settings, ThemeManager(state, settings))


class GpuFooterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gigahelm-gpufooter-")
        self.state, self.dashboard = make_dashboard(self.tmp)

    def _footer(self):
        return self.dashboard.gpu_card.footer_label.text()

    def test_footer_composes_rpm_and_util(self):
        self.state.set_fan2_rpm(1500)
        self.state.set_gpu_util(42)
        self._footer_check = None
        self.dashboard._update_gpu_card_footer()
        self.assertEqual(self._footer(), f"1500{tr('rpm_unit')} · 42{tr('percent_unit')}")

    def test_footer_without_util_is_rpm_only(self):
        self.state.set_fan2_rpm(1500)
        self.dashboard._update_gpu_card_footer()
        self.assertEqual(self._footer(), f"1500{tr('rpm_unit')}")

    def test_asleep_footer_untouched(self):
        self.state.set_gpu_asleep(True)
        self.dashboard._update_gpu_card_footer()
        self.assertEqual(self._footer(), tr("stat_gpu_asleep"))

    def test_rpm_error_footer(self):
        self.state.set_fan2_rpm(RPM_READ_ERROR_VALUE)
        self.dashboard._update_gpu_card_footer()
        self.assertEqual(self._footer(), tr("rpm_error"))


if __name__ == "__main__":
    unittest.main()
