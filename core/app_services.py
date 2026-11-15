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
from .fan_duty import percent_to_raw
from .fan_ramp import next_fan_percent
from tools.localization import tr
from config.settings import (
    FAN_MODE_BIOS, FAN_MODE_AUTO, FAN_MODE_CUSTOM,
    CHARGE_POLICY_CUSTOM, BATTERY_POLICY_CODES, BATTERY_CODE_POLICIES,
    INIT_APPLIED_PERCENTAGE, TEMP_READ_ERROR_VALUE,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_STATUS,
    SET_CHARGE_POLICY, SET_CHARGE_STOP, WMIInternalSignal
)

# H4 keepalive: reassert the software-control EC flags at least this often (cycles).
FLAG_REASSERT_EVERY_CYCLES = 8
# H3 safety: a control cycle that takes longer than this means the worker is hung.
CYCLE_WATCHDOG_MS = 8000

FanTable = List[List[int]]

class AppServices(QObject):
    """中心化服务层，管理所有后端控制器并响应应用状态的变化。"""

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
        self._cycle_in_flight = False
        self._cycles_since_flag_reassert = 0
        self._pending_cycle: Optional[Dict[str, int]] = None
        self._cycle_watchdog = QTimer(self)
        self._cycle_watchdog.setSingleShot(True)
        self._cycle_watchdog.timeout.connect(self._on_cycle_watchdog)
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self.recover_after_resume)

        self.state.set_controller_status_message(tr("initializing"))
        self._is_ui_visible: bool = False
        
        # 为崩溃安全机制定义状态文件路径
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
            self._arm_software_ramp()

    def _update_controller_settings(self):
        if self._current_profile:
            self.auto_temp_controller.update_auto_settings(self._current_profile)
            
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
        """执行优雅关机程序。崩溃紧急锁存后不再把风扇交还 BIOS。"""
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
        """执行将风扇控制权交还给BIOS所需的WMI命令序列。"""
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
        """重建 WMI。自动模式在恢复后立刻写一次风扇速度。"""
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
        self._arm_software_ramp()
        mode = profile.get_fan_mode()
        self.set_fan_mode(mode)
        if mode == FAN_MODE_AUTO:
            self._perform_control_cycle()

    def _write_last_mode_state(self, mode: str):
        """将当前风扇模式写入状态文件，用于崩溃恢复。"""
        try:
            with open(self._last_mode_file_path, 'w') as f:
                f.write(mode)
        except IOError as e:
            print(f"Warning: could not write 'last_mode.state' file: {e}", file=sys.stderr)

    @Slot(str)
    def set_fan_mode(self, mode: str):
        """设置风扇控制模式。自动模式的第一次写入留给控制节拍。"""
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
        """自定义滑块变化走同一条斜坡写入路径。"""
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
        """设置电池充电策略。不写入策略码 1。"""
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
        """设置电池充电阈值，延迟回读，不阻塞 UI 线程。"""
        if not force_apply and (not self._current_profile or self.state.get_applied_charge_policy() != CHARGE_POLICY_CUSTOM):
            return
        try:
            self.wmi_interface.execute_method(SET_CHARGE_STOP, Data=float(threshold))
            self._schedule_charge_readback(int(threshold))
        except WMIError as e:
            print(f"WMI operation failed in 'set_battery_charge_threshold': {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("wmi_error"))
    def _select_fan_target(self, mode: str, cpu_temp: float, gpu_temp: float, gpu_asleep: bool = False) -> int:
        if mode == FAN_MODE_CUSTOM and self._current_profile:
            base = int(self._current_profile.get_custom_fan_speed())
        else:
            base = self.auto_temp_controller.theoretical_target(cpu_temp, gpu_temp)
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
        """心跳（非阻塞）：向 WMI worker 提交一次温度读取，回调里决定目标并异步写入。
        H3：GUI 线程不再做任何阻塞的 WMI 调用。"""
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
            target = self._select_fan_target(mode, cpu_temp, gpu_temp, gpu_asleep)
            percent, self._kick_armed, self._spinning_down = next_fan_percent(
                self.state.get_applied_fan_speed_percent(),
                target,
                self._kick_armed,
                self._spinning_down,
            )
            changed = percent != self.state.get_applied_fan_speed_percent()
            self._cycles_since_flag_reassert += 1
            if changed or self._cycles_since_flag_reassert >= FLAG_REASSERT_EVERY_CYCLES:
                # H4：仅在占空比变化或 keepalive 到期时写 EC（reassert 同时重写占空比，双保险）。
                self._pending_cycle = {"target": target, "percent": percent}
                duty_raw = float(percent_to_raw(percent))
                self.wmi_interface.submit_async(
                    WMIInternalSignal.APPLY_FAN_CONTROL,
                    {"duty_raw": duty_raw, "reassert_flags": True},
                    on_done=self._on_cycle_applied,
                )
                return  # in_flight 保持 True，watchdog 继续看护
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
        # 飞行中用户切到 BIOS：立即纠正刚写入的软件标志，避免残留 custom 控制。
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
        """周期超过 CYCLE_WATCHDOG_MS：worker 多半卡死在 COM 调用里。重建接口而不是冻结。"""
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
        """由UI定时器触发，用于刷新UI显示的传感器数据。"""
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
        """使用反射机制，并正确处理特殊映射，用从WMI获取的数据更新AppState。"""
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
        """当UI可见性变化时调用。"""
        self._is_ui_visible = is_visible
        if is_visible:
            self.perform_full_status_update()