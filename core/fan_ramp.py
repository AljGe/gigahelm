# -*- coding: utf-8 -*-
"""Fan duty ramp and target hysteresis.

The ramp is intentionally asymmetric: speed-ups are immediate, speed-downs are
capped at 5 percentage points per control tick.  Every drop must converge - a
small drop may not be parked forever, otherwise the fans can sit at 25% raw
PWM while the UI claims 1-10%.
"""

from typing import Optional

DOWN_STEP_PERCENT = 5
TARGET_HYSTERESIS_PERCENT = 2
START_FROM_ZERO_PERCENT = 3


def _clamp_percent(value: int) -> int:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return int(value)


def next_fan_percent(applied: int, target: int, kick_armed: bool, spinning_down: bool) -> tuple[int, bool, bool]:
    """Return the next duty.

    ``kick_armed`` and ``spinning_down`` remain in the signature for callers
    and tests, but the EC's minimum-spin mapping (1% -> raw 57) already starts
    the fans; a fixed 40% surge is no longer issued.
    """
    target = _clamp_percent(target)
    applied = _clamp_percent(applied)

    if target > applied:
        return (target, False, False)
    if target == applied:
        return (applied, False, False)

    step = max(target, applied - DOWN_STEP_PERCENT)
    return (step, False, step != target)


def next_target(previous: Optional[int], raw: int, band: int = TARGET_HYSTERESIS_PERCENT) -> int:
    """Bounded target hysteresis.

    A 1-point wobble is ignored, but 0% and changes of ``band`` or more always
    apply.  Starting from 0 needs a 3-point request; that prevents a 0<->25%
    raw-PWM oscillation when the temperature sits exactly on a curve's zero
    point.
    """
    raw = _clamp_percent(raw)
    if previous is None:
        return raw
    if raw == 0:
        return 0
    if previous == 0:
        return raw if raw >= START_FROM_ZERO_PERCENT else 0
    if abs(raw - previous) < max(1, band):
        return previous
    return raw
