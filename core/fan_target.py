# -*- coding: utf-8 -*-
"""Pure fan target selection: sensor faults, custom speed, and thermal guards.

Kept free of Qt and hardware so every rule is unit-testable.
"""

from typing import List, Tuple

from .interpolation import clip
from config.settings import (
    MIN_FAN_PERCENT, MAX_FAN_PERCENT, TEMP_READ_ERROR_VALUE,
    FAN_MODE_CUSTOM,
)

# (temperature °C, minimum duty %) - a curve may ask for less, safety wins.
THERMAL_GUARDS: List[Tuple[float, int]] = [
    (80.0, 40),
    (85.0, 60),
    (90.0, 80),
]

FAULT_TARGET_BOTH = 80
FAULT_TARGET_ONE = 40


def select_fan_target(
    mode: str,
    custom_speed: int,
    base_target: int,
    cpu_temp: float,
    gpu_temp: float,
    gpu_asleep: bool = False,
) -> int:
    """Return the duty the EC should receive for this control tick."""
    base = custom_speed if mode == FAN_MODE_CUSTOM else base_target
    cpu_bad = cpu_temp == TEMP_READ_ERROR_VALUE
    # A sleeping dGPU reports 0 for both sensors; that is a sleep state, not a
    # failure, and the CPU curve decides alone.
    gpu_bad = gpu_temp == TEMP_READ_ERROR_VALUE and not gpu_asleep

    if cpu_bad and (gpu_bad or gpu_asleep):
        target = FAULT_TARGET_BOTH
    elif cpu_bad or gpu_bad:
        target = max(base, FAULT_TARGET_ONE)
    else:
        target = base

    if not cpu_bad:
        for threshold, minimum in THERMAL_GUARDS:
            if cpu_temp >= threshold:
                target = max(target, minimum)
    if not gpu_bad:
        for threshold, minimum in THERMAL_GUARDS:
            if gpu_temp >= threshold:
                target = max(target, minimum)

    return int(round(clip(target, MIN_FAN_PERCENT, MAX_FAN_PERCENT)))
