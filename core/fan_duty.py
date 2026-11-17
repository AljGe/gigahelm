# -*- coding: utf-8 -*-
"""Map a fan percent to the AORUS 15P YD raw duty. Sub-57 raw values stall the fans."""

import math

FAN_RAW_MAX = 227
FAN_PERCENT_MIN_SPIN = 25


def percent_to_raw(percent: int) -> int:
    p = 0 if percent < 0 else 100 if percent > 100 else int(percent)
    if p == 0:
        return 0
    if p < FAN_PERCENT_MIN_SPIN:
        p = FAN_PERCENT_MIN_SPIN
    raw = math.ceil(p / 100.0 * FAN_RAW_MAX)
    return FAN_RAW_MAX if raw > FAN_RAW_MAX else raw


def effective_percent(percent: int) -> int:
    """Percent as the EC actually drives it.

    Every request in 1-24 % is written as the 25 % minimum-spin duty
    (percent_to_raw), so UI percentages in that range are fiction; display
    the effective duty instead.
    """
    return int(round(percent_to_raw(percent) / FAN_RAW_MAX * 100.0))
