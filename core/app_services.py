# -*- coding: utf-8 -*-
"""
提供一个中心化的服务层(AppServices)，将后端逻辑与UI解耦。
增加了优雅退出逻辑，在程序关闭时将风扇交还BIOS控制，并为崩溃安全机制记录当前风扇模式。
"""

import os
import sys
import time
from typing import Optional, List, Any, Callable, Dict

from gui.qt import QObject, Slot, QTimer

from .wmi_interface import WMIInterface, WMIError
from .auto_temp_controller import AutoTemperatureController
from .state import AppState, ProfileState
from .fan_duty import percent_to_raw, effective_percent
from .fan_ramp import next_fan_percent, next_target
from .fan_target import guard_floor, select_fan_target
from .thermal_guard import ThermalGuardWatchdog
from .gpu_power import feedforward_offset
from tools.localization import tr
from config.settings import (
    FAN_MODE_BIOS, FAN_MODE_AUTO, FAN_MODE_CUSTOM, FAN_MODE_UNKNOWN,
    CHARGE_POLICY_CUSTOM, BATTERY_POLICY_CODES, BATTERY_CODE_POLICIES,
    CHARGE_POLICY_READ_ERROR_VALUE, CHARGE_THRESHOLD_READ_ERROR_VALUE,
    CHARGE_WATCH_INTERVAL_MS,
    INIT_APPLIED_PERCENTAGE, TEMP_READ_ERROR_VALUE, RPM_READ_ERROR_VALUE,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_STATUS, SET_CHARGE_STOP,
    DEFAULT_TARGET_HYSTERESIS_PERCENT, FAN_STALL_CYCLES, TEMP_SMOOTH_ALPHA,
    NVML_FF_IDLE_W, NVML_FF_K_C_PER_W,
    HOT_ALERT_TEMP_C, HOT_ALERT_RESET_C, HOT_ALERT_SUSTAINED_CYCLES,
    LAST_MODE_FILENAME, HEARTBEAT_FILENAME, WMIInternalSignal
)

# H4 keepalive: reassert the software-control EC flags at least this often (cycles).
FLAG_REASSERT_EVERY_CYCLES = 8
# H3 safety: a control cycle that takes longer than this means the worker is hung.
CYCLE_WATCHDOG_MS = 8000

_MODE_LABEL_KEYS = {
    FAN_MODE_AUTO: "mode_auto", FAN_MODE_CUSTOM: "mode_custom", FAN_MODE_BIOS: "mode_bios",
}


def _mode_label(mode: str) -> str:
    key = _MODE_LABEL_KEYS.get(mode)
    return tr(key) if key else mode

FanTable = List[List[int]]

class AppServices(QObject):
    """中心化服务层，管理所有后端控制器并响应应用状态的变化。"""

    def __init__(self, state: AppState, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._is_shutting_down = False
        self._emergency_latched = False
        self.state = state
        self._current_profile: Optional[ProfileState] = None
        self.wmi_interface = WMIInterface()
        self.auto_temp_controller = AutoTemperatureController()
        # Static setter map for _update_state_from_sensor_data: replaces the
        # per-tick hasattr/getattr reflection walk. Keys without a matching
        # AppState setter (gpu_power_w, raw EC status getters) were skipped
        # before and stay absent here.
        self._sensor_setters = {
            'cpu_temp': state.set_cpu_temp, 'gpu_temp': state.set_gpu_temp,
            'gpu_asleep': state.set_gpu_asleep, 'gpu_util': state.set_gpu_util,
            'fan1_rpm': state.set_fan1_rpm, 'fan2_rpm': state.set_fan2_rpm,
            'ec_fixed_fan_speed_raw': state.set_ec_fixed_fan_speed_raw,
            'battery_health': state.set_battery_health, 'battery_cycles': state.set_battery_cycles,
            'battery_percent': state.set_battery_percent,
            'charge_policy': state.set_applied_charge_policy,
            'charge_threshold': state.set_applied_charge_threshold,
        }

        self.controller_timer = QTimer(self)
        self.controller_timer.timeout.connect(self._perform_control_cycle)
        self._last_wmi_recovery_attempt: float = 0.0
        self._cycle_in_flight = False
        self._cycles_since_flag_reassert = 0
        self._pending_cycle: Optional[Dict[str, int]] = None
        self._mode_generation = 0
        self._cycle_generation = 0
        self._auto_target: Optional[int] = None
        # Consecutive zero raw targets (fan_ramp.next_target zero-release
        # debounce); EMA-smoothed control temps; engaged thermal-guard floors
        # per sensor (fan_target.guard_floor engage/release hysteresis).
        self._zero_streak = 0
        self._cpu_ctrl_temp: Optional[float] = None
        self._gpu_ctrl_temp: Optional[float] = None
        self._cpu_guard_floor = 0
        self._gpu_guard_floor = 0
        self._fan1_stall_cycles = 0
        self._fan2_stall_cycles = 0
        self._fan_stall_reported = False
        self._last_stall_message = ""
        self._cycle_watchdog = QTimer(self)
        self._cycle_watchdog.setSingleShot(True)
        self._cycle_watchdog.timeout.connect(self._on_cycle_watchdog)
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self.recover_after_resume)
        # Charge watch: periodically re-read the EC's charge policy/threshold
        # and rewrite the profile's values when firmware drifted (sleep, EC
        # reset, battery pull). Without this, a dropped charge limit silently
        # lets the battery charge to 100 %.
        self._charge_watch_timer = QTimer(self)
        self._charge_watch_timer.timeout.connect(self._verify_charge_state)
        self._charge_watch_timer.start(CHARGE_WATCH_INTERVAL_MS)
        self._charge_verify_in_flight = False
        self._last_charge_write_ms = 0.0
        # Visibility-independent WMI watchdog: on_gui_tick is the other recovery
        # driver and it stops while the window is hidden, so a failed
        # recover_after_resume() would leave the control loop dead in the tray
        # (fans latched at the last duty) until the window is shown.
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(15000)
        self._recovery_timer.timeout.connect(self._recover_if_dead)
        self._recovery_timer.start()
        # Sustained-hot balloon state (see _update_hot_alert).
        self._hot_alert_cycles = 0
        self._hot_alert_active = False

        self.state.set_controller_status_message(tr("initializing"))
        self._is_ui_visible: bool = False

        # 为崩溃安全机制/外部看门狗定义状态文件路径
        self._last_mode_file_path = os.path.join(self.state.paths.base_dir, LAST_MODE_FILENAME)
        self._heartbeat_file_path = os.path.join(self.state.paths.base_dir, HEARTBEAT_FILENAME)
        # A crash leaves last_mode=emergency and the EC at 80%.  Do not
        # auto-apply the profile's quiet curve if Task Scheduler relaunched us.
        self._suppress_profile_fan_mode = self._read_last_mode_file() == "emergency"
        self._last_heartbeat_ms: float = 0.0
        self._write_heartbeat(force=True)

        # Independent EC-level fail-safe: forces the guard floor duty when the
        # CPU crosses a thermal threshold and the EC sits below it, even if
        # this service's control cycles are stalled (see core/thermal_guard).
        self._thermal_guard = ThermalGuardWatchdog(self.state.paths.base_dir)
        self._thermal_guard.start()

        self._connect_to_state_signals()

    def _connect_to_state_signals(self):
        self.state.active_profile_changed.connect(self._on_active_profile_changed)

    @Slot(ProfileState)
    def _on_active_profile_changed(self, profile: Optional[ProfileState]):
        if self._current_profile:
            try:
                self._current_profile.fan_mode_changed.disconnect(self.set_fan_mode)
                self._current_profile.custom_fan_speed_changed.disconnect(self.set_custom_fan_speed)
                self._current_profile.battery_charge_policy_changed.disconnect(self.set_battery_charge_policy)
                self._current_profile.battery_charge_threshold_changed.disconnect(self.set_battery_charge_threshold)
                self._current_profile.cpu_fan_table_changed.disconnect(self._update_controller_curves)
                self._current_profile.gpu_fan_table_changed.disconnect(self._update_controller_curves)
                self._current_profile.appearance_changed.disconnect(self._update_controller_settings)
            except (AttributeError, RuntimeError): pass

        self._current_profile = profile
        self._auto_target = None
        self._reset_stall_state()
        if not profile:
            self.controller_timer.stop()
            return

        profile.fan_mode_changed.connect(self.set_fan_mode)
        profile.custom_fan_speed_changed.connect(self.set_custom_fan_speed)
        profile.battery_charge_policy_changed.connect(self.set_battery_charge_policy)
        profile.battery_charge_threshold_changed.connect(self.set_battery_charge_threshold)
        profile.cpu_fan_table_changed.connect(self._update_controller_curves)
        profile.gpu_fan_table_changed.connect(self._update_controller_curves)
        profile.appearance_changed.connect(self._update_controller_settings)

        self._update_controller_curves()
        self._update_controller_settings()
        if self._suppress_profile_fan_mode:
            self._suppress_profile_fan_mode = False
            self.state.set_applied_fan_mode(FAN_MODE_UNKNOWN)
            self.state.set_is_fan_control_panel_enabled(True)
            self.state.set_controller_status_message(tr("emergency_recovered"))
        else:
            self.set_fan_mode(profile.get_fan_mode())
        self.set_battery_charge_policy(profile.get_battery_charge_policy())

    def _update_controller_curves(self):
        if self._current_profile:
            self.auto_temp_controller.update_curves(
                self._current_profile.get_cpu_fan_table(),
                self._current_profile.get_gpu_fan_table()
            )
            self._auto_target = None

    def _update_controller_settings(self):
        if self._current_profile:
            controller_interval = self._current_profile.get_value('controller_update_interval_ms')
            if self.controller_timer.interval() != controller_interval:
                self.controller_timer.setInterval(controller_interval)
            # Mirror the NVML poll gate outside control cycles: this runs on
            # profile activation and appearance_changed, so BIOS mode and
            # pre-first-cycle states also drop a stale/default poll gate.
            self.wmi_interface.set_nvml_poll_enabled(
                bool(self._current_profile.get_value("gpu_power_feedforward", True)))

    def initialize_wmi(self) -> bool:
        if not self.wmi_interface.start():
            init_error = self.wmi_interface.get_initialization_error()
            error_msg = tr("wmi_init_error_msg", error=str(init_error))
            self.state.set_controller_status_message(error_msg)
            return False
        self.state.set_controller_status_message("")
        return True

    def shutdown(self):
        """执行优雅关机程序。崩溃紧急锁存后不再把风扇交还 BIOS。"""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.controller_timer.stop()
        self._cycle_watchdog.stop()
        self._thermal_guard.stop(join_s=3.0)
        self._cycle_in_flight = False
        self._cycle_generation += 1
        self._pending_cycle = None
        if self._resume_timer.isActive():
            self._resume_timer.stop()
        self._charge_watch_timer.stop()
        self._recovery_timer.stop()
        if self.state.get_applied_fan_mode() != FAN_MODE_BIOS and not self._fan_emergency_latched():
            print("Graceful exit: handing fan control back to BIOS...")
            try:
                self._configure_bios_fan_control()
                self._write_last_mode_state(FAN_MODE_BIOS)
                print("Graceful exit: fan mode set to BIOS.")
            except WMIError as e:
                # Leave last_mode as auto/custom: the external watchdog will
                # retry once this process is gone.
                print(f"Graceful exit: failed to set BIOS fan mode: {e}", file=sys.stderr)
        if self.wmi_interface:
            self.wmi_interface.stop(join_timeout=2.0)

    def _configure_bios_fan_control(self):
        """阻塞式 BIOS 移交，仅在退出路径使用。"""
        self.wmi_interface.execute_method(SET_AUTO_FAN_STATUS, Data=1.0)
        self.wmi_interface.execute_method(SET_CUSTOM_FAN_STATUS, Data=0.0)

    def _request_bios_control(self, generation: Optional[int] = None,
                              on_done: Optional[Callable[[bool], None]] = None,
                              fallback_mode: Optional[str] = None):
        """异步 BIOS 移交；成功后才写 last_mode=bios。"""
        def _finished(result: Any, exc: Optional[Exception]):
            if generation is not None and generation != self._mode_generation:
                return  # a newer mode request superseded this one
            if exc is not None:
                print(f"WMI operation failed while handing fans to BIOS: {exc}", file=sys.stderr)
                self.state.set_controller_status_message(
                    tr("fan_mode_apply_failed", mode=_mode_label(FAN_MODE_BIOS)))
                if fallback_mode in (FAN_MODE_AUTO, FAN_MODE_CUSTOM):
                    # Keep the EC under the previous curve instead of leaving
                    # it untouched until the external watchdog fires.
                    self.state.set_applied_fan_mode(fallback_mode)
                    self.state.set_is_fan_control_panel_enabled(True)
                    if not self.controller_timer.isActive():
                        self.controller_timer.start()
                    self._perform_control_cycle()
                else:
                    self.state.set_applied_fan_mode(FAN_MODE_UNKNOWN)
                    self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)
                if on_done:
                    on_done(False)
                return
            self.state.set_applied_fan_mode(FAN_MODE_BIOS)
            self.state.set_is_fan_control_panel_enabled(False)
            self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)
            self._write_last_mode_state(FAN_MODE_BIOS)
            if on_done:
                on_done(True)

        self.wmi_interface.submit_async(WMIInternalSignal.APPLY_BIOS_CONTROL, on_done=_finished)

    def mark_emergency_latched(self):
        self._emergency_latched = True

    def _fan_emergency_latched(self) -> bool:
        if self._emergency_latched:
            return True
        for name in ("__main__", "main"):
            mod = sys.modules.get(name)
            if mod is not None and getattr(mod, "_emergency_latched", False):
                return True
        return False

    def _reset_stall_state(self):
        self._fan1_stall_cycles = 0
        self._fan2_stall_cycles = 0
        if self._fan_stall_reported and self.state.get_controller_status_message() == self._last_stall_message:
            self.state.set_controller_status_message("")
        self._fan_stall_reported = False
        self._last_stall_message = ""

    def note_resume(self):
        """把连续的唤醒广播折叠成一次恢复。"""
        if self._is_shutting_down:
            return
        self._resume_timer.start(1500)

    def _recover_wmi_if_due(self):
        """Worker线程死亡时尝试重建WMI；30秒节流，失败时上报wmi_error。"""
        now = time.monotonic()
        if now - self._last_wmi_recovery_attempt < 30.0:
            return
        self._last_wmi_recovery_attempt = now
        self._log_control_event("wmi recovery attempt")
        try:
            self.wmi_interface.stop(join_timeout=1.0)
        except Exception:
            pass
        if self.wmi_interface.start(init_timeout=3.0):
            self._log_control_event("wmi recovery ok")
            self.state.set_controller_status_message("")
            self._auto_target = None
            profile = self._current_profile
            if profile and self.state.get_applied_fan_mode() in (FAN_MODE_AUTO, FAN_MODE_CUSTOM):
                # recover_after_resume() stops the timer and returns early when
                # the first rebuild after wake fails; this recovery is then the
                # only chance to bring the loop back, so the timer must be
                # restarted here too - otherwise the EC sits at the last duty
                # until the user touches fan mode.
                if not self.controller_timer.isActive():
                    self.controller_timer.start()
                self._perform_control_cycle()
        else:
            self._log_control_event("wmi recovery failed")
            self.state.set_controller_status_message(tr("wmi_error"))

    @Slot()
    def _recover_if_dead(self):
        if self._is_shutting_down or self.wmi_interface.is_running:
            return
        self._recover_wmi_if_due()

    def recover_after_resume(self):
        """重建 WMI。自动模式在恢复后立刻写一次风扇速度。"""
        if self._is_shutting_down:
            return
        self.controller_timer.stop()
        self.wmi_interface.stop(join_timeout=2.0)
        if not self.wmi_interface.start(init_timeout=5.0):
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        profile = self._current_profile
        if not profile:
            return
        self._auto_target = None
        mode = profile.get_fan_mode()
        self.set_fan_mode(mode)
        if mode == FAN_MODE_AUTO:
            self._perform_control_cycle()
        # The EC can drop its charge policy/threshold across sleep (or the
        # battery was pulled): verify and rewrite on drift.
        self._verify_charge_state()

    @Slot(bool)
    def on_power_source_changed(self, on_ac: bool):
        """AC attach/detach: switch profiles when the user bound one to this
        power source, and re-verify the charge state either way (the EC can
        drop its charge setting across power transitions)."""
        if self._is_shutting_down:
            return
        binding = self.state.get_profile_on_ac() if on_ac else self.state.get_profile_on_battery()
        if binding and binding != self.state.get_active_profile_name() and binding in self.state.profile_order:
            self.state.set_active_profile_name(binding)
        self._verify_charge_state()

    def _read_last_mode_file(self) -> str:
        try:
            with open(self._last_mode_file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _write_last_mode_state(self, mode: str):
        """将当前风扇模式写入状态文件，用于崩溃恢复。"""
        try:
            with open(self._last_mode_file_path, 'w', encoding='ascii') as f:
                f.write(mode)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            print(f"Warning: could not write {LAST_MODE_FILENAME}: {e}", file=sys.stderr)

    #: Rotate control_loop.log to .old once it exceeds this size.
    CONTROL_LOG_MAX_BYTES = 262144

    def _log_control_event(self, message: str) -> None:
        """Cycle-failure / recovery trace in the data home.

        Healthy loops never write here: an empty or missing control_loop.log
        means the WMI path has been clean since install. Written from GUI-
        thread slots only (single writer, no lock needed).
        """
        try:
            path = os.path.join(self.state.paths.base_dir, "control_loop.log")
            try:
                if os.path.exists(path) and os.path.getsize(path) > self.CONTROL_LOG_MAX_BYTES:
                    os.replace(path, path + ".old")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}\n")
        except OSError:
            pass

    def _write_heartbeat(self, force: bool = False):
        """Mtime is the liveness signal read by the external watchdog.
        Cycle-path writes are batched to at most one per 5 s — still an
        order of magnitude under WATCHDOG_HEARTBEAT_STALE_S (45 s)."""
        now = time.monotonic()
        if not force and now - self._last_heartbeat_ms < 5.0:
            return
        self._last_heartbeat_ms = now
        try:
            if not os.path.exists(self._heartbeat_file_path):
                with open(self._heartbeat_file_path, 'w', encoding='ascii') as f:
                    f.write(f"{os.getpid()}\n")
            else:
                os.utime(self._heartbeat_file_path, None)
        except OSError as e:
            print(f"Warning: could not update {HEARTBEAT_FILENAME}: {e}", file=sys.stderr)

    @Slot(str)
    def set_fan_mode(self, mode: str):
        """设置风扇控制模式。自动模式的第一次写入留给控制节拍。"""
        if not self._current_profile:
            return
        previous_mode = self.state.get_applied_fan_mode()
        self._mode_generation += 1
        generation = self._mode_generation
        try:
            if mode == FAN_MODE_AUTO:
                self._auto_target = None
                self._reset_stall_state()
                self.auto_temp_controller.reset_state()
                self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)
                if not self.controller_timer.isActive():
                    self.controller_timer.start()
                self.state.set_applied_fan_mode(FAN_MODE_AUTO)
                self.state.set_is_fan_control_panel_enabled(True)
                self._write_last_mode_state(FAN_MODE_AUTO)
                self._write_heartbeat(force=True)
                self._perform_control_cycle()
            elif mode == FAN_MODE_CUSTOM:
                self._auto_target = None
                self._reset_stall_state()
                if not self.controller_timer.isActive():
                    self.controller_timer.start()
                self.state.set_applied_fan_mode(FAN_MODE_CUSTOM)
                self.state.set_is_fan_control_panel_enabled(True)
                self._write_last_mode_state(FAN_MODE_CUSTOM)
                self._write_heartbeat(force=True)
                self._perform_control_cycle()
            elif mode == FAN_MODE_BIOS:
                if self.controller_timer.isActive():
                    self.controller_timer.stop()
                self._auto_target = None
                self._reset_stall_state()
                self._request_bios_control(generation=generation, fallback_mode=previous_mode)
            else:
                if self.controller_timer.isActive():
                    self.controller_timer.stop()
                self.state.set_applied_fan_mode(mode)
        except WMIError as e:
            print(f"WMI operation failed in 'set_fan_mode': {e}", file=sys.stderr)
            self.state.set_controller_status_message(
                tr("fan_mode_apply_failed", mode=_mode_label(mode)))
            self.state.set_applied_fan_mode(FAN_MODE_UNKNOWN)
            self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)

    @Slot(int)
    def set_custom_fan_speed(self, speed: int):
        """自定义滑块变化走同一条斜坡写入路径。"""
        if not self._current_profile or self.state.get_applied_fan_mode() != FAN_MODE_CUSTOM:
            return
        try:
            self._perform_control_cycle()
        except WMIError as e:
            print(f"WMI operation failed in 'set_custom_fan_speed': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))

    def _verify_charge_state(self):
        """Async EC read of the applied charge policy/threshold; rewrites the
        profile's values when firmware drifted (sleep, EC reset, battery pull).
        Cheap enough (2 getters) for the watch timer and resume recovery."""
        if self._is_shutting_down or self._charge_verify_in_flight:
            return
        if not self._current_profile or not self.wmi_interface.is_running:
            return
        if time.monotonic() - self._last_charge_write_ms < 5.0:
            return  # a charge write just landed; its readback covers this window
        self._charge_verify_in_flight = True
        self.wmi_interface.submit_async(
            "_get_charge_status",
            on_done=lambda data, exc: self._on_charge_status(data, exc),
        )

    def _on_charge_status(self, data: Any, exc: Optional[Exception]):
        self._charge_verify_in_flight = False
        if self._is_shutting_down:
            return
        if exc is not None:
            print(f"WMI operation failed in charge watch: {exc}", file=sys.stderr)
            return
        if not self._current_profile:
            return
        data = data or {}
        applied_code = data.get("charge_policy", CHARGE_POLICY_READ_ERROR_VALUE)
        applied_threshold = data.get("charge_threshold", CHARGE_THRESHOLD_READ_ERROR_VALUE)
        try:
            applied_code = int(applied_code)
        except (TypeError, ValueError):
            applied_code = CHARGE_POLICY_READ_ERROR_VALUE
        try:
            applied_threshold = int(applied_threshold)
        except (TypeError, ValueError):
            applied_threshold = CHARGE_THRESHOLD_READ_ERROR_VALUE
        if applied_code >= 0:
            # Reflect EC truth in the UI regardless of whether we rewrite.
            self._update_state_from_sensor_data({"charge_policy": applied_code})
        desired = self._current_profile.get_battery_charge_policy()
        if desired is None or desired not in BATTERY_POLICY_CODES or applied_code < 0:
            return
        applied_name = BATTERY_CODE_POLICIES.get(applied_code)
        drifted = applied_name != desired
        if not drifted and desired == CHARGE_POLICY_CUSTOM:
            try:
                wanted_threshold = int(self._current_profile.get_battery_charge_threshold())
            except (TypeError, ValueError):
                return
            drifted = applied_threshold >= 0 and applied_threshold != wanted_threshold
        if drifted:
            print(f"Charge watch: EC reports {applied_name}/{applied_threshold}%, "
                  f"profile wants {desired}; reapplying.", file=sys.stderr)
            self.state.set_controller_status_message(tr("charge_watch_reapplied"))
            self.set_battery_charge_policy(desired)

    def _schedule_charge_readback(self, requested: Optional[int] = None):
        def _readback():
            self.wmi_interface.submit_async(
                "_get_all_sensors",
                on_done=lambda data, exc: self._on_charge_readback(data, exc, requested),
            )

        QTimer.singleShot(500, _readback)

    def _on_charge_readback(self, sensor_data: Any, exc: Optional[Exception], requested: Optional[int]):
        if exc is not None:
            print(f"WMI operation failed in charge readback: {exc}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        self._update_state_from_sensor_data(sensor_data)
        if not sensor_data or requested is None:
            return
        actual = sensor_data.get("charge_threshold", -1)
        try:
            actual = int(actual)
        except (TypeError, ValueError):
            actual = -1
        if actual >= 0 and actual != requested:
            self.state.set_controller_status_message(
                tr("charge_readback_mismatch", actual=actual, requested=requested)
            )

    @Slot(str)
    def set_battery_charge_policy(self, policy_name: str):
        """设置电池充电策略。不写入策略码 1。"""
        if not self._current_profile:
            return
        policy_code = BATTERY_POLICY_CODES.get(policy_name)
        if policy_code is None:
            return
        requested = None
        if policy_name == CHARGE_POLICY_CUSTOM:
            requested = int(self._current_profile.get_battery_charge_threshold())
        self.wmi_interface.submit_async(
            WMIInternalSignal.APPLY_CHARGE_POLICY,
            {
                "policy": float(policy_code),
                "threshold": float(requested) if requested is not None else None,
            },
            on_done=lambda result, exc: self._on_charge_policy_applied(result, exc, requested),
        )

    def _on_charge_policy_applied(self, result: Any, exc: Optional[Exception], requested: Optional[int]):
        if exc is not None:
            print(f"WMI operation failed in 'set_battery_charge_policy': {exc}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        self._last_charge_write_ms = time.monotonic()
        self._schedule_charge_readback(requested)

    @Slot(int)
    def set_battery_charge_threshold(self, threshold: int, force_apply: bool = False):
        """设置电池充电阈值，延迟回读，不阻塞 UI 线程。"""
        if not force_apply and (not self._current_profile or self.state.get_applied_charge_policy() != CHARGE_POLICY_CUSTOM):
            return
        self.wmi_interface.submit_async(
            SET_CHARGE_STOP,
            {"Data": float(threshold)},
            on_done=lambda result, exc: self._on_charge_threshold_applied(result, exc, int(threshold)),
        )

    def _on_charge_threshold_applied(self, result: Any, exc: Optional[Exception], threshold: int):
        if exc is not None:
            print(f"WMI operation failed in 'set_battery_charge_threshold': {exc}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        self._last_charge_write_ms = time.monotonic()
        self._schedule_charge_readback(threshold)

    def _smooth_control_temp(self, state: Optional[float], raw: float) -> float:
        """EMA for the control path only (alpha TEMP_SMOOTH_ALPHA).

        Error values pass through untouched so fault detection keeps its exact
        contract; the first valid reading after an error re-seeds instead of
        easing in from stale state.
        """
        if raw == TEMP_READ_ERROR_VALUE:
            return raw
        if state is None or state == TEMP_READ_ERROR_VALUE:
            return raw
        return state + TEMP_SMOOTH_ALPHA * (raw - state)

    @Slot()
    def _perform_control_cycle(self):
        """心跳（非阻塞）：向 WMI worker 提交一次温度/转速读取，回调里决定目标并异步写入。"""
        if self._is_shutting_down:
            return
        if not self.wmi_interface.is_running:
            self._recover_wmi_if_due()
            return
        mode = self.state.get_applied_fan_mode()
        if mode not in (FAN_MODE_AUTO, FAN_MODE_CUSTOM) or not self._current_profile:
            return
        if self._cycle_in_flight:
            return
        self._cycle_in_flight = True
        self._cycle_generation += 1
        cycle_id = self._cycle_generation
        self._cycle_watchdog.start(CYCLE_WATCHDOG_MS)
        self.wmi_interface.submit_async(
            "_get_core_sensors",
            on_done=lambda result, exc: self._on_cycle_temps(result, exc, cycle_id),
        )

    def _on_cycle_temps(self, temps: Any, exc: Optional[Exception], cycle_id: int):
        if cycle_id != self._cycle_generation:
            return  # stale callback from a cycle the watchdog already retired
        try:
            if exc is not None:
                raise exc
            mode = self.state.get_applied_fan_mode()
            if self._is_shutting_down or mode not in (FAN_MODE_AUTO, FAN_MODE_CUSTOM) or not self._current_profile:
                self._cycle_watchdog.stop()
                self._cycle_in_flight = False
                return
            data = temps or {}
            cpu_temp = data.get("cpu_temp", TEMP_READ_ERROR_VALUE)
            gpu_temp = data.get("gpu_temp", TEMP_READ_ERROR_VALUE)
            gpu_asleep = bool(data.get("gpu_asleep", False))
            fan1_rpm = data.get("fan1_rpm", RPM_READ_ERROR_VALUE)
            fan2_rpm = data.get("fan2_rpm", RPM_READ_ERROR_VALUE)
            gpu_power_w = data.get("gpu_power_w")
            gpu_util = data.get("gpu_util")
            # Keep the worker's NVML gate in sync with the active profile's
            # feed-forward flag: profiles with it disabled make no NVML
            # calls at all, so the dGPU is free to enter D3cold.
            self.wmi_interface.set_nvml_poll_enabled(
                bool(self._current_profile.get_value("gpu_power_feedforward", True)))
            if self._is_ui_visible:
                self.state.set_cpu_temp(cpu_temp)
                self.state.set_gpu_temp(gpu_temp)
                self.state.set_fan1_rpm(fan1_rpm)
                self.state.set_fan2_rpm(fan2_rpm)
            self.state.set_gpu_asleep(gpu_asleep)
            # Utilization is display-only; keep it fresh while hidden so the
            # dashboard is current on show (footer dedupes identical text).
            self.state.set_gpu_util(gpu_util if gpu_util is not None else -1)

            # Control path runs on EMA-smoothed temps: integer EC temps
            # through the curve slopes would otherwise wobble the target
            # every tick. Raw temps still feed the UI, hot-alert, and fault
            # detection (the EMA passes error values through untouched, so
            # fault semantics are unchanged).
            cpu_ctrl = self._smooth_control_temp(self._cpu_ctrl_temp, cpu_temp)
            gpu_ctrl = self._smooth_control_temp(self._gpu_ctrl_temp, gpu_temp)
            self._cpu_ctrl_temp = cpu_ctrl
            self._gpu_ctrl_temp = gpu_ctrl
            # Mirror the smoothed control temps for the curve canvas: raw
            # temps drive the dotted crosshairs, the EMA ring shows the
            # operating point the controller actually acts on. Same
            # UI-visibility gate as the raw-temps block above.
            if self._is_ui_visible:
                self.state.set_cpu_ctrl_temp(cpu_ctrl)
                self.state.set_gpu_ctrl_temp(gpu_ctrl)

            if mode == FAN_MODE_CUSTOM:
                base_target = 0
                custom_speed = int(self._current_profile.get_custom_fan_speed())
            else:
                # NVML feed-forward: lift the GPU curve input by a power-based
                # offset so the fan reacts while heat is still travelling to
                # the heatsink. Unknown power / asleep dGPU -> no offset. The
                # offset cap eases in after an NVML idle-skip resume so the
                # fans don't step off the floor duty in one write.
                gpu_ff_temp = gpu_ctrl
                if (gpu_ctrl != TEMP_READ_ERROR_VALUE
                        and self._current_profile.get_value("gpu_power_feedforward", True)):
                    gpu_ff_temp = gpu_ctrl + feedforward_offset(
                        gpu_power_w, NVML_FF_IDLE_W, NVML_FF_K_C_PER_W,
                        self.wmi_interface.ff_max_boost_cap())
                base_target = self.auto_temp_controller.theoretical_target(cpu_ctrl, gpu_ff_temp)
                custom_speed = 0

            # Guard floors key on RAW temps: the EMA lags load onset by a few
            # ticks, and a lagging floor let an under-floor duty through at
            # load onset (bench 2026-11-16: 26 % written at raw cpu 90 °C).
            # guard_floor's engage/release band already debounces integer
            # sensor noise, so the EMA buys nothing here.
            self._cpu_guard_floor = guard_floor(cpu_temp, self._cpu_guard_floor)
            self._gpu_guard_floor = guard_floor(gpu_temp, self._gpu_guard_floor)
            raw_target = select_fan_target(
                mode, custom_speed, base_target, cpu_temp, gpu_temp, gpu_asleep,
                self._cpu_guard_floor, self._gpu_guard_floor,
            )
            if mode == FAN_MODE_AUTO:
                target, self._zero_streak = next_target(
                    self._auto_target, raw_target,
                    DEFAULT_TARGET_HYSTERESIS_PERCENT, self._zero_streak)
                self._auto_target = target
            else:
                target = raw_target

            percent = next_fan_percent(
                self.state.get_applied_fan_speed_percent(),
                target,
            )
            self._update_fan_stall(percent, fan1_rpm, fan2_rpm, gpu_asleep)
            self._update_hot_alert(cpu_temp, gpu_temp, gpu_asleep)

            changed = percent != self.state.get_applied_fan_speed_percent()
            self._cycles_since_flag_reassert += 1
            if changed or self._cycles_since_flag_reassert >= FLAG_REASSERT_EVERY_CYCLES:
                # H4：仅在占空比变化或 keepalive 到期时写 EC（reassert 同时重写占空比，双保险）。
                self._pending_cycle = {"target": target, "percent": percent}
                duty_raw = float(percent_to_raw(percent))
                self.wmi_interface.submit_async(
                    WMIInternalSignal.APPLY_FAN_CONTROL,
                    {"duty_raw": duty_raw, "reassert_flags": True},
                    on_done=lambda result, exc: self._on_cycle_applied(result, exc, cycle_id),
                )
                return  # in_flight 保持 True，watchdog 继续看护
            self._cycle_watchdog.stop()
            self._cycle_in_flight = False
            self._write_heartbeat()
            self.state.set_auto_fan_target_speed_percent(target)
        except Exception as e:
            self._cycle_watchdog.stop()
            self._cycle_in_flight = False
            self._on_cycle_failed(e)

    def _update_hot_alert(self, cpu_temp: float, gpu_temp: float, gpu_asleep: bool):
        """Balloon once after HOT_ALERT_SUSTAINED_CYCLES consecutive cycles at
        or above HOT_ALERT_TEMP_C (ignoring a sleeping dGPU's error value);
        re-arms once every readable sensor is below HOT_ALERT_RESET_C, so the
        next excursion alerts again instead of being suppressed forever."""
        sensors = [cpu_temp]
        if not gpu_asleep:
            sensors.append(gpu_temp)
        readable = [t for t in sensors
                    if t is not None and t != TEMP_READ_ERROR_VALUE]
        hot = any(t >= HOT_ALERT_TEMP_C for t in readable)
        if hot:
            self._hot_alert_cycles += 1
            if self._hot_alert_cycles >= HOT_ALERT_SUSTAINED_CYCLES and not self._hot_alert_active:
                self._hot_alert_active = True
                detail = " / ".join(f"{t:.0f} °C" for t in readable)
                self.state.set_controller_status_message(
                    tr("sustained_hot_alert", temps=detail))
            return
        self._hot_alert_cycles = 0
        if not readable or all(t < HOT_ALERT_RESET_C for t in readable):
            self._hot_alert_active = False

    def _update_fan_stall(self, percent: int, fan1_rpm: Any, fan2_rpm: Any, gpu_asleep: bool):
        duty_raw = percent_to_raw(percent)
        if duty_raw <= 0:
            self._reset_stall_state()
            return

        stalled_fans = []
        if fan1_rpm == 0:
            self._fan1_stall_cycles += 1
            if self._fan1_stall_cycles >= FAN_STALL_CYCLES:
                stalled_fans.append(tr("fan_label_cpu"))
        else:
            self._fan1_stall_cycles = 0

        if gpu_asleep:
            self._fan2_stall_cycles = 0
        elif fan2_rpm == 0:
            self._fan2_stall_cycles += 1
            if self._fan2_stall_cycles >= FAN_STALL_CYCLES:
                stalled_fans.append(tr("fan_label_gpu"))
        else:
            self._fan2_stall_cycles = 0

        if stalled_fans:
            if not self._fan_stall_reported:
                self._fan_stall_reported = True
                self._last_stall_message = tr(
                    "fan_stall_warning",
                    fans=", ".join(stalled_fans),
                    duty=effective_percent(percent),
                )
                self.state.set_controller_status_message(self._last_stall_message)
        elif self._fan1_stall_cycles == 0 and self._fan2_stall_cycles == 0:
            if self._fan_stall_reported and self.state.get_controller_status_message() == self._last_stall_message:
                self.state.set_controller_status_message("")
            self._fan_stall_reported = False
            self._last_stall_message = ""

    def _on_cycle_applied(self, result: Any, exc: Optional[Exception], cycle_id: int):
        if cycle_id != self._cycle_generation:
            return  # stale callback from a cycle the watchdog already retired
        self._cycle_watchdog.stop()
        self._cycle_in_flight = False
        if exc is not None:
            self._on_cycle_failed(exc)
            return
        pending, self._pending_cycle = self._pending_cycle, None
        if not pending:
            return
        self._cycles_since_flag_reassert = 0
        self._write_heartbeat()
        # 飞行中用户切到 BIOS：异步纠正刚写入的软件标志。
        if self.state.get_applied_fan_mode() == FAN_MODE_BIOS:
            self._request_bios_control(generation=self._mode_generation)
            return
        self.state.set_auto_fan_target_speed_percent(pending["target"])
        self.state.set_applied_fan_speed_percent(pending["percent"])

    def _on_cycle_failed(self, error: Exception):
        print(f"WMI operation failed in control cycle: {error}", file=sys.stderr)
        self._log_control_event(f"cycle failed: {error!r}")
        self.state.set_controller_status_message(tr("wmi_error"))
        self._auto_target = None
        self._reset_stall_state()
        if self._is_ui_visible:
            self.state.set_cpu_temp(TEMP_READ_ERROR_VALUE)
            self.state.set_gpu_temp(TEMP_READ_ERROR_VALUE)
        self.state.set_gpu_asleep(False)

    def _on_cycle_watchdog(self):
        """周期超过 CYCLE_WATCHDOG_MS：worker 多半卡死在 COM 调用里。重建接口而不是冻结。"""
        if self._is_shutting_down or not self._cycle_in_flight:
            return
        self._cycle_in_flight = False
        self._cycle_generation += 1
        self._pending_cycle = None
        self._auto_target = None
        self._log_control_event("cycle watchdog fired: rebuilding worker")
        try:
            self.wmi_interface.stop(join_timeout=1.0)
        except Exception:
            pass
        if not self.wmi_interface.start(init_timeout=3.0):
            self.state.set_controller_status_message(tr("wmi_error"))

    @Slot()
    def on_gui_tick(self):
        """由UI定时器触发，用于刷新UI显示的传感器数据。"""
        if self._is_shutting_down:
            return
        if not self.wmi_interface.is_running:
            self._recover_wmi_if_due()
            return
        if not self._is_ui_visible:
            return

        # In auto/custom the control cycle sweeps at its own cadence and
        # already feeds this cache; polling here as well would double the EC
        # traffic. In BIOS/UNKNOWN this tick is the only poller.
        if self.state.get_applied_fan_mode() not in (FAN_MODE_AUTO, FAN_MODE_CUSTOM):
            self.wmi_interface.request_core_sensor_poll()
        sensor_data = self.wmi_interface.get_latest_core_sensor_data()
        self._update_state_from_sensor_data(sensor_data)

    def request_full_status_update(self):
        """非阻塞的完整传感器刷新。"""
        if self._is_shutting_down:
            return
        self.wmi_interface.submit_async("_get_all_sensors", on_done=self._on_full_status)

    def _on_full_status(self, sensor_data: Any, exc: Optional[Exception]):
        if exc is not None:
            print(f"WMI operation failed in full status update: {exc}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        self._update_state_from_sensor_data(sensor_data)

    @Slot()
    def perform_full_status_update(self):
        """执行一次全局的、阻塞式的WMI查询。成功时返回传感器字典。"""
        try:
            sensor_data = self.wmi_interface.get_all_sensors_sync()
            self._update_state_from_sensor_data(sensor_data)
            return sensor_data
        except WMIError as e:
            print(f"WMI operation failed in 'perform_full_status_update': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return None

    def _update_state_from_sensor_data(self, sensor_data: dict):
        """Push sensor values into AppState via the static setter map
        (change-gated setters keep the old dedup semantics)."""
        if not sensor_data:
            return
        if 'charge_policy' in sensor_data:
            sensor_data['charge_policy'] = BATTERY_CODE_POLICIES.get(sensor_data['charge_policy'], "err")
        for key, setter in self._sensor_setters.items():
            if key in sensor_data:
                setter(sensor_data[key])

    def set_ui_visibility(self, is_visible: bool):
        """当UI可见性变化时调用。"""
        self._is_ui_visible = is_visible
        self.wmi_interface.set_nvml_util_enabled(bool(is_visible))
        if is_visible:
            self.request_full_status_update()
