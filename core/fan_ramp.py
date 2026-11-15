# -*- coding: utf-8 -*-
"""One fan-percent step: instant catch-up, a 40% kick off zero, and a slow spin-down."""


def next_fan_percent(applied: int, target: int, kick_armed: bool, spinning_down: bool) -> tuple[int, bool, bool]:
    target = 0 if target < 0 else 100 if target > 100 else int(target)
    if applied < 0:
        applied = 0
    if target > applied:
        if kick_armed and applied == 0 and 0 < target < 40:
            return (40, False, False)
        return (target, False, False)
    if target == applied:
        return (applied, False, False)
    if not spinning_down and (applied - target) < 8:
        return (applied, False, False)
    step = max(target, applied - 5)
    return (step, False, step != target)
