# -*- coding: utf-8 -*-
"""Acoustic target shaping (core/noise_shaping.py).

Pure-function cases pin the gates (upward-only, never wake a stopped fan,
guard overlays win because shaping runs after selection); cycle cases prove
the split path applies detune/assist before hysteresis and that disabling
the profile flags restores the pre-shaping duties byte-for-byte."""
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from core.fan_duty import percent_to_raw  # noqa: E402
from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.app_services import AppServices  # noqa: E402
from core.noise_shaping import anti_beat_detune, fan_load_assist  # noqa: E402
import core.wmi_interface as wmi_module  # noqa: E402
from config.settings import WMIInternalSignal  # noqa: E402

CURVE = [[40, 0], [50, 20], [60, 40], [70, 60]]


class AntiBeatDetuneTests(unittest.TestCase):
    def test_equal_floor_targets_get_gpu_lifted(self):
        # The silent-mode park: both channels on the 25 % floor.
        self.assertEqual(anti_beat_detune(25, 25), (25, 29))

    def test_floor_clamped_parks_detune_in_effective_space(self):
        # 1-25 % all write the same raw 57 park: equality and the raise
        # must happen in EC-effective space or the detune writes raw 57
        # again, i.e. nothing.
        self.assertEqual(anti_beat_detune(20, 20), (20, 29))
        self.assertEqual(anti_beat_detune(20, 24), (20, 29))
        self.assertEqual(anti_beat_detune(1, 1), (1, 29))

    def test_beat_zone_boundary(self):
        self.assertEqual(anti_beat_detune(27, 27), (27, 31))

    def test_self_separated_parks_not_detuned(self):
        # From raw 64 the fans sit ~250 Hz apart on their own; shifting one
        # would drag the pair back into the roughness band (sweep
        # 2026-11-18).
        self.assertEqual(anti_beat_detune(28, 28), (28, 28))
        self.assertEqual(anti_beat_detune(30, 30), (30, 30))
        self.assertEqual(anti_beat_detune(40, 40), (40, 40))

    def test_unequal_targets_untouched(self):
        self.assertEqual(anti_beat_detune(25, 0), (25, 0))
        self.assertEqual(anti_beat_detune(30, 27), (30, 27))
        self.assertEqual(anti_beat_detune(27, 30), (27, 30))

    def test_never_lowered(self):
        # A higher GPU target passes through; detune only ever raises.
        self.assertEqual(anti_beat_detune(25, 40), (25, 40))

    def test_detune_must_exceed_hysteresis_band(self):
        # band is 2 %; a 1-point offset would be swallowed by next_target.
        self.assertEqual(anti_beat_detune(25, 25, detune=1), (25, 26))


class FanLoadAssistTests(unittest.TestCase):
    def test_cpu_hot_lifts_spinning_gpu_channel(self):
        self.assertEqual(fan_load_assist(45, 27), (45, 35))

    def test_gpu_hot_lifts_spinning_cpu_channel(self):
        self.assertEqual(fan_load_assist(27, 45), (35, 45))

    def test_stopped_cool_channel_stays_stopped(self):
        # Waking a stopped fan fires the 57-raw start surge.
        self.assertEqual(fan_load_assist(45, 0), (45, 0))

    def test_below_trigger_spread_untouched(self):
        self.assertEqual(fan_load_assist(35, 27), (35, 27))

    def test_margin_caps_rise_to_hot_margin(self):
        # hot - margin == cool -> nothing to assist.
        self.assertEqual(fan_load_assist(35, 25), (35, 25))

    def test_max_rise_cap(self):
        self.assertEqual(fan_load_assist(60, 25), (60, 33))

    def test_max_target_cap(self):
        self.assertEqual(fan_load_assist(52, 25), (52, 33))

    def test_equal_targets_untouched(self):
        self.assertEqual(fan_load_assist(30, 30), (30, 30))


class FakeWMI:
    """Cycle double with a scriptable sensor reading."""

    def __init__(self, cpu_temp=55.0, gpu_temp=40.0, gpu_asleep=False):
        self.running = True
        self.cpu_temp = cpu_temp
        self.gpu_temp = gpu_temp
        self.gpu_asleep = gpu_asleep
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
        return 0.0

    def submit_async(self, signal, params=None, on_done=None):
        if signal == "_get_core_sensors":
            on_done({"cpu_temp": self.cpu_temp, "gpu_temp": self.gpu_temp,
                     "gpu_asleep": self.gpu_asleep,
                     "fan1_rpm": 1200, "fan2_rpm": 1200,
                     "gpu_power_w": None}, None)
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


def run_one_cycle(svc):
    svc._last_wmi_recovery_attempt = 0.0
    svc.controller_timer.stop()
    svc._recover_wmi_if_due()


def make_cycle_service(case, profile_config):
    patcher = patch.object(wmi_module.WMIInterface, "start",
                           return_value=False)
    patcher.start()
    case.addCleanup(patcher.stop)
    tmp = tempfile.mkdtemp(prefix="gigahelm-noise-")
    state = AppState(path_manager=PathManager(
        base_dir=tmp, executable_path="python.exe", main_script_path=""))
    svc = AppServices(state=state)
    state.load_profiles_from_config({"Game": profile_config}, ["Game"])
    state.set_active_profile_name("Game")
    return state, svc


class ShapingCycleTests(unittest.TestCase):
    def _make_service(self, profile_config):
        return make_cycle_service(self, profile_config)

    def _run_cycle(self, svc):
        run_one_cycle(svc)

    def _profile(self, **overrides):
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE,
               "gpu_fan_table": CURVE, "gpu_power_feedforward": False,
               "split_fan_control": True}
        cfg.update(overrides)
        return cfg

    def test_equal_targets_detuned_in_cycle(self):
        # cpu 50 degC -> 20 %, gpu 50 degC -> 20 %: both map to the raw 57
        # park, so the cycle must raise the GPU channel to effective 29
        # (raw 66). Two cycles: the first is the staggered start surge,
        # the second runs the shaped steady state.
        state, svc = self._make_service(self._profile())
        svc.wmi_interface = FakeWMI(cpu_temp=50.0, gpu_temp=50.0)
        self._run_cycle(svc)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"], float(percent_to_raw(20)))
        self.assertEqual(apply_params["gpu_duty_raw"],
                         float(percent_to_raw(29)))

    def test_detune_flag_off_restores_identical_duties(self):
        state, svc = self._make_service(
            self._profile(anti_beat_detune=False))
        svc.wmi_interface = FakeWMI(cpu_temp=50.0, gpu_temp=50.0)
        self._run_cycle(svc)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"],
                         apply_params["gpu_duty_raw"])
        self.assertEqual(apply_params["gpu_duty_raw"],
                         float(percent_to_raw(20)))

    def test_one_sided_burst_gets_assisted(self):
        # cpu 70 degC -> 60 %, gpu 55 degC -> 30 %: the cool spinning GPU
        # channel is lifted toward hot - margin (50), capped at +8 (38).
        state, svc = self._make_service(self._profile())
        svc.wmi_interface = FakeWMI(cpu_temp=70.0, gpu_temp=55.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"],
                         float(percent_to_raw(60)))
        self.assertEqual(apply_params["gpu_duty_raw"],
                         float(percent_to_raw(38)))

    def test_assist_flag_off_keeps_curve_duties(self):
        state, svc = self._make_service(
            self._profile(fan_load_assist=False, anti_beat_detune=False))
        svc.wmi_interface = FakeWMI(cpu_temp=70.0, gpu_temp=55.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["gpu_duty_raw"],
                         float(percent_to_raw(30)))

    def test_asleep_gpu_channel_stays_zero(self):
        state, svc = self._make_service(self._profile())
        svc.wmi_interface = FakeWMI(cpu_temp=70.0, gpu_temp=-1.0,
                                    gpu_asleep=True)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["gpu_duty_raw"], 0.0)

    def test_mirrored_mode_not_shaped(self):
        state, svc = self._make_service(
            self._profile(split_fan_control=False))
        svc.wmi_interface = FakeWMI(cpu_temp=60.0, gpu_temp=60.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"],
                         apply_params["gpu_duty_raw"])


if __name__ == "__main__":
    unittest.main()
