# -*- coding: utf-8 -*-
"""Split fan control: each EC channel gets its own duty.

Split ON proves the headline behavior — a cool GPU leaves the GPU channel at
raw 0 while the CPU channel follows the CPU curve — and split OFF must keep
the mirrored one-duty-for-both-channels behavior byte-identical."""
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
import core.wmi_interface as wmi_module  # noqa: E402
from config.settings import WMIInternalSignal  # noqa: E402

CURVE = [[40, 0], [50, 20], [60, 40], [70, 60]]


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
    """Build an AppServices with a profile loaded, hermetic at elevation:
    see tests/test_gpu_feedforward.py — without the WMIInterface.start patch,
    profile activation drives one live cycle through the REAL WMI worker and
    latches _cycle_in_flight forever."""
    patcher = patch.object(wmi_module.WMIInterface, "start",
                           return_value=False)
    patcher.start()
    case.addCleanup(patcher.stop)
    tmp = tempfile.mkdtemp(prefix="gigahelm-split-")
    state = AppState(path_manager=PathManager(
        base_dir=tmp, executable_path="python.exe", main_script_path=""))
    svc = AppServices(state=state)
    state.load_profiles_from_config({"Game": profile_config}, ["Game"])
    state.set_active_profile_name("Game")
    return state, svc


class SplitFanCycleTests(unittest.TestCase):
    def _make_service(self, profile_config):
        return make_cycle_service(self, profile_config)

    def _run_cycle(self, svc):
        run_one_cycle(svc)

    def test_split_on_cool_gpu_keeps_gpu_channel_off(self):
        # cpu 55 degC -> CPU curve 30 %; gpu 40 degC -> GPU curve 0 %.
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False, "split_fan_control": True}
        state, svc = self._make_service(dict(cfg))
        svc.wmi_interface = FakeWMI(cpu_temp=55.0, gpu_temp=40.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"], float(percent_to_raw(30)))
        self.assertEqual(apply_params["gpu_duty_raw"], 0.0)
        self.assertEqual(state.get_applied_gpu_fan_percent(), 0)

    def test_split_off_mirrors_one_duty_to_both_channels(self):
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False, "split_fan_control": False}
        state, svc = self._make_service(dict(cfg))
        svc.wmi_interface = FakeWMI(cpu_temp=55.0, gpu_temp=40.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"],
                         apply_params["gpu_duty_raw"])
        self.assertGreater(apply_params["cpu_duty_raw"], 0.0)
        self.assertEqual(
            apply_params["cpu_duty_raw"], float(percent_to_raw(30)))

    def test_split_on_hot_gpu_channel_follows_gpu_curve(self):
        # cpu 60 degC -> 40 %; gpu 70 degC -> 60 %: both spin, at own duties.
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False, "split_fan_control": True}
        state, svc = self._make_service(dict(cfg))
        svc.wmi_interface = FakeWMI(cpu_temp=60.0, gpu_temp=70.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"], float(percent_to_raw(40)))
        self.assertEqual(apply_params["gpu_duty_raw"], float(percent_to_raw(60)))
        self.assertNotEqual(apply_params["cpu_duty_raw"],
                            apply_params["gpu_duty_raw"])

    def test_split_on_asleep_gpu_keeps_gpu_channel_off(self):
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False, "split_fan_control": True}
        state, svc = self._make_service(dict(cfg))
        svc.wmi_interface = FakeWMI(cpu_temp=55.0, gpu_temp=-1.0, gpu_asleep=True)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"], float(percent_to_raw(30)))
        self.assertEqual(apply_params["gpu_duty_raw"], 0.0)

    def test_split_default_on_for_legacy_configs(self):
        # A config stored before the key existed gets the shipped default
        # (split ON) via DEFAULT_PROFILE_SETTINGS / inline default.
        cfg = {"fan_mode": "auto", "cpu_fan_table": CURVE, "gpu_fan_table": CURVE,
               "gpu_power_feedforward": False}
        state, svc = self._make_service(dict(cfg))
        svc.wmi_interface = FakeWMI(cpu_temp=55.0, gpu_temp=40.0)
        self._run_cycle(svc)
        apply_params = svc.wmi_interface.applies[-1]
        self.assertEqual(apply_params["cpu_duty_raw"], float(percent_to_raw(30)))
        self.assertEqual(apply_params["gpu_duty_raw"], 0.0)


class FanRpmPairingTests(unittest.TestCase):
    """The split A/B bench (2026-11-17) proved the EC's fan channels are
    crossed relative to their duty methods: SET_GPU_FAN_DUTY drives the fan
    reporting as getRpm1, SET_CUSTOM_FAN_SPEED drives getRpm2. The sensor
    sweep must mirror the readbacks so fan1_rpm stays 'the fan the CPU duty
    commands' - a revert here silently breaks stall detection (false
    CPU-stall alerts whenever the split GPU channel sits at 0)."""

    def test_core_sensors_mirror_rpm_readbacks(self):
        from core.wmi_interface import WMIWorker

        scripted = {
            "getCpuTemp": 45.0,
            "getGpuTemp1": 50.0, "getGpuTemp2": 50.0,
            "getRpm1": 1111.0, "getRpm2": 2222.0,
            "GetFixedFanSpeed": 57.0,
            "GetCPUFanDuty": 57.0, "GetGPUFanDuty": 57.0,
        }

        class _Obj:
            """Namespace exposing every scripted getter as a method."""
            def __getattr__(self, name):
                if name in scripted:
                    return lambda **kwargs: scripted[name]
                raise AttributeError(name)

        import queue as _queue
        worker = WMIWorker(_queue.Queue(), "MockClass", "MockClass")
        worker._wmi_get_obj = _Obj()
        worker._wmi_set_obj = _Obj()
        worker._nvml_ok = False
        worker._execute_wmi_method = lambda obj, name, **kwargs: scripted[name]
        data = worker._get_core_sensors()
        # getRpm1 answers the GPU duty channel -> fan2; getRpm2 answers the
        # CPU duty channel -> fan1.
        self.assertEqual(data["fan1_rpm"], 2222)
        self.assertEqual(data["fan2_rpm"], 1111)


class RampShapingCycleTests(unittest.TestCase):
    """Auto-mode ramp shaping on a continuous control session: curve rises
    glide UP_STEP_PERCENT per tick, guard floors bypass the glide, and
    same-tick start surges stagger one channel apart.  First cycles after a
    reset (profile/mode switch, resume) keep the immediate jump - covered by
    the tests above, which all assert full targets on cycle one."""

    AUTO_CFG = {"fan_mode": "auto", "cpu_fan_table": CURVE,
                "gpu_fan_table": CURVE, "gpu_power_feedforward": False,
                "split_fan_control": True}

    def _establish(self, cpu_temp, gpu_temp):
        state, svc = make_cycle_service(self, dict(self.AUTO_CFG))
        wmi = FakeWMI(cpu_temp=cpu_temp, gpu_temp=gpu_temp)
        svc.wmi_interface = wmi
        run_one_cycle(svc)  # fresh session: jumps to the curve target
        return state, svc, wmi

    def test_curve_rise_glides_after_session_established(self):
        # 55 degC -> 30 %; then 75 degC: EMA 65 -> curve 50, glide caps the
        # rise at 30 + 10; the next EMA step carries the glide onward.
        state, svc, wmi = self._establish(55.0, 40.0)
        self.assertEqual(state.get_applied_fan_speed_percent(), 30)
        wmi.cpu_temp = 75.0
        run_one_cycle(svc)
        self.assertEqual(state.get_applied_fan_speed_percent(), 40)
        run_one_cycle(svc)
        self.assertEqual(state.get_applied_fan_speed_percent(), 50)

    def test_guard_floor_bypasses_glide(self):
        # Continuous session at 30 %; raw 91 degC engages the 80 % floor and
        # the channel must jump in one tick, not glide.
        state, svc, wmi = self._establish(55.0, 40.0)
        wmi.cpu_temp = 91.0
        run_one_cycle(svc)
        self.assertEqual(state.get_applied_fan_speed_percent(), 80)
        self.assertEqual(
            svc.wmi_interface.applies[-1]["cpu_duty_raw"],
            float(percent_to_raw(80)))

    def test_same_tick_start_surge_staggers(self):
        # Both channels off (curve zero on both dies), then both cross their
        # knees in the same tick: CPU leads, GPU starts one cycle later.
        state, svc, wmi = self._establish(40.0, 40.0)
        self.assertEqual(state.get_applied_fan_speed_percent(), 0)
        self.assertEqual(state.get_applied_gpu_fan_percent(), 0)
        wmi.cpu_temp = 70.0
        wmi.gpu_temp = 70.0
        run_one_cycle(svc)
        lead = svc.wmi_interface.applies[-1]
        self.assertEqual(lead["cpu_duty_raw"], float(percent_to_raw(10)))
        self.assertEqual(lead["gpu_duty_raw"], 0.0)
        run_one_cycle(svc)
        follow = svc.wmi_interface.applies[-1]
        self.assertEqual(follow["gpu_duty_raw"], float(percent_to_raw(10)))


if __name__ == "__main__":
    unittest.main()
