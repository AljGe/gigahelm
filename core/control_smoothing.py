# -*- coding: utf-8 -*-
"""Pure temperature-input conditioning for the auto control loop.

Three stages, all stateless functions over caller-owned deques:
1. Median-of-3 pre-filter - kills single-sample EC spikes.
2. EMA smoothing - slows target churn from bursty loads.
3. dT/dt predictive boost - acts early on fast temperature rises
   ("load approximation" without a power sensor: a fast-rising
   temperature IS the load signal).

Safety paths (>=90 C override, sensor-failure floors) always receive the
RAW temperature from app_services; they are never smoothed or boosted.
Error cycles never call into here, so histograms stay clean.
"""

from statistics import median
from typing import Deque

from config.settings import (
    CONTROL_SMOOTH_EMA_ALPHA, PREDICT_SLOPE_THRESHOLD_C_PER_S,
    PREDICT_TAU_S, PREDICT_MAX_BOOST_C, PREDICT_ARM_TEMP_C,
)


def smooth_and_predict(
    raw_hist: Deque[float],
    smooth_hist: Deque[float],
    raw: float,
    interval_s: float,
) -> float:
    """Append *raw* to *raw_hist* and return the conditioned control temperature."""
    raw_hist.append(raw)
    recent = list(raw_hist)[-3:]
    m = median(recent)
    prev = smooth_hist[-1] if smooth_hist else None
    smooth = m if prev is None else CONTROL_SMOOTH_EMA_ALPHA * m + (1.0 - CONTROL_SMOOTH_EMA_ALPHA) * prev
    smooth_hist.append(smooth)

    boost = 0.0
    if len(smooth_hist) >= 3 and interval_s > 0:
        span = (len(smooth_hist) - 1) * interval_s
        slope = (smooth_hist[-1] - smooth_hist[0]) / span
        if slope > PREDICT_SLOPE_THRESHOLD_C_PER_S and smooth < PREDICT_ARM_TEMP_C:
            boost = min(slope * PREDICT_TAU_S, PREDICT_MAX_BOOST_C)
    return smooth + boost
