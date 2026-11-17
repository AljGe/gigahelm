# -*- coding: utf-8 -*-
"""NVML GPU power feed-forward: offset math + cycle integration.

The offset lifts the GPU curve input while heat is still travelling to the
heatsink; unknown power (asleep dGPU, NVML absent, read error) must never
boost, and the per-profile toggle must turn the whole channel off."""
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

from core.gpu_power import feedforward_offset  # noqa: E402
from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.app_services import AppServices  # noqa: E402
import core.wmi_interface as wmi_module  # noqa: E402
from config.settings import (  # noqa: E402
    NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C,
)
from config.settings import WMIInternalSignal  # noqa: E402

CURVE = [[40, 0], [50, 20], [60, 40], [70, 60]]


class FeedforwardOffsetTests(unittest.TestCase):
    def test_unknown_power_never_boosts(self):
        self.assertEqual(feedforward_offset(None, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 0.0)
        self.assertEqual(feedforward_offset("garbage", NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 0.0)

    def test_below_idle_never_boosts(self):
        self.assertEqual(feedforward_offset(30.0, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 0.0)
        self.assertEqual(feedforward_offset(10.0, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 0.0)

    def test_linear_region_and_clamp(self):
        # (80 - 30) * 0.4 = 20 -> clamped at 10
        self.assertEqual(feedforward_offset(80.0, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 10.0)
        # (50 - 30) * 0.4 = 8 -> unclamped linear region
        self.assertEqual(feedforward_offset(50.0, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W, NVML_FF_MAX_BOOST_C), 8.0)


class FakeWMI:
    """Cycle double with a scriptable NVML power reading."""

    def __init__(self, gpu_power_w=None):
        self.running = True
        self.gpu_power_w = gpu_power_w
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

    def ff_max_boost_cap(self) -> float:
        return NVML_FF_MAX_BOOST_C

    def submit_async(self, signal, params=None, on_done=None):
        if signal == "_get_core_sensors":
            on_done({"cpu_temp": 45.0, "gpu_temp": 45.0, "gpu_asleep": False,
                     "fan1_rpm": 1200, "fan2_rpm": 1200,
                     "gpu_power_w": self.gpu_power_w}, None)
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


class FeedforwardCycleTests(unittest.TestCase):
    def _make_service(self, profile_config):
        # Hermetic at elevation: without this, profile activation drives one
        # live cycle through the REAL WMI worker (it starts successfully when
        # EC WMI is available), which latches _cycle_in_flight forever - the
        # result drain needs a Qt event loop - and every later fake-driven
        # cycle is silently skipped.
        patcher = patch.object(wmi_module.WMIInterface, "start",
                               return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.mkdtemp(prefix="gigahelm-ff-")
        state = AppState(path_manager=PathManager(
            base_dir=tmp, executable_path="python.exe", main_script_path=""))
        svc = AppServices(state=state)
        state.load_profiles_from_config({"Game": profile_config}, ["Game"])
        state.set_active_profile_name("Game")
        return state, svc

    def test_power_rises_curve_target(self):
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": True}
        state_base, svc_base = self._make_service(dict(cfg))
        state_ff, svc_ff = self._make_service(dict(cfg))

        svc_base.wmi_interface = FakeWMI(gpu_power_w=None)
        svc_base._last_wmi_recovery_attempt = 0.0
        svc_base.controller_timer.stop()
        svc_base._recover_wmi_if_due()
        base_target = state_base.get_auto_fan_target_speed_percent()

        svc_ff.wmi_interface = FakeWMI(gpu_power_w=80.0)
        svc_ff._last_wmi_recovery_attempt = 0.0
        svc_ff.controller_timer.stop()
        svc_ff._recover_wmi_if_due()
        ff_target = state_ff.get_auto_fan_target_speed_percent()

        # baseline: curve(45) = 10 %; with feed-forward: curve(55) = 30 %
        self.assertEqual(base_target, 10)
        self.assertEqual(ff_target, 30)

    def test_toggle_off_disables_channel(self):
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False}
        state, svc = self._make_service(cfg)
        svc.wmi_interface = FakeWMI(gpu_power_w=80.0)
        svc._last_wmi_recovery_attempt = 0.0
        svc.controller_timer.stop()
        svc._recover_wmi_if_due()
        self.assertEqual(state.get_auto_fan_target_speed_percent(), 10)

    def test_asleep_gpu_ignores_power(self):
        # NVML reads fail when the dGPU sleeps, so this combination is
        # defensive - but the offset must never apply to an error temperature.
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": True}

        class SleepingWMI(FakeWMI):
            def submit_async(self, signal, params=None, on_done=None):
                if signal == "_get_core_sensors":
                    on_done({"cpu_temp": 45.0, "gpu_temp": -1.0, "gpu_asleep": True,
                             "fan1_rpm": 1200, "fan2_rpm": 1200,
                             "gpu_power_w": 80.0}, None)
                else:
                    super().submit_async(signal, params, on_done)

        state, svc = self._make_service(cfg)
        svc.wmi_interface = SleepingWMI(gpu_power_w=80.0)
        svc._last_wmi_recovery_attempt = 0.0
        svc.controller_timer.stop()
        svc._recover_wmi_if_due()
        self.assertEqual(state.get_auto_fan_target_speed_percent(), 10)


if __name__ == "__main__":
    unittest.main()
