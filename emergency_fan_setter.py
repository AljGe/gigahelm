# -*- coding: utf-8 -*-
"""
Emergency fan speed setter.

Fully standalone script whose only purpose is to be invoked when the main
application crashes, setting the fan speed to a safe value (80%).

Runs in a separate process to avoid the COM re-entrancy deadlock the main
app can hit while crashing. Uses the same WMI call style as the main
program - simpler, more robust, more concise.
"""

import os
import sys
import math
import traceback
from datetime import datetime

# Make sure the config module is importable from the project root
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.settings import (
        WMI_NAMESPACE, DEFAULT_WMI_SET_CLASS, SET_CUSTOM_FAN_STATUS, SET_SUPER_QUIET,
        SET_AUTO_FAN_STATUS, SET_STEP_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_GPU_FAN_DUTY
    )
except ImportError:
    try:
        from config.settings import (
            WMI_NAMESPACE, DEFAULT_WMI_SET_CLASS, SET_CUSTOM_FAN_STATUS, SET_SUPER_QUIET,
            SET_AUTO_FAN_STATUS, SET_STEP_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_GPU_FAN_DUTY
        )
    except ImportError:
        WMI_NAMESPACE = r"root\WMI"
        DEFAULT_WMI_SET_CLASS = "GB_WMIACPI_Set"
        SET_CUSTOM_FAN_STATUS = "SetFixedFanStatus"
        SET_AUTO_FAN_STATUS = "SetAutoFanStatus"
        SET_CUSTOM_FAN_SPEED = "SetFixedFanSpeed"
        SET_GPU_FAN_DUTY = "SetGPUFanDuty"
        SET_SUPER_QUIET = "SetSuperQuiet"
        SET_STEP_FAN_STATUS = "SetStepFanStatus"

def _inline_percent_to_raw(percent: int) -> int:
    fan_raw_max = 227
    fan_percent_min_spin = 25
    p = 0 if percent < 0 else 100 if percent > 100 else int(percent)
    if p == 0:
        return 0
    if p < fan_percent_min_spin:
        p = fan_percent_min_spin
    raw = math.ceil(p / 100.0 * fan_raw_max)
    return fan_raw_max if raw > fan_raw_max else raw

try:
    from core.fan_duty import percent_to_raw
except ImportError:
    percent_to_raw = _inline_percent_to_raw

def log_message(message: str):
    """Log a message to the emergency.log file."""
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'emergency.log')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass

def set_emergency_fan_speed() -> bool:
    """
    Standalone entry point that sets the emergency fan speed.
    Returns True only after every EC write succeeded.
    """
    log_message("Emergency fan setter script started...")

    try:
        import wmi
        import pythoncom
    except ImportError:
        log_message("Error: wmi or pythoncom library not found. Cannot set emergency speed.")
        return False

    com_initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        com_initialized = True
        
        wmi_conn = wmi.WMI(namespace=WMI_NAMESPACE)
        instances = wmi_conn.query(f"SELECT * FROM {DEFAULT_WMI_SET_CLASS}")
        if not instances:
            log_message("Error: could not obtain WMI Set instance.")
            raise RuntimeError("Could not obtain WMI Set instance.")
        wmi_set_obj = instances[0]

        # 1. Take software fan control
        log_message("Taking software fan control...")
        # Use the same high-level WMI call style as the main program: simple and reliable
        getattr(wmi_set_obj, SET_CUSTOM_FAN_STATUS)(Data=1.0)
        getattr(wmi_set_obj, SET_SUPER_QUIET)(Data=0.0)
        getattr(wmi_set_obj, SET_AUTO_FAN_STATUS)(Data=0.0)
        getattr(wmi_set_obj, SET_STEP_FAN_STATUS)(Data=0.0)
        
        # 2. Set the fan speed to 80%
        raw_speed = float(percent_to_raw(80))
        log_message(f"Setting fan speed to {raw_speed} (80%)...")
        getattr(wmi_set_obj, SET_CUSTOM_FAN_SPEED)(Data=raw_speed)
        getattr(wmi_set_obj, SET_GPU_FAN_DUTY)(Data=raw_speed)
        
        log_message("Emergency fan speed set successfully.")
        
    except Exception:
        log_message("Error: failed to set emergency fan speed.")
        log_message(traceback.format_exc())
        return False
    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
                log_message("COM library uninitialized.")
            except Exception:
                log_message("Warning: COM library uninitialization failed.")
        log_message("Emergency fan setter script finished.")
    return True

if __name__ == "__main__":
    import sys
    sys.exit(0 if set_emergency_fan_speed() else 1)