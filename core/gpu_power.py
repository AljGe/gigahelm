# -*- coding: utf-8 -*-
"""NVML GPU power feed-forward: convert the measured dGPU power draw into a
curve-temperature offset so the GPU fan curve reacts to load while heat is
still travelling to the heatsink.

Pure math, no pynvml import here - the WMI worker supplies the reading or
None (dGPU asleep / NVML absent / transient read error), and this never
boosts on unknown data.

Calibration (RTX 3080 Laptop, AORUS 15P YD): desktop idle 10-20 W (NVML read
fails in D3cold sleep), video playback ~25-30 W, gaming 60-100 W. NVML_FF_IDLE_W
sits above video playback so light tasks stay silent; NVML_FF_MAX_BOOST_C caps
the pre-spin at the same magnitude the old dT/dt boost used (10 degC).
"""
from typing import Optional


def feedforward_offset(power_w: Optional[float], idle_w: float,
                       k_c_per_w: float, max_boost_c: float) -> float:
    """Curve-temperature offset (degC) for a power reading; 0.0 when the
    reading is unknown, unparseable, or at/below the idle floor."""
    if power_w is None:
        return 0.0
    try:
        power = float(power_w)
    except (TypeError, ValueError):
        return 0.0
    excess = power - idle_w
    if excess <= 0.0:
        return 0.0
    return min(excess * k_c_per_w, max_boost_c)
