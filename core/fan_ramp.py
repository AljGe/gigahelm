# -*- coding: utf-8 -*-
"""One fan-percent step: instant catch-up (or rate-limited climb), a soft
kick off zero, and a slow spin-down."""


def next_fan_percent(applied: int, target: int, kick_armed: bool, spinning_down: bool,
                     max_up_step: int = 0) -> tuple[int, bool, bool]:
    """max_up_step > 0 caps the climb per call; 0 = instant (heat/safety bypass)."""
    target = 0 if target < 0 else 100 if target > 100 else int(target)
    if applied < 0:
        applied = 0
    if target > applied:
        if kick_armed and applied == 0 and 0 < target < 40:
            # Stall recovery: enter just above the EC spin floor, not at a loud fixed 40.
            return (max(target, 30), False, False)
        if 0 < max_up_step < target - applied:
            return (applied + max_up_step, False, False)
        return (target, False, False)
    if target == applied:
        return (applied, False, False)
    if not spinning_down and (applied - target) < 8:
        return (applied, False, False)
    step = max(target, applied - 5)
    return (step, False, step != target)
