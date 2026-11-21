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

# (temperature °C, minimum duty %) anchors - a curve may ask for less,
# safety wins.  The floor is continuous between anchors (see guard_floor);
# the UI already draws this polyline, the controller now applies it.
THERMAL_GUARDS: List[Tuple[float, int]] = [
    (80.0, 40),
    (85.0, 60),
    (90.0, 80),
]

FAULT_TARGET_BOTH = 80
FAULT_TARGET_ONE = 40


def guard_floor(temp: Optional[float],
                thresholds: List[Tuple[float, int]] = THERMAL_GUARDS) -> int:
    """Continuous thermal-guard floor in percent (0 below the first anchor).

    Linear between the (temp, duty) anchors, flat at the top anchor.  The
    anchors are the old stepwise tiers, and interpolation is never below the
    old lower tier between them, so cooling is never reduced - only the step
    discontinuities are gone.

    Measured 2026-11-19 (flap_watch: 12x 30 s all-core bursts at fixed
    40 %): the stepwise floors pumped 60<->80 % at the ~89-91 degC all-core
    ceiling - 11 tier transitions in 660 s, 44 s median gap - because +-1
    degC integer sensor noise crossed the 90 degC boundary and the 3 degC
    release band let go again.  A continuous floor turns that into a +-4 %
    duty wiggle that the 2 % target hysteresis and the up-glide absorb.
    """
    if not thresholds or temp is None:
        return 0
    if temp < thresholds[0][0]:
        return 0
    for (t0, d0), (t1, d1) in zip(thresholds, thresholds[1:]):
        if temp < t1:
            return int(round(d0 + (temp - t0) * (d1 - d0) / (t1 - t0)))
    return int(thresholds[-1][1])


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
    ``guard_floor`` (pass them when the caller already computed them).
    ``None`` computes the stateless floor from the temperature.
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
    - GPU channel: a sleeping dGPU needs no airflow (0; the acoustic layer
      may still park this channel at the spin floor - see
      ``core.noise_shaping.twin_wake``); an unreadable GPU
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
