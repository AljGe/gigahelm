# -*- coding: utf-8 -*-
"""Pure fan target selection: sensor faults, custom speed, and thermal guards.

Kept free of Qt and hardware so every rule is unit-testable.
"""

from typing import List, Optional, Tuple

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

#: Engaged guard floors release only this many degC below their engage
#: threshold.  EC temperatures are integers and the floors step by 20-40
#: points while upward moves apply immediately, so engage/release at the same
#: temperature lets ±1 degC sensor noise at 80/85/90 °C pump the fans.
GUARD_RELEASE_C = 3.0

FAULT_TARGET_BOTH = 80
FAULT_TARGET_ONE = 40


def guard_floor(temp: float, engaged_floor: int = 0,
                thresholds: List[Tuple[float, int]] = THERMAL_GUARDS,
                release_c: float = GUARD_RELEASE_C) -> int:
    """This tick's guard floor with engage/release hysteresis.

    Engages at a threshold; an already-engaged floor holds until the
    temperature drops ``release_c`` below that floor's engage threshold.
    ``engaged_floor`` is the floor returned for the previous tick (0 = none).
    Unreadable temperatures release immediately - fault handling owns them.
    """
    floor = 0
    for threshold, minimum in thresholds:
        if temp >= threshold:
            floor = max(floor, minimum)
    if engaged_floor:
        for threshold, minimum in thresholds:
            if minimum == engaged_floor and temp >= threshold - release_c:
                floor = max(floor, engaged_floor)
                break
    return floor


def select_fan_target(
    mode: str,
    custom_speed: int,
    base_target: int,
    cpu_temp: float,
    gpu_temp: float,
    gpu_asleep: bool = False,
    cpu_floor: Optional[int] = None,
    gpu_floor: Optional[int] = None,
) -> int:
    """Return the duty the EC should receive for this control tick.

    ``cpu_floor``/``gpu_floor`` are this tick's guard floors from
    ``guard_floor`` (pass them when the caller tracks engagement).  ``None``
    computes the stateless engage-only floor from the temperature, matching
    the pre-hysteresis behavior.
    """
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
        floor = cpu_floor if cpu_floor is not None else guard_floor(cpu_temp)
        target = max(target, floor)
    if not gpu_bad:
        floor = gpu_floor if gpu_floor is not None else guard_floor(gpu_temp)
        target = max(target, floor)

    return int(round(clip(target, MIN_FAN_PERCENT, MAX_FAN_PERCENT)))


def select_per_fan_targets(
    mode: str,
    custom_speed: int,
    cpu_base: int,
    gpu_base: int,
    cpu_temp: float,
    gpu_temp: float,
    gpu_asleep: bool = False,
    cpu_floor: Optional[int] = None,
    gpu_floor: Optional[int] = None,
) -> Tuple[int, int]:
    """Return ``(cpu_channel_target, gpu_channel_target)`` for this tick.

    Each EC channel follows its own die: CPU fan from ``cpu_base`` (CPU curve),
    GPU fan from ``gpu_base`` (GPU curve).  Channels are NOT coupled through a
    shared max - that is the mirrored pattern this function replaces.
    Fault/floor overlays apply per channel:

    - CPU channel: unreadable CPU temp forces ``FAULT_TARGET_BOTH``.
    - GPU channel: a sleeping dGPU needs no airflow (0); an unreadable GPU
      temp (awake) floors the channel at ``FAULT_TARGET_ONE``; otherwise the
      guard floor lifts the channel like ``select_fan_target`` does.
    """
    base = custom_speed if mode == FAN_MODE_CUSTOM else None

    if cpu_temp == TEMP_READ_ERROR_VALUE:
        cpu_target: int = FAULT_TARGET_BOTH
    else:
        cpu_target = base if base is not None else cpu_base
        floor = cpu_floor if cpu_floor is not None else guard_floor(cpu_temp)
        cpu_target = max(cpu_target, floor)

    if gpu_asleep:
        gpu_target = 0
    elif gpu_temp == TEMP_READ_ERROR_VALUE:
        gpu_target = max(base if base is not None else gpu_base, FAULT_TARGET_ONE)
    else:
        gpu_target = base if base is not None else gpu_base
        floor = gpu_floor if gpu_floor is not None else guard_floor(gpu_temp)
        gpu_target = max(gpu_target, floor)

    cpu_target = int(round(clip(cpu_target, MIN_FAN_PERCENT, MAX_FAN_PERCENT)))
    gpu_target = int(round(clip(gpu_target, MIN_FAN_PERCENT, MAX_FAN_PERCENT)))
    return cpu_target, gpu_target
