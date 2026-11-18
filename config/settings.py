# -*- coding: utf-8 -*-
"""
风扇和电池控制应用的全局常量和默认设置。
"""
from typing import List, Dict, Any

# ==============================================================================
# 应用信息
# ==============================================================================
APP_NAME: str = "Gigahelm"
APP_ORGANIZATION_NAME: str = "Gigahelm" # Qt用于设置路径
APP_INTERNAL_NAME: str = "Gigahelm" # 用于互斥锁、任务名称等

# ==============================================================================
# 默认配置值
# ==============================================================================
DEFAULT_LANGUAGE: str = "en" # 默认语言代码 (ISO 639-1)

# --- 外观主题 ---
THEME_MODE_SYSTEM = "system" # 跟随 Windows 应用主题
THEME_MODE_DARK = "dark"
THEME_MODE_LIGHT = "light"
THEME_MODES = (THEME_MODE_SYSTEM, THEME_MODE_DARK, THEME_MODE_LIGHT)
DEFAULT_THEME_MODE: str = THEME_MODE_SYSTEM

# --- 版本 (页脚显示) ---
APP_VERSION: str = "2.3.1"

# --- 核心状态常量 ---
FAN_MODE_BIOS = "bios"
FAN_MODE_AUTO = "auto" # 软件控制的曲线
FAN_MODE_CUSTOM = "custom" # 软件控制的自定义速度
FAN_MODE_UNKNOWN = "unknown"
CHARGE_POLICY_BIOS = "bios"
CHARGE_POLICY_CUSTOM = "custom"

# --- 电池控制常量 ---
BATTERY_POLICY_CODES: Dict[str, int] = {"bios": 0, "custom": 4}
BATTERY_CODE_POLICIES: Dict[int, str] = {0: "bios", 1: "custom", 4: "custom"}

# --- 默认配置文件设置 ---
# 定义了 *每个* 配置文件的结构和默认值
# EC floor rule: percent_to_raw maps every 1-24 % request to the 25 %
# minimum-spin duty, so points between 0 and 25 are fiction - use 0 or >= 25.
DEFAULT_CPU_FAN_TABLE: List[List[int]] = [[50, 0], [58, 0], [60, 30], [70, 30], [80, 40], [85, 60], [90, 80], [95, 100]]
DEFAULT_GPU_FAN_TABLE: List[List[int]] = [[40, 0], [55, 0], [63, 0], [65, 30], [70, 40], [75, 60], [80, 80], [85, 100]]
DEFAULT_FAN_MODE: str = FAN_MODE_AUTO
DEFAULT_CUSTOM_FAN_SPEED: int = 30 # 百分比
DEFAULT_CHARGE_POLICY: str = CHARGE_POLICY_BIOS
DEFAULT_CHARGE_THRESHOLD: int = 80 # 百分比

# --- 默认控制逻辑和时序 (每个配置文件) ---
DEFAULT_GUI_UPDATE_INTERVAL_MS: int = 1000 # GUI刷新状态的频率
DEFAULT_CONTROLLER_UPDATE_INTERVAL_MS: int = 1500 # 在自动模式下调整风扇速度朝向目标的频率
DEFAULT_TARGET_HYSTERESIS_PERCENT: int = 2 # 目标速度变化小于该值时沿用旧目标，避免 1% 抖动

# --- 默认曲线绘制和UI外观 (每个配置文件) ---
DEFAULT_MIN_DISPLAY_TEMP_C: int = 40 # 曲线图X轴显示的最小温度
DEFAULT_CURVE_POINT_PICKER_RADIUS: float = 8.0 # 图上点的点击半径
DEFAULT_SPLINE_POINTS: int = 100 # 平滑曲线插值线的点数
DEFAULT_POINT_SIZE_ACTIVE: int = 7
DEFAULT_LINE_WIDTH_ACTIVE: float = 2.0
DEFAULT_LINE_WIDTH_INACTIVE: float = 1.0
DEFAULT_ALPHA_ACTIVE: float = 1.0
DEFAULT_ALPHA_INACTIVE: float = 0.4

# --- 将所有默认配置文件设置合并到一个字典中 ---
DEFAULT_PROFILE_SETTINGS: Dict[str, Any] = {
    "cpu_fan_table": [list(p) for p in DEFAULT_CPU_FAN_TABLE],
    "gpu_fan_table": [list(p) for p in DEFAULT_GPU_FAN_TABLE],
    "fan_mode": DEFAULT_FAN_MODE,
    "custom_fan_speed": DEFAULT_CUSTOM_FAN_SPEED,
    "battery_charge_policy": DEFAULT_CHARGE_POLICY,
    "battery_charge_threshold": DEFAULT_CHARGE_THRESHOLD,
    "gpu_power_feedforward": True,
    "split_fan_control": True,
    "anti_beat_detune": True,
    "fan_load_assist": True,
    "controller_update_interval_ms": DEFAULT_CONTROLLER_UPDATE_INTERVAL_MS,
    "min_display_temp_c": DEFAULT_MIN_DISPLAY_TEMP_C,
    "curve_point_picker_radius": DEFAULT_CURVE_POINT_PICKER_RADIUS,
    "spline_points": DEFAULT_SPLINE_POINTS,
    "point_size_active": DEFAULT_POINT_SIZE_ACTIVE,
    "line_width_active": DEFAULT_LINE_WIDTH_ACTIVE,
    "line_width_inactive": DEFAULT_LINE_WIDTH_INACTIVE,
    "alpha_active": DEFAULT_ALPHA_ACTIVE,
    "alpha_inactive": DEFAULT_ALPHA_INACTIVE,
}

# --- 出厂配置方案 ---
# The shipped profile set (this machine's bench-tuned curves). A config with
# no profiles (fresh install, deleted config) regenerates these, so an
# accidental reset loses nothing that a restart can't restore.
# EC floor rule: the AORUS 15P YD stalls below raw duty 57 (= 25 %), and
# percent_to_raw maps every 1-24 % request onto that same 25 % duty, so curve
# points between 0 and 25 are fiction - author every point as 0 or >= 30.
# Whistle sweep 2026-11-18: park at 30 % (raw 69) - beat-free, stable; and
# keep a 0-plateau up to ~2 degC below the spin-onset knee so idle temps map
# to fan-off instead of lingering in the raw 57-62 beat zone.
AC_CPU_FAN_TABLE: List[List[int]] = [
    [50, 0], [55, 0], [60, 0], [65, 0], [68, 30], [72, 35],
    [74, 45], [76, 60], [78, 75], [80, 85], [82, 88], [84, 90], [88, 95], [95, 95],
]
AC_GPU_FAN_TABLE: List[List[int]] = [
    [40, 0], [55, 0], [63, 0], [65, 30], [73, 35], [78, 53], [83, 74], [89, 99],
]
BATT_CPU_FAN_TABLE: List[List[int]] = [
    [50, 0], [58, 0], [60, 30], [63, 35], [66, 45], [70, 55], [73, 65],
    [75, 70], [77, 75], [80, 80], [83, 85], [86, 90], [90, 95], [95, 95],
]


def _default_profile(**overrides: Any) -> Dict[str, Any]:
    import copy
    profile = copy.deepcopy(DEFAULT_PROFILE_SETTINGS)
    # overrides may hold module-level table lists shared between profiles;
    # copy them so profiles never alias the same mutable table.
    profile.update(copy.deepcopy(overrides))
    return profile


DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "Tuned AC": _default_profile(
        cpu_fan_table=AC_CPU_FAN_TABLE,
        gpu_fan_table=AC_GPU_FAN_TABLE,
        fan_mode="auto",
        battery_charge_policy=CHARGE_POLICY_BIOS,
        battery_charge_threshold=80,
    ),
    "Tuned Battery": _default_profile(
        cpu_fan_table=BATT_CPU_FAN_TABLE,
        gpu_fan_table=AC_GPU_FAN_TABLE,
        fan_mode="auto",
        battery_charge_policy=CHARGE_POLICY_CUSTOM,
        battery_charge_threshold=80,
    ),
}

# ==============================================================================
# WMI 配置
# ==============================================================================
WMI_NAMESPACE: str = r"root\WMI"
DEFAULT_WMI_GET_CLASS: str = "GB_WMIACPI_Get"
DEFAULT_WMI_SET_CLASS: str = "GB_WMIACPI_Set"
WMI_REQUEST_TIMEOUT_S: float = 5.0
# 异步请求结果的最长等待时间；超过后回调超时并回收 QTimer。
WMI_ASYNC_RESULT_TIMEOUT_S: float = 10.0

# WMI方法常量命名统一
GET_CPU_TEMP: str = "getCpuTemp"
GET_GPU_TEMP1: str = "getGpuTemp1"
GET_GPU_TEMP2: str = "getGpuTemp2"
GET_RPM1: str = "getRpm1"
GET_RPM2: str = "getRpm2"
GET_CHARGE_POLICY: str = "GetChargePolicy"
GET_CHARGE_STOP: str = "GetChargeStop"
SET_CUSTOM_FAN_STATUS: str = "SetFixedFanStatus"
SET_AUTO_FAN_STATUS: str = "SetAutoFanStatus"
SET_CUSTOM_FAN_SPEED: str = "SetFixedFanSpeed"
SET_GPU_FAN_DUTY: str = "SetGPUFanDuty"
SET_CHARGE_POLICY: str = "SetChargePolicy"
SET_CHARGE_STOP: str = "SetChargeStop"
SET_SUPER_QUIET: str = "SetSuperQuiet"
SET_STEP_FAN_STATUS: str = "SetStepFanStatus"
GET_FIXED_FAN_STATUS: str = "GetFixedFanStatus"
GET_AUTO_FAN_STATUS: str = "GetAutoFanStatus"
GET_STEP_FAN_STATUS: str = "GetStepFanStatus"
GET_FIXED_FAN_SPEED: str = "GetFixedFanSpeed"
GET_CPU_FAN_DUTY: str = "GetCPUFanDuty"
GET_GPU_FAN_DUTY: str = "GetGPUFanDuty"
GET_BATTERY_HEALTH: str = "GetBatteryHealth"
GET_BATT_CYC: str = "getBattCyc"
GET_BATT_CYC1: str = "getBattCyc1"

# --- 内部信号 ---
# 使用内部信号代替字符串，更健壮
class WMIInternalSignal:
    STOP = object()
    POLL_CORE_SENSORS = object()
    UPDATE_POLLING_INTERVAL = object()
    APPLY_FAN_CONTROL = object()
    APPLY_BIOS_CONTROL = object()
    APPLY_CHARGE_POLICY = object()

TEMP_READ_ERROR_VALUE: float = -1.0
RPM_READ_ERROR_VALUE: int = -1
CHARGE_POLICY_READ_ERROR_VALUE: int = -1
CHARGE_THRESHOLD_READ_ERROR_VALUE: int = -1
# How often the charge watch re-reads the EC's applied charge policy/threshold
# and rewrites it when firmware drifted (sleep, EC reset, battery pull).
CHARGE_WATCH_INTERVAL_MS: int = 60_000

# --- NVML GPU power feed-forward (per-profile toggle: gpu_power_feedforward) ---
# Curve-temperature offset = k * max(0, power - idle), clamped. See
# core/gpu_power.py for the calibration reasoning.
NVML_FF_IDLE_W: float = 30.0
NVML_FF_K_C_PER_W: float = 0.4
NVML_FF_MAX_BOOST_C: float = 10.0
# NVML power readings flap tick-to-tick at moderate load; an unfiltered
# offset pumps the fans through the steep GPU curve segments. EMA over the
# worker's readings (~0.7 Hz) smooths the offset without delaying spin-up.
NVML_FF_EMA_ALPHA: float = 0.34
# After the idle-skip resumes (GPU crossed NVML_FF_RESUME_TEMP_C), the full
# 10 degC offset would land in one write - a step from the 25 % floor duty to
# mid curve. Cap the offset for this many cycles while it eases in.
NVML_FF_RESUME_SOFT_CYCLES: int = 4
NVML_FF_RESUME_SOFT_CAP_C: float = 3.0
INIT_APPLIED_PERCENTAGE: int = -1

# ==============================================================================
# 控制逻辑参数
# ==============================================================================
MIN_TEMP_C: int = 0
MAX_TEMP_C: int = 100
MIN_FAN_PERCENT: int = 0
MAX_FAN_PERCENT: int = 100
MIN_CHARGE_PERCENT: int = 60
MAX_CHARGE_PERCENT: int = 100
MIN_CURVE_POINTS: int = 2
MIN_POINTS_FOR_INTERPOLATION: int = 2

# ==============================================================================
# 任务计划程序 / 单例实例
# ==============================================================================
TASK_SCHEDULER_NAME: str = f"{APP_INTERNAL_NAME} Startup Task"
# 旧版本（FanBatteryControl）注册的任务名，首次运行新版本时必须删除
LEGACY_TASK_SCHEDULER_NAME: str = "FanBatteryControl Startup Task"
WATCHDOG_TASK_NAME: str = f"{APP_INTERNAL_NAME} BIOS Revert Watchdog"
# 旧版/迁移前的看门狗任务名，必须删除，否则它会读取旧安装目录的状态。
LEGACY_WATCHDOG_TASK_NAMES: tuple = (
    "GFBC BIOS Revert Watchdog",
    "FanBatteryControl BIOS Revert Watchdog",
)
WATCHDOG_INTERVAL_MINUTES: int = 1
# 控制线程应至少每 1.5 秒写一次心跳；45 秒未更新视为挂死。
WATCHDOG_HEARTBEAT_STALE_S: float = 45.0
LAST_MODE_FILENAME: str = "last_mode.state"
HEARTBEAT_FILENAME: str = "heartbeat.state"
FAN_STALL_CYCLES: int = 4
# Sustained-high-temperature balloon: fires once after this many consecutive
# cycles at/above HOT_ALERT_TEMP_C, re-arms below HOT_ALERT_RESET_C.
HOT_ALERT_TEMP_C: float = 95.0
HOT_ALERT_RESET_C: float = 90.0
HOT_ALERT_SUSTAINED_CYCLES: int = 8
# NVML idle-skip: once a reading lands at/below NVML_IDLE_SKIP_W, stop polling
# until the EC GPU temp reaches NVML_FF_RESUME_TEMP_C (real load) or the GPU
# sleeps and wakes again. Measured on this machine: NVML polling pins an idle
# RTX 3080 at P0 at ~31-34 W, so the skip threshold must sit above the
# pinned-idle draw (40 W) while the feed-forward offset floor stays at 30 W.
NVML_IDLE_SKIP_W: float = 40.0
NVML_FF_RESUME_TEMP_C: float = 65.0
# Control-path temperature EMA (core/app_services): EC temps are integers and
# curve knees rise 3-5 %/degC, so raw temps wobble the target every tick.
# alpha 0.5 -> one-tick lag at the 1.5 s control cadence; raw temps still feed
# the UI, hot-alert, and fault detection.
TEMP_SMOOTH_ALPHA: float = 0.5
# --- Acoustic shaping (core/noise_shaping.py; per-profile toggles
# anti_beat_detune / fan_load_assist, split auto mode only) ---
# Sweep 2026-11-18 (three recordings): identical duties park the two fans
# 35-90 Hz apart up to raw 62 - the roughness band, the silent-mode whistle
# - and the hardware self-separates to ~250 Hz from raw 64 up. raw 69 (30 %)
# is the only park that is beat-free AND stable across runs (28-29 % wander).
ANTI_BEAT_DETUNE_PERCENT: int = 4
# Detune only guards the beat zone (any spin request 1-27 % parks at raw
# 57-62); higher parks are already self-separated. Must exceed
# TARGET_HYSTERESIS_PERCENT or next_target swallows the offset.
ANTI_BEAT_MAX_DUTY_PERCENT: int = 27
# Load assist: lift the already-spinning cooler channel toward the hot one
# (shared 5-heatpipe heatsink: GPU-side airflow cools the CPU fin section).
ASSIST_TRIGGER_SPREAD_PERCENT: int = 10
ASSIST_MARGIN_PERCENT: int = 10
ASSIST_MAX_RISE_PERCENT: int = 8
ASSIST_MAX_TARGET_PERCENT: int = 40
# While the dGPU is latched asleep, re-probe the EC GPU sensors every Nth
# sweep instead of parking for 19 (~12-20 s blind after a real wake). EC
# register reads never touch NVML, so D3cold is unaffected.
GPU_SLEEP_PROBE_EVERY_N_SWEEPS: int = 3
STARTUP_ARG_MINIMIZED: str = "--minimized"
_APP_GUID: str = "{17e0cc04-cddb-4b9b-adcc-5faa4872e054}"
MUTEX_NAME: str = f"Global\\{APP_INTERNAL_NAME}_Mutex_{_APP_GUID}"
SHARED_MEM_NAME: str = f"Global\\{APP_INTERNAL_NAME}_SharedMem_{_APP_GUID}"
SHARED_MEM_SIZE: int = 4096 # 命令载荷是 JSON（含 profile/策略），31 字节的旧尺寸会静默丢弃它
SHARED_MEM_HWND_OFFSET: int = 0
SHARED_MEM_HWND_SIZE: int = 32
SHARED_MEM_COMMAND_OFFSET: int = 32
SHARED_MEM_COMMAND_SIZE: int = 1
SHARED_MEM_PAYLOAD_OFFSET: int = 33
SHARED_MEM_PAYLOAD_SIZE: int = SHARED_MEM_SIZE - SHARED_MEM_PAYLOAD_OFFSET
COMMAND_NONE: int = 0
COMMAND_QUIT: int = 1
COMMAND_RELOAD_AND_SHOW: int = 2
COMMAND_RELOAD_ONLY: int = 3
COMMAND_CLI_ACTION: int = 4

# ==============================================================================
# 杂项
# ==============================================================================
TOOLTIP_DELAY_MS: int = 500
KNOWN_LANGUAGES: Dict[str, str] = {"en": "English", "zh": "中文"}