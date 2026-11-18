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

from .fan_duty import FAN_PERCENT_MIN_SPIN, effective_percent
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
    """Split identical beat-zone parks so the two blade-pass tones separate.

    Measured on this machine (whistle sweep 2026-11-18, three recordings):
    identical duties park the two fans 35-90 Hz apart up to raw 62 - inside
    the roughness band, the silent-mode whistle - and the hardware
    self-separates to ~250 Hz from raw 64 up, which is why parks >= 28 %
    never detune here: a further shift would drag the pair back toward the
    roughness band.

    Equality is judged in EC-effective percent: every request of 1-25 %
    maps to the same raw 57 park, so the comparison - and the raise - must
    happen in that space or a floor-clamped park would detune to another
    raw 57 (i.e. not at all). +4 at ~1700 RPM is ~200 Hz of split, resolved
    like the self-separated parks. The offset must exceed the target
    hysteresis band (2 %) or next_target would swallow it. Stopped or
    asleep channels (effective 0) pass through untouched - waking a stopped
    fan for acoustics trades a start surge for nothing.
    """
    cpu_effective = effective_percent(cpu_target)
    if (0 < cpu_target <= max_duty
            and effective_percent(gpu_target) == cpu_effective):
        return cpu_target, cpu_effective + detune
    return cpu_target, gpu_target


def fan_load_assist(cpu_target: int, gpu_target: int,
                    *,
                    trigger: int = ASSIST_TRIGGER_SPREAD_PERCENT,
                    margin: int = ASSIST_MARGIN_PERCENT,
                    max_rise: int = ASSIST_MAX_RISE_PERCENT,
                    max_target: int = ASSIST_MAX_TARGET_PERCENT,
                    ) -> Tuple[int, int]:
    """Spread a one-sided load across both fans of the shared heatsink.

    When one channel's effective duty exceeds the other's by ``trigger``
    points and the cooler channel is already spinning, the cooler channel
    is lifted to ``hot - margin``, capped at ``cool + max_rise`` and
    ``max_target``. The GPU-side airflow reaches the shared fin array, so
    the assisted fan buys real headroom for the hot die - bursts then read
    as two moderately boosted fans instead of one fan sweeping alone
    through the whistle band. Upward-only: guard floors embedded in either
    target are never undercut. A stopped cooler channel (effective 0)
    stays stopped - waking it fires the 57-raw start surge, exactly what
    silent mode must avoid. Floor-clamped parks count as spinning
    (effective 25).
    """
    cpu_effective = effective_percent(cpu_target)
    gpu_effective = effective_percent(gpu_target)
    if (cpu_effective < FAN_PERCENT_MIN_SPIN
            or gpu_effective < FAN_PERCENT_MIN_SPIN):
        return cpu_target, gpu_target
    if cpu_effective >= gpu_effective:
        hot, cool = cpu_effective, gpu_effective
        cpu_hot = True
    else:
        hot, cool = gpu_effective, cpu_effective
        cpu_hot = False
    if hot - cool < trigger:
        return cpu_target, gpu_target
    assisted = max(cool, min(hot - margin, cool + max_rise, max_target))
    return (cpu_target, assisted) if cpu_hot else (assisted, gpu_target)
