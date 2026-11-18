# -*- coding: utf-8 -*-
"""Acoustic target shaping for split fan control (auto mode only).

Both stages are pure, upward-only target adjustments applied AFTER
``select_per_fan_targets`` (so thermal guards and fault overlays always win)
and BEFORE per-channel target hysteresis. They never lower a channel, never
wake a stopped channel, and never touch custom mode (one slider = one
explicit duty for both channels).

Why this exists (AORUS 15P YD, 83-blade 12 V blowers, unified 5-heatpipe
heatsink): the dominant silent-mode annoyance is a high-pitched whistle from
the blade-pass tone - RPM/60 * 83 blades lands at ~3.5-4.5 kHz at the 25 %
floor park, right in the ear's most sensitive band. Two fans commanded to
the SAME duty spin at near-identical RPM but are not phase-locked, so their
blade-pass tones beat at the tolerance spread (tens of Hz) - the 30-300 Hz
modulation band where hearing perceives roughness. A slow wah-wah on a
4 kHz whistle reads as "whining"; two tones separated past that band read
as soft air noise.
"""

from typing import Tuple

from .fan_duty import FAN_PERCENT_MIN_SPIN
from config.settings import (
    ANTI_BEAT_DETUNE_PERCENT,
    ANTI_BEAT_MAX_DUTY_PERCENT,
    ASSIST_TRIGGER_SPREAD_PERCENT,
    ASSIST_MARGIN_PERCENT,
    ASSIST_MAX_RISE_PERCENT,
    ASSIST_MAX_TARGET_PERCENT,
)


def anti_beat_detune(cpu_target: int, gpu_target: int,
                     *,
                     detune: int = ANTI_BEAT_DETUNE_PERCENT,
                     max_duty: int = ANTI_BEAT_MAX_DUTY_PERCENT,
                     ) -> Tuple[int, int]:
    """Split identical spinning duties so the two blade-pass tones detune.

    When both channels command the same duty in the tonal band (floor park
    and light-load plateaus), the GPU channel is lifted by ``detune``.
    Detune must exceed the target hysteresis band (2 %) or next_target
    would swallow the offset and the beat would return; unequal targets and
    stopped/asleep channels (target 0) pass through untouched - the burst
    asymmetry of split control detunes those pairs on its own, and waking a
    stopped fan for acoustics trades a start surge for nothing.
    """
    if (cpu_target == gpu_target
            and FAN_PERCENT_MIN_SPIN <= cpu_target <= max_duty):
        return cpu_target, gpu_target + detune
    return cpu_target, gpu_target


def fan_load_assist(cpu_target: int, gpu_target: int,
                    *,
                    trigger: int = ASSIST_TRIGGER_SPREAD_PERCENT,
                    margin: int = ASSIST_MARGIN_PERCENT,
                    max_rise: int = ASSIST_MAX_RISE_PERCENT,
                    max_target: int = ASSIST_MAX_TARGET_PERCENT,
                    ) -> Tuple[int, int]:
    """Spread a one-sided load across both fans of the shared heatsink.

    When one channel's target exceeds the other's by ``trigger`` points and
    the cooler channel is already spinning, the cooler channel is lifted to
    ``hot - margin``, capped at ``cool + max_rise`` and ``max_target``. The
    GPU-side airflow reaches the shared fin array, so the assisted fan buys
    real headroom for the hot die - bursts then read as two moderately
    boosted fans instead of one fan sweeping alone through the whistle
    band. Upward-only: guard floors embedded in either target are never
    undercut. A stopped cooler channel (< 25 %) stays stopped - waking it
    fires the 57-raw start surge, exactly what silent mode must avoid.
    """
    if (cpu_target < FAN_PERCENT_MIN_SPIN
            or gpu_target < FAN_PERCENT_MIN_SPIN):
        return cpu_target, gpu_target
    if cpu_target >= gpu_target:
        hot, cool = cpu_target, gpu_target
    else:
        hot, cool = gpu_target, cpu_target
    if hot - cool < trigger:
        return cpu_target, gpu_target
    assisted = max(cool, min(hot - margin, cool + max_rise, max_target))
    if cpu_target >= gpu_target:
        return cpu_target, assisted
    return assisted, gpu_target
