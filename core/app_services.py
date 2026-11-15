# -*- coding: utf-8 -*-
"""
Provides a centralized service layer (AppServices) that decouples backend logic from the UI.
Adds graceful-shutdown logic: on exit, fan control is handed back to the BIOS, and the current fan mode is recorded for the crash-safety mechanism.
"""

import os
import sys
import time
from collections import deque
from typing import Optional, List, Any, Callable, Dict

from gui.qt import QObject, Slot, QTimer

from .wmi_interface import WMIInterface, WMIError
from .auto_temp_controller import AutoTemperatureController
from .control_smoothing import smooth_and_predict
from .state import AppState, ProfileState
from .fan_duty import percent_to_raw
from .fan_ramp import next_fan_percent
from config.models import detect_model, get_preset
from tools.localization import tr
from config.settings import (
    FAN_MODE_BIOS, FAN_MODE_AUTO, FAN_MODE_CUSTOM,
    CHARGE_POLICY_CUSTOM, BATTERY_POLICY_CODES, BATTERY_CODE_POLICIES,
    INIT_APPLIED_PERCENTAGE, TEMP_READ_ERROR_VALUE,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_STATUS,
    SET_CHARGE_POLICY, SET_CHARGE_STOP, WMIInternalSignal,
    CONTROL_SMOOTH_WINDOW, PREDICT_SLOPE_SAMPLES, DEFAULT_MAX_ADJUSTMENT_STEP,
    UP_RATE_BYPASS_TARGET_PERCENT, UP_RATE_BYPASS_TEMP_C
)

# H4 keepalive: reassert the software-control EC flags at least this often (cycles).
FLAG_REASSERT_EVERY_CYCLES = 8
# H3 safety: a control cycle that takes longer than this means the worker is hung.
CYCLE_WATCHDOG_MS = 8000

FanTable = List[List[int]]

class AppServices(QObject):
    """Centralized service layer managing all backend controllers and reacting to app-state changes."""

    def __init__(self, state: AppState, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._is_shutting_down = False
        self.state = state
        self._current_profile: Optional[ProfileState] = None
        self.wmi_interface = WMIInterface()
        self.auto_temp_controller = AutoTemperatureController()

        self.controller_timer = QTimer(self)
        self.controller_timer.timeout.connect(self._perform_control_cycle)
        self._kick_armed = False
        self._spinning_down = False
        self._last_wmi_recovery_attempt: float = 0.0
        self._model = detect_model()
        self._fan_preset = get_preset(self._model)
        self._cycle_in_flight = False
        self._cycles_since_flag_reassert = 0
        self._pending_cycle: Optional[Dict[str, int]] = None
        # Auto-loop temperature conditioning state (see core/control_smoothing.py).
        self._cpu_temp_hist: deque = deque(maxlen=CONTROL_SMOOTH_WINDOW)
        self._gpu_temp_hist: deque = deque(maxlen=CONTROL_SMOOTH_WINDOW)
        self._cpu_smooth_hist: deque = deque(maxlen=PREDICT_SLOPE_SAMPLES)
        self._gpu_smooth_hist: deque = deque(maxlen=PREDICT_SLOPE_SAMPLES)
        self._max_up_step: int = DEFAULT_MAX_ADJUSTMENT_STEP
        self._cycle_watchdog = QTimer(self)
        self._cycle_watchdog.setSingleShot(True)
        self._cycle_watchdog.timeout.connect(self._on_cycle_watchdog)
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self.recover_after_resume)

        self.state.set_controller_status_message(tr("initializing"))
        self._is_ui_visible: bool = False
        
        # State file path for the crash-safety mechanism
        self._last_mode_file_path = os.path.join(self.state.paths.base_dir, 'last_mode.state')
        
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
        self.set_fan_mode(profile.get_fan_mode())
        self.set_battery_charge_policy(profile.get_battery_charge_policy())

    def _update_controller_curves(self):
        if self._current_profile:
            self.auto_temp_controller.update_curves(
                self._current_profile.get_cpu_fan_table(),
                self._current_profile.get_gpu_fan_table()
            )
            self._reset_smoothing()
            self._arm_software_ramp()

    def _reset_smoothing(self):
        """Clears temperature conditioning history (profile/curve switch, resume)."""
        self._cpu_temp_hist.clear()
        self._gpu_temp_hist.clear()
        self._cpu_smooth_hist.clear()
        self._gpu_smooth_hist.clear()

    def _condition_temperature(self, raw_hist: deque, smooth_hist: deque, raw: float) -> Optional[float]:
        """Median -> EMA -> dT/dt boost; None for error samples (history untouched)."""
        if raw == TEMP_READ_ERROR_VALUE:
            return None
        return smooth_and_predict(raw_hist, smooth_hist, float(raw), self.controller_timer.interval() / 1000.0)

    def _update_controller_settings(self):
        if self._current_profile:
            self.auto_temp_controller.update_auto_settings(self._current_profile)
            try:
                self._max_up_step = max(0, int(self._current_profile.get_value("max_adjustment_step")))
            except (TypeError, ValueError):
                self._max_up_step = DEFAULT_MAX_ADJUSTMENT_STEP

            controller_interval = self._current_profile.get_value('controller_update_interval_ms')
            if self.controller_timer.interval() != controller_interval:
                self.controller_timer.setInterval(controller_interval)

    def initialize_wmi(self) -> bool:
        if not self.wmi_interface.start():
            init_error = self.wmi_interface.get_initialization_error()
            error_msg = tr("wmi_init_error_msg", error=str(init_error))
            self.state.set_controller_status_message(error_msg)
            return False
        self.state.set_controller_status_message("")
        return True

    def shutdown(self):
        """Run the graceful shutdown sequence. After an emergency latch, fans are not handed back to the BIOS."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.controller_timer.stop()
        self._cycle_watchdog.stop()
        self._cycle_in_flight = False
        self._pending_cycle = None
        if self._resume_timer.isActive():
            self._resume_timer.stop()
        if self.state.get_applied_fan_mode() != FAN_MODE_BIOS and not self._fan_emergency_latched():
            print("Graceful exit: handing fan control back to BIOS...")
            try:
                self._configure_bios_fan_control()
                print("Graceful exit: fan mode set to BIOS.")
            except WMIError as e:
                print(f"Graceful exit: failed to set BIOS fan mode: {e}", file=sys.stderr)
        if self.wmi_interface:
            self.wmi_interface.stop()

    def _configure_bios_fan_control(self):
        """Execute the WMI command sequence that hands fan control back to the BIOS."""
        self.wmi_interface.execute_method(SET_AUTO_FAN_STATUS, Data=1.0)
        self.wmi_interface.execute_method(SET_CUSTOM_FAN_STATUS, Data=0.0)

    def _arm_software_ramp(self):
        self._kick_armed = True
        self._spinning_down = False

    def _fan_emergency_latched(self) -> bool:
        for name in ("__main__", "main"):
            mod = sys.modules.get(name)
            if mod is not None and getattr(mod, "_emergency_latched", False):
                return True
        return False

    def note_resume(self):
        """Collapse consecutive resume broadcasts into a single recovery."""
        if self._is_shutting_down:
            return
        self._resume_timer.start(1500)

    def _recover_wmi_if_due(self):
        """Rebuild WMI when the worker thread dies; throttled to once per 30 s, reports wmi_error on failure."""
        now = time.monotonic()
        if now - self._last_wmi_recovery_attempt < 30.0:
            return
        self._last_wmi_recovery_attempt = now
        try:
            self.wmi_interface.stop()
        except Exception:
            pass
        if self.wmi_interface.start(init_timeout=3.0):
            self.state.set_controller_status_message("")
            self._arm_software_ramp()
            profile = self._current_profile
            if profile and self.state.get_applied_fan_mode() in (FAN_MODE_AUTO, FAN_MODE_CUSTOM):
                self._perform_control_cycle()
        else:
            self.state.set_controller_status_message(tr("wmi_error"))

    def recover_after_resume(self):
        """Rebuild WMI. In auto mode, write a fan speed once immediately after resume."""
        if self._is_shutting_down:
            return
        self.controller_timer.stop()
        self.wmi_interface.stop()
        if not self.wmi_interface.start():
            self.state.set_controller_status_message(tr("wmi_error"))
            return
        profile = self._current_profile
        if not profile:
            return
        self._reset_smoothing()  # pre-sleep samples are stale
        self._arm_software_ramp()
        mode = profile.get_fan_mode()
        self.set_fan_mode(mode)
        if mode == FAN_MODE_AUTO:
            self._perform_control_cycle()

    def _write_last_mode_state(self, mode: str):
        """Write the current fan mode to the state file for crash recovery."""
        try:
            with open(self._last_mode_file_path, 'w') as f:
                f.write(mode)
        except IOError as e:
            print(f"Warning: could not write 'last_mode.state' file: {e}", file=sys.stderr)

    @Slot(str)
    def set_fan_mode(self, mode: str):
        """Set the fan control mode. In auto mode, the first write is left to the control tick."""
        if not self._current_profile:
            return
        try:
            if mode == FAN_MODE_AUTO:
                self._arm_software_ramp()
                self.auto_temp_controller.reset_state()
                self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)
                if not self.controller_timer.isActive():
                    self.controller_timer.start()
            elif mode == FAN_MODE_CUSTOM:
                self._arm_software_ramp()
                if not self.controller_timer.isActive():
                    self.controller_timer.start()
            elif mode == FAN_MODE_BIOS:
                if self.controller_timer.isActive():
                    self.controller_timer.stop()
                self._kick_armed = False
                self._spinning_down = False
                self._configure_bios_fan_control()
                self.state.set_applied_fan_speed_percent(INIT_APPLIED_PERCENTAGE)
            else:
                if self.controller_timer.isActive():
                    self.controller_timer.stop()

            self.state.set_applied_fan_mode(mode)
            self.state.set_is_fan_control_panel_enabled(mode != FAN_MODE_BIOS)
            self._write_last_mode_state(mode)
            if mode == FAN_MODE_CUSTOM:
                self._perform_control_cycle()
        except WMIError as e:
            print(f"WMI operation failed in 'set_fan_mode': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            self.state.set_applied_fan_mode(FAN_MODE_BIOS)
            self._write_last_mode_state(FAN_MODE_BIOS)

    @Slot(int)
    def set_custom_fan_speed(self, speed: int):
        """Custom slider changes go through the same ramped write path."""
        if not self._current_profile or self.state.get_applied_fan_mode() != FAN_MODE_CUSTOM:
            return
        try:
            self._perform_control_cycle()
        except WMIError as e:
            print(f"WMI operation failed in 'set_custom_fan_speed': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))

    def _schedule_charge_readback(self, requested: Optional[int] = None):
        def _readback():
            sensor_data = self.perform_full_status_update()
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
        QTimer.singleShot(500, _readback)

    @Slot(str)
    def set_battery_charge_policy(self, policy_name: str):
        """Set the battery charge policy. Policy code 1 is never written."""
        if not self._current_profile:
            return
        try:
            policy_code = BATTERY_POLICY_CODES.get(policy_name)
            if policy_code is None:
                return
            self.wmi_interface.execute_method(SET_CHARGE_POLICY, Data=float(policy_code))
            requested = None
            if policy_name == CHARGE_POLICY_CUSTOM:
                requested = int(self._current_profile.get_battery_charge_threshold())
                self.wmi_interface.execute_method(SET_CHARGE_STOP, Data=float(requested))
            self._schedule_charge_readback(requested)
        except WMIError as e:
            print(f"WMI operation failed in 'set_battery_charge_policy': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))

    @Slot(int)
    def set_battery_charge_threshold(self, threshold: int, force_apply: bool = False):
        """Set the battery charge threshold with a delayed readback, without blocking the UI thread."""
        if not force_apply and (not self._current_profile or self.state.get_applied_charge_policy() != CHARGE_POLICY_CUSTOM):
            return
        try:
            self.wmi_interface.execute_method(SET_CHARGE_STOP, Data=float(threshold))
            self._schedule_charge_readback(int(threshold))
        except WMIError as e:
            print(f"WMI operation failed in 'set_battery_charge_threshold': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
    def _select_fan_target(self, mode: str, cpu_temp: float, gpu_temp: float, gpu_asleep: bool = False,
                           cpu_ctrl: Optional[float] = None, gpu_ctrl: Optional[float] = None) -> int:
        if mode == FAN_MODE_CUSTOM and self._current_profile:
            base = int(self._current_profile.get_custom_fan_speed())
        else:
            # Curve input uses the conditioned (median/EMA/boost) temperatures;
            # safety logic below always sees the raw values.
            base = self.auto_temp_controller.theoretical_target(
                cpu_temp if cpu_ctrl is None else cpu_ctrl,
                gpu_temp if gpu_ctrl is None else gpu_ctrl,
            )
        cpu_bad = cpu_temp == TEMP_READ_ERROR_VALUE
        # A sleeping dGPU reports 0 (no sensor to read) - not a failure; the CPU curve decides alone.
        gpu_bad = gpu_temp == TEMP_READ_ERROR_VALUE and not gpu_asleep
        if cpu_bad and (gpu_bad or gpu_asleep):
            target = 80
        elif cpu_bad or gpu_bad:
            target = max(base, 40)
        else:
            target = base
        if (not cpu_bad and cpu_temp >= 90) or (not gpu_bad and gpu_temp >= 90):
            target = max(target, 80)
        return target

    @Slot()
    def _perform_control_cycle(self):
        """Heartbeat (non-blocking): submit one temperature read to the WMI worker; the callback picks the target and writes asynchronously.
        H3: the GUI thread no longer makes any blocking WMI calls."""
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
        self._cycle_watchdog.start(CYCLE_WATCHDOG_MS)
        self.wmi_interface.submit_async("_get_temperatures", on_done=self._on_cycle_temps)

    def _on_cycle_temps(self, temps: Any, exc: Optional[Exception]):
        try:
            if exc is not None:
                raise exc
            mode = self.state.get_applied_fan_mode()
            if self._is_shutting_down or mode not in (FAN_MODE_AUTO, FAN_MODE_CUSTOM) or not self._current_profile:
                self._cycle_watchdog.stop()
                self._cycle_in_flight = False
                return
            cpu_temp = temps.get("cpu_temp", TEMP_READ_ERROR_VALUE)
            gpu_temp = temps.get("gpu_temp", TEMP_READ_ERROR_VALUE)
            gpu_asleep = bool(temps.get("gpu_asleep", False))
            if self._is_ui_visible:
                self.state.set_cpu_temp(cpu_temp)
                self.state.set_gpu_temp(gpu_temp)
            self.state.set_gpu_asleep(gpu_asleep)
            cpu_ctrl = self._condition_temperature(self._cpu_temp_hist, self._cpu_smooth_hist, cpu_temp)
            gpu_ctrl = None if gpu_asleep else self._condition_temperature(self._gpu_temp_hist, self._gpu_smooth_hist, gpu_temp)
            target = self._select_fan_target(mode, cpu_temp, gpu_temp, gpu_asleep, cpu_ctrl, gpu_ctrl)
            # Spin-up rate limit; bypassed for big targets or hot sensors (safety snappiness).
            max_up_step = self._max_up_step
            hot = (cpu_temp != TEMP_READ_ERROR_VALUE and cpu_temp >= UP_RATE_BYPASS_TEMP_C) or (
                not gpu_asleep and gpu_temp != TEMP_READ_ERROR_VALUE and gpu_temp >= UP_RATE_BYPASS_TEMP_C)
            if target >= UP_RATE_BYPASS_TARGET_PERCENT or hot:
                max_up_step = 0
            percent, self._kick_armed, self._spinning_down = next_fan_percent(
                self.state.get_applied_fan_speed_percent(),
                target,
                self._kick_armed,
                self._spinning_down,
                max_up_step,
            )
            changed = percent != self.state.get_applied_fan_speed_percent()
            self._cycles_since_flag_reassert += 1
            if changed or self._cycles_since_flag_reassert >= FLAG_REASSERT_EVERY_CYCLES:
                # H4: write the EC only when the duty changed or the keepalive is due (the reassert also rewrites the duty as a belt-and-braces measure).
                self._pending_cycle = {"target": target, "percent": percent}
                duty_raw = float(percent_to_raw(
                    percent,
                    self._fan_preset["fan_raw_max"],
                    self._fan_preset["fan_percent_min_spin"],
                ))
                self.wmi_interface.submit_async(
                    WMIInternalSignal.APPLY_FAN_CONTROL,
                    {"duty_raw": duty_raw, "reassert_flags": True},
                    on_done=self._on_cycle_applied,
                )
                return  # keep in_flight True; the watchdog keeps guarding
            self._cycle_watchdog.stop()
            self._cycle_in_flight = False
            self.state.set_auto_fan_target_speed_percent(target)
        except Exception as e:
            self._cycle_watchdog.stop()
            self._cycle_in_flight = False
            self._on_cycle_failed(e)

    def _on_cycle_applied(self, result: Any, exc: Optional[Exception]):
        self._cycle_watchdog.stop()
        self._cycle_in_flight = False
        if exc is not None:
            self._on_cycle_failed(exc)
            return
        pending, self._pending_cycle = self._pending_cycle, None
        if not pending:
            return
        self._cycles_since_flag_reassert = 0
        self.state.set_auto_fan_target_speed_percent(pending["target"])
        self.state.set_applied_fan_speed_percent(pending["percent"])
        # User switched to BIOS mid-flight: immediately correct the just-written software flags so no custom control lingers.
        if self.state.get_applied_fan_mode() == FAN_MODE_BIOS:
            try:
                self._configure_bios_fan_control()
            except WMIError as e:
                print(f"WMI operation failed correcting to BIOS after mid-flight mode change: {e}", file=sys.stderr)
                self.state.set_controller_status_message(tr("wmi_error"))

    def _on_cycle_failed(self, error: Exception):
        print(f"WMI operation failed in control cycle: {error}", file=sys.stderr)
        self.state.set_controller_status_message(tr("wmi_error"))
        if self._is_ui_visible:
            self.state.set_cpu_temp(TEMP_READ_ERROR_VALUE)
            self.state.set_gpu_temp(TEMP_READ_ERROR_VALUE)
        self.state.set_gpu_asleep(False)

    def _on_cycle_watchdog(self):
        """Cycle exceeded CYCLE_WATCHDOG_MS: the worker is most likely stuck in a COM call. Rebuild the interface instead of freezing."""
        if self._is_shutting_down or not self._cycle_in_flight:
            return
        self._cycle_in_flight = False
        self._pending_cycle = None
        try:
            self.wmi_interface.stop(join_timeout=1.0)
        except Exception:
            pass
        if not self.wmi_interface.start(init_timeout=3.0):
            self.state.set_controller_status_message(tr("wmi_error"))

    @Slot()
    def on_gui_tick(self):
        """Triggered by the UI timer to refresh the sensor data shown in the UI."""
        if self._is_shutting_down:
            return
        if not self.wmi_interface.is_running:
            self._recover_wmi_if_due()
            return
        if not self._is_ui_visible:
            return
        
        self.wmi_interface.request_core_sensor_poll()
        sensor_data = self.wmi_interface.get_latest_core_sensor_data()
        self._update_state_from_sensor_data(sensor_data)

    @Slot()
    def perform_full_status_update(self):
        """Run one global, blocking WMI query. Returns the sensor dict on success."""
        try:
            sensor_data = self.wmi_interface.get_all_sensors_sync()
            self._update_state_from_sensor_data(sensor_data)
            return sensor_data
        except WMIError as e:
            print(f"WMI operation failed in 'perform_full_status_update': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
            return None

    def _update_state_from_sensor_data(self, sensor_data: dict):
        """Update AppState from WMI data via reflection, handling the special mappings correctly."""
        if not sensor_data: return
        
        key_map = {
            'charge_policy': 'applied_charge_policy',
            'charge_threshold': 'applied_charge_threshold'
        }

        for key, value in sensor_data.items():
            state_key = key_map.get(key, key)
            setter_name = f"set_{state_key}"
            
            if hasattr(self.state, setter_name):
                if state_key == 'applied_charge_policy':
                    value = BATTERY_CODE_POLICIES.get(value, "err")
                getattr(self.state, setter_name)(value)

    def set_ui_visibility(self, is_visible: bool):
        """Called when UI visibility changes."""
        self._is_ui_visible = is_visible
        if is_visible:
            self.perform_full_status_update()