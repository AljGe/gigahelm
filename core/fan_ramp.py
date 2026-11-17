# -*- coding: utf-8 -*-
"""Fan duty ramp and target hysteresis.

The ramp is intentionally asymmetric: curve-driven speed-ups glide at
UP_STEP_PERCENT per control tick (a burst reads as a ramp instead of one RPM
jump), speed-downs are capped at 5 percentage points per tick, and a
safety floor above the applied duty bypasses the up-glide entirely.
Callers pass ``up_step=None`` for the old jump-immediately behavior
(custom mode, a channel's first cycle in a fresh control session).
Every drop must converge - a
small drop may not be parked forever, otherwise the fans can sit at 25% raw
PWM while the UI claims 1-10%.

Zero-release debounce: the EC reports integer temperatures, and a curve knee
rises 3-5 %/degC, so a target hovering at the fan-off point flaps between 0
and a few percent on sensor noise.  Percent-domain hysteresis cannot absorb
that (the flap is larger than the band), and every 0 -> positive transition
writes the 25% minimum-spin duty to stalled fans - an audible start surge.
A raw target of 0 must therefore persist for ZERO_RELEASE_TICKS consecutive
ticks before a running fan is released to off.
"""

from typing import Optional, Tuple

DOWN_STEP_PERCENT = 5
UP_STEP_PERCENT = 10
TARGET_HYSTERESIS_PERCENT = 2
START_FROM_ZERO_PERCENT = 3
ZERO_RELEASE_TICKS = 3


def _clamp_percent(value: int) -> int:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return int(value)


def next_fan_percent(applied: int, target: int,
                     safety_floor: int = 0,
                     up_step: Optional[int] = None) -> int:
    """Return the next duty.

    Speed-downs step DOWN_STEP_PERCENT per tick and always converge.
    Speed-ups depend on ``up_step``: ``None`` jumps immediately, otherwise a
    curve-driven rise glides ``up_step`` points per tick.  A ``safety_floor``
    (guard/fault) above the applied duty bypasses the glide - thermal
    headroom is never queued behind noise shaping.
    """
    target = _clamp_percent(target)
    applied = _clamp_percent(applied)

    if target > applied:
        if up_step is None or safety_floor > applied:
            return target
        return min(target, applied + max(1, up_step))
    if target == applied:
        return applied

    return max(target, applied - DOWN_STEP_PERCENT)


def next_target(previous: Optional[int], raw: int,
                band: int = TARGET_HYSTERESIS_PERCENT,
                zero_streak: int = 0) -> Tuple[int, int]:
    """Bounded target hysteresis with zero-release debouncing.

    A 1-point wobble is ignored, but changes of ``band`` or more always apply.
    Starting from 0 needs a START_FROM_ZERO_PERCENT request; that prevents a
    0<->25% raw-PWM oscillation when the temperature sits exactly on a curve's
    zero point.

    Returns ``(target, zero_streak)``.  ``zero_streak`` counts consecutive
    zero raw targets; a running fan only releases to 0 once it reaches
    ZERO_RELEASE_TICKS.  Passing ``previous=None`` (fresh mode/profile/resume)
    passes the raw target through and resets the streak.
    """
    raw = _clamp_percent(raw)
    if previous is None:
        return raw, 0
    if raw == 0:
        streak = zero_streak + 1
        if previous == 0 or streak >= ZERO_RELEASE_TICKS:
            return 0, streak
        return previous, streak
    if previous == 0:
        return (raw if raw >= START_FROM_ZERO_PERCENT else 0), 0
    if abs(raw - previous) < max(1, band):
        return previous, 0
    return raw, 0
