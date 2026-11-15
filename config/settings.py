# -*- coding: utf-8 -*-
"""
Global constants and default settings for the fan and battery control app.
"""
from typing import List, Dict, Any

# ==============================================================================
# App info
# ==============================================================================
APP_NAME: str = "FanBatteryControl"
APP_ORGANIZATION_NAME: str = "FanBatteryControl" # Used by Qt for settings paths
APP_INTERNAL_NAME: str = "FanBatteryControl" # Used for mutex, task name, etc.

# ==============================================================================
# Default configuration values
# ==============================================================================
DEFAULT_LANGUAGE: str = "en" # Default language code (ISO 639-1)

# --- Core state constants ---
FAN_MODE_BIOS = "bios"
FAN_MODE_AUTO = "auto" # Software-controlled curve
FAN_MODE_CUSTOM = "custom" # Software-controlled custom speed
FAN_MODE_UNKNOWN = "unknown"
CHARGE_POLICY_BIOS = "bios"
CHARGE_POLICY_CUSTOM = "custom"

# --- Battery control constants ---
BATTERY_POLICY_CODES: Dict[str, int] = {"bios": 0, "custom": 4}
BATTERY_CODE_POLICIES: Dict[int, str] = {0: "bios", 1: "custom", 4: "custom"}

# --- Default profile settings ---
# Defines the structure and defaults for *each* profile
DEFAULT_CPU_FAN_TABLE: List[List[int]] = [[50, 0], [60, 15], [70, 25], [80, 40], [85, 60], [90, 80], [95, 100]]
DEFAULT_GPU_FAN_TABLE: List[List[int]] = [[40, 0], [55, 15], [65, 25], [70, 40], [75, 60], [80, 80], [85, 100]]
DEFAULT_FAN_MODE: str = FAN_MODE_AUTO
DEFAULT_CUSTOM_FAN_SPEED: int = 30 # percent
DEFAULT_CHARGE_POLICY: str = CHARGE_POLICY_BIOS
DEFAULT_CHARGE_THRESHOLD: int = 80 # percent

# --- Default control logic and timing (per profile) ---
DEFAULT_GUI_UPDATE_INTERVAL_MS: int = 1000 # How often the GUI refreshes status
DEFAULT_CONTROLLER_UPDATE_INTERVAL_MS: int = 1500 # How often fan speed is adjusted toward the target in auto mode
DEFAULT_FAN_HYSTERESIS_PERCENT: int = 5 # °C drop below the anchor before a spin-down target is adopted (rises always apply at once)
DEFAULT_MAX_ADJUSTMENT_STEP: int = 5 # Max spin-up percent per cycle when rate-limited (heat/safety targets bypass)

# --- Auto-loop temperature conditioning (global constants, not per-profile) ---
# Control input = median-of-3 -> EMA -> optional dT/dt boost; safety paths use raw temps.
CONTROL_SMOOTH_WINDOW: int = 4 # raw samples kept per sensor for the median pre-filter
CONTROL_SMOOTH_EMA_ALPHA: float = 0.4 # weight of the newest median in the EMA
PREDICT_SLOPE_SAMPLES: int = 7 # smoothed samples used for the dT/dt estimate (~9 s at 1.5 s)
PREDICT_SLOPE_THRESHOLD_C_PER_S: float = 0.7 # rise rate that arms the predictive boost
PREDICT_TAU_S: float = 8.0 # seconds of rise the boost extrapolates ahead
PREDICT_MAX_BOOST_C: float = 12.0 # cap on the extrapolated offset
PREDICT_ARM_TEMP_C: float = 85.0 # no boost above this temperature (curve is already steep)
# Spin-up rate limit bypasses: hotter or bigger targets climb instantly.
UP_RATE_BYPASS_TARGET_PERCENT: int = 60
UP_RATE_BYPASS_TEMP_C: float = 85.0

# --- Default curve plotting and UI appearance (per profile) ---
DEFAULT_MIN_DISPLAY_TEMP_C: int = 40 # Minimum temperature shown on the curve plot X axis
DEFAULT_CURVE_POINT_PICKER_RADIUS: float = 8.0 # Pick radius for points on the plot
DEFAULT_SPLINE_POINTS: int = 100 # Number of points on the interpolated smooth curve
DEFAULT_CPU_CURVE_COLOR: str = '#00AEEF'
DEFAULT_GPU_CURVE_COLOR: str = '#7AC143'
DEFAULT_POINT_COLOR_ACTIVE: str = '#FFFFFF'
DEFAULT_POINT_SIZE_ACTIVE: int = 7
DEFAULT_CPU_TEMP_INDICATOR_COLOR: str = '#FF6347' # tomato
DEFAULT_GPU_TEMP_INDICATOR_COLOR: str = '#90EE90' # light green
DEFAULT_LINE_WIDTH_ACTIVE: float = 2.0
DEFAULT_LINE_WIDTH_INACTIVE: float = 1.0
DEFAULT_ALPHA_ACTIVE: float = 1.0
DEFAULT_ALPHA_INACTIVE: float = 0.4
DEFAULT_FIGURE_BG_COLOR: str = '#33373B'
DEFAULT_AXES_BG_COLOR: str = '#2A2D30'
DEFAULT_GRID_COLOR: str = '#555555'
DEFAULT_AXES_LABEL_COLOR: str = '#E0E0E0'

# --- All default profile settings merged into one dict ---
DEFAULT_PROFILE_SETTINGS: Dict[str, Any] = {
    "cpu_fan_table": [list(p) for p in DEFAULT_CPU_FAN_TABLE],
    "gpu_fan_table": [list(p) for p in DEFAULT_GPU_FAN_TABLE],
    "fan_mode": DEFAULT_FAN_MODE,
    "custom_fan_speed": DEFAULT_CUSTOM_FAN_SPEED,
    "battery_charge_policy": DEFAULT_CHARGE_POLICY,
    "battery_charge_threshold": DEFAULT_CHARGE_THRESHOLD,
    "gui_update_interval_ms": DEFAULT_GUI_UPDATE_INTERVAL_MS,
    "controller_update_interval_ms": DEFAULT_CONTROLLER_UPDATE_INTERVAL_MS,
    "fan_hysteresis_percent": DEFAULT_FAN_HYSTERESIS_PERCENT,
    "max_adjustment_step": DEFAULT_MAX_ADJUSTMENT_STEP,
    "min_display_temp_c": DEFAULT_MIN_DISPLAY_TEMP_C,
    "curve_point_picker_radius": DEFAULT_CURVE_POINT_PICKER_RADIUS,
    "spline_points": DEFAULT_SPLINE_POINTS,
    "cpu_curve_color": DEFAULT_CPU_CURVE_COLOR,
    "gpu_curve_color": DEFAULT_GPU_CURVE_COLOR,
    "point_color_active": DEFAULT_POINT_COLOR_ACTIVE,
    "point_size_active": DEFAULT_POINT_SIZE_ACTIVE,
    "cpu_temp_indicator_color": DEFAULT_CPU_TEMP_INDICATOR_COLOR,
    "gpu_temp_indicator_color": DEFAULT_GPU_TEMP_INDICATOR_COLOR,
    "line_width_active": DEFAULT_LINE_WIDTH_ACTIVE,
    "line_width_inactive": DEFAULT_LINE_WIDTH_INACTIVE,
    "alpha_active": DEFAULT_ALPHA_ACTIVE,
    "alpha_inactive": DEFAULT_ALPHA_INACTIVE,
    "figure_bg_color": DEFAULT_FIGURE_BG_COLOR,
    "axes_bg_color": DEFAULT_AXES_BG_COLOR,
    "grid_color": DEFAULT_GRID_COLOR,
    "axes_label_color": DEFAULT_AXES_LABEL_COLOR,
}

# ==============================================================================
# WMI configuration
# ==============================================================================
WMI_NAMESPACE: str = r"root\WMI"
DEFAULT_WMI_GET_CLASS: str = "GB_WMIACPI_Get"
DEFAULT_WMI_SET_CLASS: str = "GB_WMIACPI_Set"
WMI_REQUEST_TIMEOUT_S: float = 5.0

# Unified naming for WMI method constants
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

# --- Internal signals ---
# Internal signal objects instead of strings, more robust
class WMIInternalSignal:
    STOP = object()
    POLL_CORE_SENSORS = object()
    UPDATE_POLLING_INTERVAL = object()
    APPLY_FAN_CONTROL = object()

TEMP_READ_ERROR_VALUE: float = -1.0
RPM_READ_ERROR_VALUE: int = -1
CHARGE_POLICY_READ_ERROR_VALUE: int = -1
CHARGE_THRESHOLD_READ_ERROR_VALUE: int = -1
INIT_APPLIED_PERCENTAGE: int = -1

# ==============================================================================
# Control logic parameters
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
# Task Scheduler / single instance
# ==============================================================================
TASK_SCHEDULER_NAME: str = f"{APP_INTERNAL_NAME} Startup Task"
STARTUP_ARG_MINIMIZED: str = "--minimized"
_APP_GUID: str = "{17e0cc04-cddb-4b9b-adcc-5faa4872e054}"
MUTEX_NAME: str = f"Global\\{APP_INTERNAL_NAME}_Mutex_{_APP_GUID}"
SHARED_MEM_NAME: str = f"Global\\{APP_INTERNAL_NAME}_SharedMem_{_APP_GUID}"
SHARED_MEM_SIZE: int = 64
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
# Misc
# ==============================================================================
TOOLTIP_DELAY_MS: int = 500
KNOWN_LANGUAGES: Dict[str, str] = {"en": "English", "zh": "中文"}
