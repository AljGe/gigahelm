# -*- coding: utf-8 -*-
"""Hardware diagnostic dump for issue reports (`main.exe --diagnose`).

Writes a JSON report next to the executable containing the machine model,
the EC preset that resolved, WMI availability, every method the EC Get/Set
objects expose, and the current sensor readbacks. This is what a useful
bug report looks like on an untested Gigabyte model.
"""
import json
import os
from typing import Any, Dict

from config.models import detect_model, get_preset, resolved_model_name

REPORT_NAME = "diagnose_report.json"

_READ_METHODS = (
    "getCpuTemp", "getGpuTemp1", "getGpuTemp2", "getRpm1", "getRpm2",
    "GetChargePolicy", "GetChargeStop", "GetFixedFanStatus", "GetAutoFanStatus",
    "GetStepFanStatus", "GetFixedFanSpeed", "GetCPUFanDuty", "GetGPUFanDuty",
    "GetBatteryHealth", "getBattCyc", "getBattCyc1",
)


def _collect_wmi(report: Dict[str, Any]):
    section: Dict[str, Any] = {"available": False}
    try:
        import wmi
        import pythoncom
        section["available"] = True
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        try:
            conn = wmi.WMI(namespace="root\\WMI")
            get_objs = conn.query("SELECT * FROM GB_WMIACPI_Get")
            set_objs = conn.query("SELECT * FROM GB_WMIACPI_Set")
            if get_objs:
                getter = get_objs[0]
                names = [n for n in dir(getter) if not n.startswith("_")]
                section["get_methods"] = sorted(n for n in names if not n.isupper())
                section["readbacks"] = {}
                for name in _READ_METHODS:
                    if hasattr(getter, name):
                        try:
                            value = getattr(getter, name)()
                            section["readbacks"][name] = value[0] if isinstance(value, tuple) and value else value
                        except Exception as exc:
                            section["readbacks"][name] = f"ERROR: {exc!r}"
            else:
                section["get_methods"] = "GB_WMIACPI_Get not found"
            section["set_present"] = bool(set_objs)
            if set_objs:
                section["set_methods"] = sorted(
                    n for n in dir(set_objs[0]) if not n.startswith("_") and not n.isupper())
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        section["error"] = repr(exc)
    report["wmi"] = section


def run_diagnose(base_dir: str, is_admin_flag: bool) -> str:
    model = detect_model()
    report: Dict[str, Any] = {
        "model": model,
        "preset": resolved_model_name(model),
        "fan_preset": get_preset(model),
        "is_admin": is_admin_flag,
    }
    _collect_wmi(report)
    path = os.path.join(base_dir, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return path
