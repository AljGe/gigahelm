# -*- coding: utf-8 -*-
"""Map a fan percent to the raw EC duty. Sub-min-spin raw values stall the fans.

The raw range and spin floor are model-specific (see config/models.py);
the defaults here match the AORUS 15P YD.
"""

import math

FAN_RAW_MAX = 227
FAN_PERCENT_MIN_SPIN = 25


def percent_to_raw(percent: int, fan_raw_max: int = FAN_RAW_MAX,
                   fan_percent_min_spin: int = FAN_PERCENT_MIN_SPIN) -> int:
    p = 0 if percent < 0 else 100 if percent > 100 else int(percent)
    if p == 0:
        return 0
    if p < fan_percent_min_spin:
        p = fan_percent_min_spin
    raw = math.ceil(p / 100.0 * fan_raw_max)
    return fan_raw_max if raw > fan_raw_max else raw
