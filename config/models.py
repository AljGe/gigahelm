# -*- coding: utf-8 -*-
"""Per-model EC constants.

Gigabyte's EC WMI interface is undocumented and the meaning of the raw
duty range differs between models. The values below were reverse-
engineered per machine. Wrong values can stall fans (too-low raw floor)
or max them, so unknown models get the default preset plus a loud
warning - never a guess.

Check `main.exe --diagnose` to see which preset your machine resolved to.
"""
from typing import Dict

ModelPreset = Dict[str, int]

# Raw EC duty range and the minimum percentage that reliably keeps the
# fans spinning (below it they stall). Verified on the listed machines.
MODEL_PRESETS: Dict[str, ModelPreset] = {
    "AORUS 15P YD": {"fan_raw_max": 227, "fan_percent_min_spin": 25},
    "AORUS 15P XD": {"fan_raw_max": 227, "fan_percent_min_spin": 25},  # upstream's reference machine
}

DEFAULT_MODEL: str = "AORUS 15P YD"


def get_preset(model: str) -> ModelPreset:
    """Return the preset for *model*, falling back to the default preset."""
    return MODEL_PRESETS.get(model, MODEL_PRESETS[DEFAULT_MODEL])


def resolved_model_name(model: str) -> str:
    """'AORUS 15P YD' or the default-model note for unknown machines."""
    return model if model in MODEL_PRESETS else f"{DEFAULT_MODEL} (default; '{model}' not in presets)"


def detect_model() -> str:
    """Machine model string from Win32_ComputerSystem; 'unknown' off-Windows or on failure."""
    if os_name_check():
        try:
            import wmi
            cs = wmi.WMI().Win32_ComputerSystem()
            if cs:
                return str(cs[0].Model or "unknown")
        except Exception:
            pass
    return "unknown"


def os_name_check() -> bool:
    import os
    return os.name == 'nt'
