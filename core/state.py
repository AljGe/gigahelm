# -*- coding: utf-8 -*-
"""
定义了应用的中心化、响应式状态。
所有状态类都继承自QObject，并使用Qt的属性系统(@Property)来在其值改变时自动发出信号。
这使得UI组件可以精确地订阅它们所关心的数据变化，实现最高效的更新。
"""
import copy
from gui.qt import QObject, Signal, Property, Slot
from typing import List, Dict, Optional, Any, cast
from config.settings import (
    DEFAULT_PROFILE_SETTINGS, DEFAULT_THEME_MODE, THEME_MODES,
    FAN_MODE_BIOS, FAN_MODE_AUTO, FAN_MODE_CUSTOM, FAN_MODE_UNKNOWN,
    BATTERY_POLICY_CODES, MIN_FAN_PERCENT, MAX_FAN_PERCENT,
    MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT, MIN_CURVE_POINTS,
    TEMP_READ_ERROR_VALUE,
)
from core.interpolation import clip
from core.path_manager import PathManager


def _as_number(value: Any) -> Optional[float]:
    """Numeric (non-bool) value as float, else None. Config data is untrusted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _valid_curve_points(value: Any) -> List[List[Any]]:
    """2-element numeric [temp, speed] rows; speed clipped to 0..100.
    Temperatures are kept as-is (AutoTemperatureController sorts/validates)."""
    points: List[List[Any]] = []
    if isinstance(value, list):
        for point in value:
            if (isinstance(point, (list, tuple)) and len(point) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in point)):
                points.append([point[0],
                               int(round(clip(point[1], MIN_FAN_PERCENT, MAX_FAN_PERCENT)))])
    return points


def _sanitize_profile_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Hand-edited or corrupted control_config.json must never produce a
    zombie fan mode, an unmapped charge policy, or an empty curve: every
    behavioral key is validated; anything invalid falls back to the shipped
    default (DEFAULT_PROFILE_SETTINGS). Appearance keys pass through as before."""
    final = copy.deepcopy(DEFAULT_PROFILE_SETTINGS)
    for key in DEFAULT_PROFILE_SETTINGS:
        if key not in data:
            continue
        value = data[key]
        if key == "fan_mode":
            if value in (FAN_MODE_BIOS, FAN_MODE_AUTO, FAN_MODE_CUSTOM):
                final[key] = value
        elif key == "battery_charge_policy":
            if value in BATTERY_POLICY_CODES:
                final[key] = value
        elif key == "custom_fan_speed":
            number = _as_number(value)
            if number is not None:
                final[key] = int(round(clip(number, MIN_FAN_PERCENT, MAX_FAN_PERCENT)))
        elif key == "battery_charge_threshold":
            number = _as_number(value)
            if number is not None:
                final[key] = int(round(clip(number, MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT)))
        elif key in ("cpu_fan_table", "gpu_fan_table"):
            points = _valid_curve_points(value)
            if len(points) >= MIN_CURVE_POINTS:
                final[key] = points
        elif key in ("gpu_power_feedforward", "anti_beat_detune",
                     "fan_load_assist", "fan_twin_wake"):
            if isinstance(value, bool):
                final[key] = value
        else:
            final[key] = value
    return final

class ProfileState(QObject):
    """
    表示单个配置文件的响应式设置集。
    每个属性的更改都会发出一个特定的信号。
    """
    fan_mode_changed = Signal(str)
    custom_fan_speed_changed = Signal(int)
    battery_charge_policy_changed = Signal(str)
    battery_charge_threshold_changed = Signal(int)
    cpu_fan_table_changed = Signal(list)
    gpu_fan_table_changed = Signal(list)
    appearance_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._data = copy.deepcopy(DEFAULT_PROFILE_SETTINGS)

    def _set_value(self, key: str, value: Any, signal: Any):
        if self._data.get(key) != value:
            self._data[key] = value
            signal.emit(value)

    def get_value(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set_value(self, key: str, value: Any):
        """Generic setter for profile keys without a typed property (behavior
        flags like gpu_power_feedforward); announces via appearance_changed."""
        if self._data.get(key) != value:
            self._data[key] = value
            self.appearance_changed.emit()

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def from_dict(self, data: Dict[str, Any]):
        final_data = _sanitize_profile_data(data)

        self.set_fan_mode(final_data['fan_mode'])
        self.set_custom_fan_speed(final_data['custom_fan_speed'])
        self.set_battery_charge_policy(final_data['battery_charge_policy'])
        self.set_battery_charge_threshold(final_data['battery_charge_threshold'])
        self.set_cpu_fan_table(final_data['cpu_fan_table'])
        self.set_gpu_fan_table(final_data['gpu_fan_table'])

        appearance_changed = False
        for key, value in final_data.items():
            if key in ['fan_mode', 'custom_fan_speed', 'battery_charge_policy', 'battery_charge_threshold', 'cpu_fan_table', 'gpu_fan_table']:
                continue
            if self._data.get(key) != value:
                self._data[key] = value
                appearance_changed = True

        if appearance_changed:
            self.appearance_changed.emit()

    def get_fan_mode(self) -> str: return cast(str, self._data['fan_mode'])
    def set_fan_mode(self, value: str): self._set_value('fan_mode', value, self.fan_mode_changed)
    fan_mode = Property(str, get_fan_mode, set_fan_mode, notify=fan_mode_changed) # type: ignore

    def get_custom_fan_speed(self) -> int: return cast(int, self._data['custom_fan_speed'])
    def set_custom_fan_speed(self, value: int): self._set_value('custom_fan_speed', value, self.custom_fan_speed_changed)
    custom_fan_speed = Property(int, get_custom_fan_speed, set_custom_fan_speed, notify=custom_fan_speed_changed) # type: ignore

    def get_battery_charge_policy(self) -> str: return cast(str, self._data['battery_charge_policy'])
    def set_battery_charge_policy(self, value: str): self._set_value('battery_charge_policy', value, self.battery_charge_policy_changed)
    battery_charge_policy = Property(str, get_battery_charge_policy, set_battery_charge_policy, notify=battery_charge_policy_changed) # type: ignore

    def get_battery_charge_threshold(self) -> int: return cast(int, self._data['battery_charge_threshold'])
    def set_battery_charge_threshold(self, value: int): self._set_value('battery_charge_threshold', value, self.battery_charge_threshold_changed)
    battery_charge_threshold = Property(int, get_battery_charge_threshold, set_battery_charge_threshold, notify=battery_charge_threshold_changed) # type: ignore

    def get_cpu_fan_table(self) -> List[List[int]]: return cast(List[List[int]], self._data['cpu_fan_table'])
    def set_cpu_fan_table(self, value: List[List[int]]): self._set_value('cpu_fan_table', value, self.cpu_fan_table_changed)
    cpu_fan_table = Property(list, get_cpu_fan_table, set_cpu_fan_table, notify=cpu_fan_table_changed) # type: ignore

    def get_gpu_fan_table(self) -> List[List[int]]: return cast(List[List[int]], self._data['gpu_fan_table'])
    def set_gpu_fan_table(self, value: List[List[int]]): self._set_value('gpu_fan_table', value, self.gpu_fan_table_changed)
    gpu_fan_table = Property(list, get_gpu_fan_table, set_gpu_fan_table, notify=gpu_fan_table_changed) # type: ignore


class AppState(QObject):
    """
    整个应用的顶层响应式状态对象。
    这是所有持久化和瞬时数据的唯一真实来源。
    """
    language_changed = Signal(str)
    theme_mode_changed = Signal(str)
    start_on_boot_changed = Signal(bool)
    notifications_enabled_changed = Signal(bool)
    profile_on_ac_changed = Signal(str)
    profile_on_battery_changed = Signal(str)
    active_profile_changed = Signal(ProfileState)
    profiles_list_changed = Signal(list, str) # names, active_name

    cpu_temp_changed = Signal(float)
    gpu_temp_changed = Signal(float)
    # EMA-smoothed control temps (TEMP_SMOOTH_ALPHA): display-only mirror of
    # AppServices._cpu_ctrl_temp/_gpu_ctrl_temp for the curve canvas.
    cpu_ctrl_temp_changed = Signal(float)
    gpu_ctrl_temp_changed = Signal(float)
    gpu_asleep_changed = Signal(bool)
    # NVML GPU utilization percent, -1 = unknown (asleep / poll paused).
    gpu_util_changed = Signal(int)
    # 重命名以匹配硬件实现，消除业务逻辑层的转化
    fan1_rpm_changed = Signal(int)
    fan2_rpm_changed = Signal(int)
    ec_fixed_fan_speed_raw_changed = Signal(int)
    battery_health_changed = Signal(int)
    battery_cycles_changed = Signal(int)
    battery_percent_changed = Signal(int)
    applied_fan_mode_changed = Signal(str)
    applied_fan_speed_percent_changed = Signal(int)
    applied_gpu_fan_percent_changed = Signal(int)
    auto_fan_target_speed_percent_changed = Signal(int)
    applied_charge_policy_changed = Signal(str)
    applied_charge_threshold_changed = Signal(int)
    is_fan_control_panel_enabled_changed = Signal(bool)
    active_curve_type_changed = Signal(str)
    controller_status_message_changed = Signal(str)
    # Global UI refresh cadence (ms); migrated out of per-profile storage.
    gui_update_interval_ms_changed = Signal(int)

    def __init__(self, path_manager: PathManager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.paths = path_manager
        self._language: str = "en"
        self._theme_mode: str = DEFAULT_THEME_MODE
        self._start_on_boot: bool = False
        # Balloon confirmations for profile/charge actions; fault + thermal
        # alerts intentionally ignore this flag.
        self._notifications_enabled: bool = True
        # Optional profile names to switch to on AC attach / battery power
        # ("" = feature off for that source).
        self._profile_on_ac: str = ""
        self._profile_on_battery: str = ""
        self._active_profile_name: Optional[str] = None
        self._profiles: Dict[str, ProfileState] = {}
        self.profile_order: List[str] = []
        self.window_geometry: Optional[str] = None

        # 瞬时状态
        self._cpu_temp: float = 0.0
        self._gpu_temp: float = 0.0
        # EMA control temps as seen by the controller; TEMP_READ_ERROR_VALUE
        # until the first valid control cycle (canvas hides the marker).
        self._cpu_ctrl_temp: float = TEMP_READ_ERROR_VALUE
        self._gpu_ctrl_temp: float = TEMP_READ_ERROR_VALUE
        self._gpu_asleep: bool = False
        self._gpu_util: int = -1
        # 重命名
        self._fan1_rpm: int = 0
        self._fan2_rpm: int = 0
        self._ec_fixed_fan_speed_raw: int = -1
        self._battery_health: int = -1
        self._battery_cycles: int = -1
        self._battery_percent: int = -1
        self._applied_fan_mode: str = FAN_MODE_UNKNOWN
        self._applied_fan_speed_percent: int = 0
        self._applied_gpu_fan_percent: int = 0
        self._auto_fan_target_speed_percent: int = 0
        self._applied_charge_policy: str = "bios"
        self._applied_charge_threshold: int = -1
        self._is_fan_control_panel_enabled: bool = True
        self._active_curve_type: str = "cpu"
        self._controller_status_message: str = ""
        # Persistent global app setting (was a per-profile key before the
        # migration in ProfileManager.load_config).
        self._gui_update_interval_ms: int = 1000

    def _set_value(self, field_name: str, value: Any, signal: Any):
        if getattr(self, field_name) != value:
            setattr(self, field_name, value)
            signal.emit(value)

    # --- 持久化状态属性 ---
    def get_language(self) -> str: return self._language
    def set_language(self, value: str): self._set_value('_language', value, self.language_changed)
    language = Property(str, get_language, set_language, notify=language_changed) # type: ignore

    def get_theme_mode(self) -> str: return self._theme_mode
    def set_theme_mode(self, value: str):
        self._set_value('_theme_mode', value if value in THEME_MODES else DEFAULT_THEME_MODE, self.theme_mode_changed)
    theme_mode = Property(str, get_theme_mode, set_theme_mode, notify=theme_mode_changed) # type: ignore

    def get_start_on_boot(self) -> bool: return self._start_on_boot
    def set_start_on_boot(self, value: bool): self._set_value('_start_on_boot', value, self.start_on_boot_changed)
    start_on_boot = Property(bool, get_start_on_boot, set_start_on_boot, notify=start_on_boot_changed) # type: ignore

    # Balloon confirmations for profile/charge actions; fault + thermal
    # alerts intentionally ignore this flag.
    def get_notifications_enabled(self) -> bool: return self._notifications_enabled
    def set_notifications_enabled(self, value: bool): self._set_value('_notifications_enabled', value, self.notifications_enabled_changed)
    notifications_enabled = Property(bool, get_notifications_enabled, set_notifications_enabled, notify=notifications_enabled_changed) # type: ignore

    def get_profile_on_ac(self) -> str: return self._profile_on_ac
    def set_profile_on_ac(self, value: str): self._set_value('_profile_on_ac', value or "", self.profile_on_ac_changed)
    profile_on_ac = Property(str, get_profile_on_ac, set_profile_on_ac, notify=profile_on_ac_changed) # type: ignore

    def get_profile_on_battery(self) -> str: return self._profile_on_battery
    def set_profile_on_battery(self, value: str): self._set_value('_profile_on_battery', value or "", self.profile_on_battery_changed)
    profile_on_battery = Property(str, get_profile_on_battery, set_profile_on_battery, notify=profile_on_battery_changed) # type: ignore

    def get_active_profile(self) -> Optional[ProfileState]: return self._profiles.get(self._active_profile_name) if self._active_profile_name else None
    active_profile = Property(QObject, get_active_profile, notify=active_profile_changed) # type: ignore

    def get_active_profile_name(self) -> Optional[str]: return self._active_profile_name
    def set_active_profile_name(self, value: str):
        if self._active_profile_name != value and value in self._profiles:
            self._active_profile_name = value
            active_profile = self.get_active_profile()
            if active_profile:
                self.active_profile_changed.emit(active_profile)
            self.profiles_list_changed.emit(self.get_profile_names(), self._active_profile_name or "")
    active_profile_name = Property(str, get_active_profile_name, set_active_profile_name, notify=active_profile_changed) # type: ignore

    def get_profile_names(self) -> List[str]: return self.profile_order
    profile_names = Property(list, get_profile_names, notify=profiles_list_changed) # type: ignore

    # --- 瞬时状态属性 ---
    def get_cpu_temp(self) -> float: return self._cpu_temp
    def set_cpu_temp(self, value: float): self._set_value('_cpu_temp', value, self.cpu_temp_changed)
    cpu_temp = Property(float, get_cpu_temp, set_cpu_temp, notify=cpu_temp_changed) # type: ignore

    def get_gpu_temp(self) -> float: return self._gpu_temp
    def set_gpu_temp(self, value: float): self._set_value('_gpu_temp', value, self.gpu_temp_changed)
    gpu_temp = Property(float, get_gpu_temp, set_gpu_temp, notify=gpu_temp_changed) # type: ignore

    def get_cpu_ctrl_temp(self) -> float: return self._cpu_ctrl_temp
    def set_cpu_ctrl_temp(self, value: float): self._set_value('_cpu_ctrl_temp', value, self.cpu_ctrl_temp_changed)
    cpu_ctrl_temp = Property(float, get_cpu_ctrl_temp, set_cpu_ctrl_temp, notify=cpu_ctrl_temp_changed) # type: ignore

    def get_gpu_ctrl_temp(self) -> float: return self._gpu_ctrl_temp
    def set_gpu_ctrl_temp(self, value: float): self._set_value('_gpu_ctrl_temp', value, self.gpu_ctrl_temp_changed)
    gpu_ctrl_temp = Property(float, get_gpu_ctrl_temp, set_gpu_ctrl_temp, notify=gpu_ctrl_temp_changed) # type: ignore

    def get_gpu_asleep(self) -> bool: return self._gpu_asleep
    def set_gpu_asleep(self, value: bool): self._set_value('_gpu_asleep', value, self.gpu_asleep_changed)
    gpu_asleep = Property(bool, get_gpu_asleep, set_gpu_asleep, notify=gpu_asleep_changed) # type: ignore

    def get_gpu_util(self) -> int: return self._gpu_util
    def set_gpu_util(self, value: int): self._set_value('_gpu_util', int(value), self.gpu_util_changed)
    gpu_util = Property(int, get_gpu_util, set_gpu_util, notify=gpu_util_changed) # type: ignore

    # 重命名
    def get_fan1_rpm(self) -> int: return self._fan1_rpm
    def set_fan1_rpm(self, value: int): self._set_value('_fan1_rpm', value, self.fan1_rpm_changed)
    fan1_rpm = Property(int, get_fan1_rpm, set_fan1_rpm, notify=fan1_rpm_changed) # type: ignore

    def get_fan2_rpm(self) -> int: return self._fan2_rpm
    def set_fan2_rpm(self, value: int): self._set_value('_fan2_rpm', value, self.fan2_rpm_changed)
    fan2_rpm = Property(int, get_fan2_rpm, set_fan2_rpm, notify=fan2_rpm_changed) # type: ignore
    def get_ec_fixed_fan_speed_raw(self) -> int: return self._ec_fixed_fan_speed_raw
    def set_ec_fixed_fan_speed_raw(self, value: int): self._set_value('_ec_fixed_fan_speed_raw', value, self.ec_fixed_fan_speed_raw_changed)
    ec_fixed_fan_speed_raw = Property(int, get_ec_fixed_fan_speed_raw, set_ec_fixed_fan_speed_raw, notify=ec_fixed_fan_speed_raw_changed) # type: ignore

    def get_battery_health(self) -> int: return self._battery_health
    def set_battery_health(self, value: int): self._set_value('_battery_health', value, self.battery_health_changed)
    battery_health = Property(int, get_battery_health, set_battery_health, notify=battery_health_changed) # type: ignore

    def get_battery_cycles(self) -> int: return self._battery_cycles
    def set_battery_cycles(self, value: int): self._set_value('_battery_cycles', value, self.battery_cycles_changed)
    battery_cycles = Property(int, get_battery_cycles, set_battery_cycles, notify=battery_cycles_changed) # type: ignore

    def get_battery_percent(self) -> int: return self._battery_percent
    def set_battery_percent(self, value: int): self._set_value('_battery_percent', value, self.battery_percent_changed)
    battery_percent = Property(int, get_battery_percent, set_battery_percent, notify=battery_percent_changed) # type: ignore

    def get_applied_fan_mode(self) -> str: return self._applied_fan_mode
    def set_applied_fan_mode(self, value: str): self._set_value('_applied_fan_mode', value, self.applied_fan_mode_changed)
    applied_fan_mode = Property(str, get_applied_fan_mode, set_applied_fan_mode, notify=applied_fan_mode_changed) # type: ignore

    def get_applied_fan_speed_percent(self) -> int: return self._applied_fan_speed_percent
    def set_applied_fan_speed_percent(self, value: int): self._set_value('_applied_fan_speed_percent', value, self.applied_fan_speed_percent_changed)
    applied_fan_speed_percent = Property(int, get_applied_fan_speed_percent, set_applied_fan_speed_percent, notify=applied_fan_speed_percent_changed) # type: ignore

    def get_applied_gpu_fan_percent(self) -> int: return self._applied_gpu_fan_percent
    def set_applied_gpu_fan_percent(self, value: int): self._set_value('_applied_gpu_fan_percent', value, self.applied_gpu_fan_percent_changed)
    applied_gpu_fan_percent = Property(int, get_applied_gpu_fan_percent, set_applied_gpu_fan_percent, notify=applied_gpu_fan_percent_changed) # type: ignore

    def get_auto_fan_target_speed_percent(self) -> int: return self._auto_fan_target_speed_percent
    def set_auto_fan_target_speed_percent(self, value: int): self._set_value('_auto_fan_target_speed_percent', value, self.auto_fan_target_speed_percent_changed)
    auto_fan_target_speed_percent = Property(int, get_auto_fan_target_speed_percent, set_auto_fan_target_speed_percent, notify=auto_fan_target_speed_percent_changed) # type: ignore

    def get_applied_charge_policy(self) -> str: return self._applied_charge_policy
    def set_applied_charge_policy(self, value: str): self._set_value('_applied_charge_policy', value, self.applied_charge_policy_changed)
    applied_charge_policy = Property(str, get_applied_charge_policy, set_applied_charge_policy, notify=applied_charge_policy_changed) # type: ignore

    def get_applied_charge_threshold(self) -> int: return self._applied_charge_threshold
    def set_applied_charge_threshold(self, value: int): self._set_value('_applied_charge_threshold', value, self.applied_charge_threshold_changed)
    applied_charge_threshold = Property(int, get_applied_charge_threshold, set_applied_charge_threshold, notify=applied_charge_threshold_changed) # type: ignore

    def get_is_fan_control_panel_enabled(self) -> bool: return self._is_fan_control_panel_enabled
    def set_is_fan_control_panel_enabled(self, value: bool): self._set_value('_is_fan_control_panel_enabled', value, self.is_fan_control_panel_enabled_changed)
    is_fan_control_panel_enabled = Property(bool, get_is_fan_control_panel_enabled, set_is_fan_control_panel_enabled, notify=is_fan_control_panel_enabled_changed) # type: ignore

    def get_active_curve_type(self) -> str: return self._active_curve_type
    def set_active_curve_type(self, value: str): self._set_value('_active_curve_type', value, self.active_curve_type_changed)
    active_curve_type = Property(str, get_active_curve_type, set_active_curve_type, notify=active_curve_type_changed) # type: ignore

    def get_controller_status_message(self) -> str: return self._controller_status_message
    def set_controller_status_message(self, value: str): self._set_value('_controller_status_message', value, self.controller_status_message_changed)
    controller_status_message = Property(str, get_controller_status_message, set_controller_status_message, notify=controller_status_message_changed) # type: ignore

    def get_gui_update_interval_ms(self) -> int: return self._gui_update_interval_ms
    def set_gui_update_interval_ms(self, value: int): self._set_value('_gui_update_interval_ms', value, self.gui_update_interval_ms_changed)
    gui_update_interval_ms = Property(int, get_gui_update_interval_ms, set_gui_update_interval_ms, notify=gui_update_interval_ms_changed) # type: ignore

    # --- 配置文件管理方法 ---
    def get_profile(self, name: str) -> Optional[ProfileState]:
        return self._profiles.get(name)

    def add_profile(self, name: str, profile: ProfileState):
        if name not in self._profiles:
            self._profiles[name] = profile
            self.profile_order.append(name)
            self.profiles_list_changed.emit(self.get_profile_names(), self.active_profile_name or "")

    def rename_profile(self, old_name: str, new_name: str):
        if old_name in self._profiles and new_name not in self._profiles:
            profile = self._profiles.pop(old_name)
            self._profiles[new_name] = profile
            try:
                idx = self.profile_order.index(old_name)
                self.profile_order[idx] = new_name
            except ValueError:
                self.profile_order.append(new_name)

            if self.active_profile_name == old_name:
                self.set_active_profile_name(new_name)
            else:
                self.profiles_list_changed.emit(self.get_profile_names(), self.active_profile_name or "")

    def delete_profile(self, name: str):
        if name in self._profiles and len(self._profiles) > 1:
            del self._profiles[name]
            if name in self.profile_order:
                self.profile_order.remove(name)

            if self.active_profile_name == name:
                new_active_name = self.profile_order[0] if self.profile_order else None
                if new_active_name:
                    self.set_active_profile_name(new_active_name)
            else:
                self.profiles_list_changed.emit(self.get_profile_names(), self.active_profile_name or "")

    def get_profiles_for_config(self) -> Dict[str, Any]:
        return {name: profile.to_dict() for name, profile in self._profiles.items()}

    def load_profiles_from_config(self, profiles_data: Dict[str, Any], profile_order: List[str]):
        for old in self._profiles.values():
            old.deleteLater()
        self._profiles.clear()
        self.profile_order = profile_order
        for name, profile_dict in profiles_data.items():
            profile = ProfileState(self)
            profile.from_dict(profile_dict)
            self._profiles[name] = profile
