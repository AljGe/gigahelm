# -*- coding: utf-8 -*-
"""numpy-free spectral analysis of whistle-sweep recordings.

Ranks the floor-sweep park duties objectively from the laptop's own mic:
per step we measure, inside the blade-pass band (2.5-5.5 kHz at the park
RPMs),

- tonality_db  prominence of the strongest tone over the band floor. The
  primary ranking metric: amplitude-scale-invariant, so mic gain and any
  Windows AGC drift between steps do not bias it. A steady whistle scores
  high; soft air noise scores low.
- band_db      whistle-band energy (relative, uncalibrated, AGC-affected).
- split_hz     gap between the two strongest tones - the two fans' blade-pass
  lines. A small (<~150 Hz) split with high tonality is the roughness beat;
  a resolved large split or a single merged line is benign.
- broadband_db overall whoosh floor (sanity).

Pure stdlib (math, wave, array, statistics): the app venv carries no numpy
by rule, and the bench harness imports nothing from the package tree.
Median-aggregated across FFT blocks so process-spawn clicks (nvidia-smi)
and door bumps cannot dominate a step.
"""
from __future__ import annotations

import array
import cmath
import math
import statistics
import wave
from typing import List, Optional, Sequence, Tuple

#: Blade-pass analysis band at park RPMs (3.5-4.5 kHz nominal, 83 blades).
WHISTLE_BAND_HZ = (2500.0, 5500.0)
#: Half-width (bins) masked around a found peak when measuring its floor.
PEAK_MASK_BINS = 8
#: Wider floor mask for tonality: a near-merged twin line (the beat park's
#: 35-90 Hz split = 9-23 bins) must NOT survive into its own floor, or
#: prominence collapses exactly when beating is worst (measured 2026-11-18:
#: 39 Hz twin scored 9.5 dB while single tones scored ~20). ~47 Hz at
#: 16 kHz / 4096-pt FFT.
TONALITY_FLOOR_MASK_BINS = 12
#: Minimum prominence (dB) for a line to count as a second distinct tone.
TWIN_PROMINENCE_DB = 6.0
#: Twin tones farther apart than this are resolved (not roughness).
TWIN_MAX_SPLIT_HZ = 400.0
#: Twin tones this close (or closer) beat inside the roughness band
#: (~20-120 Hz); reported as beat_hz and penalized in ranking.
BEAT_MAX_HZ = 120.0

FFT_BLOCK = 4096


def read_wav_mono(path: str) -> Tuple[List[float], int]:
    """Read a 16-bit PCM mono WAV into normalized floats + sample rate."""
    with wave.open(path, "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    if channels != 1 or width != 2:
        raise ValueError(f"expected 16-bit mono WAV: ch={channels} width={width}")
    samples = array.array("h")
    samples.frombytes(raw)
    return [v / 32768.0 for v in samples], rate


def _hann(n: int) -> List[float]:
    return [0.5 * (1.0 - math.cos(2.0 * math.pi * i / n)) for i in range(n)]


def _fft_inplace(a: List[complex]) -> None:
    """Iterative radix-2 Cooley-Tukey; len(a) must be a power of two."""
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        half = length >> 1
        step = cmath.exp(-2j * cmath.pi / length)
        twiddles = [step ** k for k in range(half)]
        for start in range(0, n, length):
            for k in range(half):
                i = start + k
                jx = i + half
                t = a[jx] * twiddles[k]
                a[jx] = a[i] - t
                a[i] = a[i] + t
        length <<= 1


def psd_blocks(samples: Sequence[float], rate: int,
               block: int = FFT_BLOCK) -> List[List[float]]:
    """Windowed power spectra of consecutive non-overlapping blocks.

    Only bins 0..block//2 are returned. Trailing partial blocks are dropped;
    at least one full block is required.
    """
    if block & (block - 1):
        raise ValueError("FFT block must be a power of two")
    if len(samples) < block:
        raise ValueError(f"need >= {block} samples, got {len(samples)}")
    window = _hann(block)
    scale = 1.0 / (block * 0.5) ** 2  # coherent-gain normalization (relative)
    out: List[List[float]] = []
    for start in range(0, len(samples) - block + 1, block):
        buf = [complex(s * w) for s, w in zip(samples[start:start + block], window)]
        _fft_inplace(buf)
        half = block // 2
        out.append([scale * (buf[k].real ** 2 + buf[k].imag ** 2)
                    for k in range(half + 1)])
    return out


def median_psd(psds: List[List[float]]) -> List[float]:
    return [statistics.median(col) for col in zip(*psds)]


def _band_bins(rate: int, block: int, lo: float, hi: float) -> Tuple[int, int]:
    bin_hz = rate / block
    k_lo = max(1, math.ceil(lo / bin_hz))
    k_hi = min(block // 2 - 1, math.floor(hi / bin_hz))
    if k_hi <= k_lo:
        raise ValueError("band narrower than one bin")
    return k_lo, k_hi


def band_level_db(psd: Sequence[float], rate: int, block: int,
                  lo: float, hi: float) -> float:
    k_lo, k_hi = _band_bins(rate, block, lo, hi)
    power = sum(psd[k] for k in range(k_lo, k_hi + 1))
    return 10.0 * math.log10(max(power, 1e-30))


def tonal_prominence_db(psd: Sequence[float], rate: int, block: int,
                        lo: float, hi: float,
                        floor_mask: int = TONALITY_FLOOR_MASK_BINS,
                        ) -> Tuple[float, float]:
    """(prominence_dB, peak_Hz) of the strongest band tone vs its floor.

    The floor is the median over the band excluding a +-``floor_mask``
    neighborhood of the peak, so neither the tone's own skirt nor a
    near-merged twin line can lift the reference. Amplitude-invariant:
    scales out mic gain and AGC.
    """
    k_lo, k_hi = _band_bins(rate, block, lo, hi)
    peak = max(range(k_lo, k_hi + 1), key=lambda k: psd[k])
    floor_bins = [k for k in range(k_lo, k_hi + 1)
                  if abs(k - peak) > floor_mask]
    floor = statistics.median([psd[k] for k in floor_bins]) if floor_bins \
        else statistics.median(psd[k_lo:k_hi + 1])
    prominence = 10.0 * math.log10(max(psd[peak], 1e-30)
                                   / max(floor, 1e-30))
    bin_hz = rate / block
    return prominence, peak * bin_hz


def twin_peak_split_hz(psd: Sequence[float], rate: int, block: int,
                       lo: float, hi: float,
                       prominence_db: float = TWIN_PROMINENCE_DB,
                       max_split_hz: float = TWIN_MAX_SPLIT_HZ,
                       ) -> Optional[float]:
    """Gap between the two strongest distinct band tones, else None.

    The fans' two blade-pass lines: a small gap at high tonality is the
    roughness beat; a resolved gap beyond max_split_hz (detuned channels)
    or no second line is benign.
    """
    k_lo, k_hi = _band_bins(rate, block, lo, hi)
    peak1 = max(range(k_lo, k_hi + 1), key=lambda k: psd[k])
    floor_bins = [k for k in range(k_lo, k_hi + 1)
                  if abs(k - peak1) > PEAK_MASK_BINS]
    floor = statistics.median([psd[k] for k in floor_bins]) if floor_bins \
        else statistics.median(psd[k_lo:k_hi + 1])
    floor = max(floor, 1e-30)
    masked = [k for k in range(k_lo, k_hi + 1)
              if abs(k - peak1) > PEAK_MASK_BINS]
    if not masked:
        return None
    peak2 = max(masked, key=lambda k: psd[k])
    if 10.0 * math.log10(psd[peak2] / floor) < prominence_db:
        return None
    split_hz = abs(peak2 - peak1) * (rate / block)
    return split_hz if split_hz <= max_split_hz else None


def analyze_recording(path: str,
                      band: Tuple[float, float] = WHISTLE_BAND_HZ,
                      skip_s: float = 3.0,
                      window_s: float = 8.0,
                      block: int = FFT_BLOCK,
                      ) -> dict:
    """Metrics for one step's WAV: median-aggregated, transient-rejecting."""
    samples, rate = read_wav_mono(path)
    start = min(int(skip_s * rate), max(0, len(samples) - block))
    end = min(len(samples), start + int(window_s * rate))
    if end - start < block:
        raise ValueError(f"{path}: too much audio below one FFT block")
    psds = psd_blocks(samples[start:end], rate, block)
    med = median_psd(psds)
    prominence, peak_hz = tonal_prominence_db(med, rate, block, *band)
    split = twin_peak_split_hz(med, rate, block, *band)
    return {
        "band_db": round(band_level_db(med, rate, block, *band), 2),
        "tonality_db": round(prominence, 2),
        "split_hz": split,
        "beat_hz": split if (split is not None
                             and split <= BEAT_MAX_HZ) else None,
        "peak_hz": round(peak_hz, 1),
        "broadband_db": round(band_level_db(med, rate, block,
                                            300.0, rate / 2.0 - 500.0), 2),
        "blocks": len(psds),
    }


def rank_steps(steps: dict) -> List[Tuple[str, dict]]:
    """Quietest-first ordering: roughness beat first (beating at 35-120 Hz
    dominates annoyance regardless of level), then tonality (shape-based,
    AGC-proof), then band energy. ``steps`` maps a label (e.g. '25') to
    metrics."""
    return sorted(steps.items(),
                  key=lambda kv: (kv[1].get("beat_hz") is not None,
                                  kv[1]["tonality_db"], kv[1]["band_db"]))
