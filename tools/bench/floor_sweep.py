# -*- coding: utf-8 -*-
"""Whistle-sweep bench: find the acoustically quietest floor park duty.

Why: silent mode parks the fans at the EC minimum-spin duty (percent 25 ->
raw 57) for hours. With 83-blade blowers the blade-pass tone sits at
~3.5-4.5 kHz there - the ear's most sensitive band - and if raw 57 happens
to sit on a chassis/duct resonance the lone-CPU-fan whistle persists even
with anti-beat detune (which only separates the two fans). This sweep
drives BOTH fans through the park band in 1 % steps while the desktop idles
so the ear can pick the quietest parking duty; the winner then replaces the
25 % floor points in the active curves (and any FAN_PERCENT_MIN_SPIN bump).

Each step is also RECORDED through the laptop mic (sounddevice, 16 kHz
mono, needs `pip install sounddevice`; disable with --no-record) and
spectrally scored by acoustic_analysis.py: tonal prominence in the
2.5-5.5 kHz blade-pass band, band energy, and the two-fan beat split.
Tonality is amplitude-scale-invariant (mic gain/AGC cannot bias it), so it
ranks the steps; use your ears to confirm the winner.

The app stays alive the whole time (custom mode = its control loop keeps
writing, heartbeat stays fresh, the thermal-guard thread and the external
BIOS-revert watchdog stay armed). Each step is a config write + --minimized
reload, the established appctl pattern.

Run ELEVATED from the user's terminal, desktop quiet:
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\floor_sweep.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appctl
from ec_probe import ECProbe

PROFILE = "Whistle Sweep"
STEPS = tuple(range(25, 34))          # 25 % .. 33 % -> raw 57 .. 75
DWELL_S = 45.0                        # listen window per step
SETTLE_S = 6.0                        # reload + spool-up before sampling
SAMPLE_S = 2.0
RECORD_S = 30.0                       # audio per step (record mode)
CAPTURE_RATE = 16000
FIRST_SETTLE_S = 20.0                 # spool-down from the pre-sweep profile
ABORT_CPU_C = 75.0                    # guard thread owns anything hotter
RESULTS = os.path.join(HERE, "results")
ROWS_PATH = os.path.join(RESULTS, "floor-sweep.jsonl")


def set_custom_percent(percent: int) -> None:
    cfg = appctl.load_cfg()
    profile = appctl.ensure_profile(cfg, PROFILE, {})
    appctl.edit_profile(cfg, PROFILE,
                        fan_mode="custom", custom_fan_speed=int(percent))
    appctl.save_cfg_atomic(cfg)
    appctl.apply_profile(PROFILE, "custom")


def restore(previous: str, prev_mode: str) -> None:
    """Drop the bench profile from the config, re-activate the user's own."""
    cfg = appctl.load_cfg()
    cfg.get("profiles", {}).pop(PROFILE, None)
    order = cfg.get("profile_order", [])
    if PROFILE in order:
        order.remove(PROFILE)
    appctl.save_cfg_atomic(cfg)
    appctl.apply_profile(previous, prev_mode)
    print(f"restored profile: {previous}")


def setup_recorder():
    """Return a sounddevice module handle, or None when unavailable."""
    try:
        import sounddevice as sd  # optional bench dependency (no numpy use)
    except ImportError:
        return None
    try:
        if sd.query_devices(kind="input") is None:
            return None
    except Exception:
        return None
    return sd


def record_step(sd, percent: int, seconds: float) -> str:
    """Capture one step's audio to results/floor-sweep-NNpct.wav."""
    path = os.path.join(RESULTS, f"floor-sweep-{percent:02d}pct.wav")
    overflowed = False
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CAPTURE_RATE)
        stream = sd.RawInputStream(samplerate=CAPTURE_RATE, channels=1,
                                   dtype="int16", blocksize=CAPTURE_RATE)
        stream.start()
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                data, of = stream.read(CAPTURE_RATE)
                overflowed |= bool(of)
                wav.writeframes(bytes(data))
        finally:
            stream.stop()
            stream.close()
    if overflowed:
        print("      (capture overflowed - some samples dropped)",
              file=sys.stderr)
    return path


def analyze_recordings(recorded: dict, band_desc: str = "2.5-5.5 kHz") -> None:
    """Score every recorded step, print the quietest-first ranking."""
    import acoustic_analysis as aa

    metrics = {}
    for percent, path in sorted(recorded.items()):
        try:
            metrics[str(percent)] = aa.analyze_recording(path)
            m = metrics[str(percent)]
            split = f"{m['split_hz']:.0f} Hz" if m["split_hz"] is not None else "-"
            print(f"  {percent:>2} %: tonality {m['tonality_db']:>6.1f} dB  "
                  f"band {m['band_db']:>7.2f} dB  split {split:>8}  "
                  f"peak {m['peak_hz']:.0f} Hz  ({m['blocks']} blocks)",
                  flush=True)
        except (ValueError, wave.Error) as exc:
            print(f"  {percent:>2} %: analysis failed ({exc})", file=sys.stderr)
    if not metrics:
        return
    ranking = aa.rank_steps(metrics)
    print(f"\nquietest first (by tonality in {band_desc}):")
    print("  " + "  >  ".join(f"{label} %" for label, _ in ranking))
    winner = ranking[0][0]
    baseline = metrics.get("25")
    if baseline is not None and winner != "25":
        w = metrics[winner]
        print(f"sweep suggests parking at {winner} % instead of 25 % "
              f"(tonality {w['tonality_db']:.1f} vs "
              f"{baseline['tonality_db']:.1f} dB) - confirm by ear")
    with open(os.path.join(RESULTS, "floor-sweep-audio-summary.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"metrics": metrics,
                   "ranking": [label for label, _ in ranking]}, fh, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-record", action="store_true",
                        help="skip mic capture and spectral ranking")
    parser.add_argument("--record-s", type=float, default=RECORD_S,
                        help=f"audio seconds per step (default {RECORD_S:.0f})")
    parser.add_argument("--steps", type=str, default="",
                        help="comma list of percents to A/B, e.g. 25,28,30 "
                             f"(default {STEPS[0]}..{STEPS[-1]})")
    args = parser.parse_args()
    if args.steps:
        try:
            steps = tuple(sorted({int(s) for s in args.steps.split(",")}))
        except ValueError:
            print(f"bad --steps {args.steps!r}: comma integers expected")
            return 2
        if not steps or not all(1 <= s <= 100 for s in steps):
            print("--steps out of range 1-100")
            return 2
    else:
        steps = STEPS

    os.makedirs(RESULTS, exist_ok=True)
    cfg = appctl.load_cfg()
    previous = cfg.get("active_profile_name", "")
    if not previous:
        print("no active profile recorded - refusing to guess a restore target")
        return 2
    prev_mode = str(cfg.get("profiles", {}).get(previous, {})
                    .get("fan_mode", "auto"))

    recorder = None
    if not args.no_record:
        recorder = setup_recorder()
        if recorder is None:
            print("sounddevice/mic unavailable - falling back to ears-only "
                  "(pip install sounddevice for spectral ranking)")

    probe = ECProbe()
    probe.connect()
    first = probe.sample_once()
    if first["cpu_t"] >= ABORT_CPU_C:
        print(f"cpu at {first['cpu_t']:.0f} degC - too hot for a floor sweep, "
              "come back to an idle desktop")
        return 2

    step_s = DWELL_S if recorder is None else SETTLE_S + args.record_s + 5.0
    recorded: dict[int, str] = {}
    try:
        set_custom_percent(steps[0])
        mode = "listen + record" if recorder else "listen"
        print(f"sweeping {steps[0]}..{steps[-1]} %, ~{step_s:.0f} s per step "
              f"({mode}); keep the desktop quiet", flush=True)
        if recorder:
            print("tonality ranks the steps; mic AGC means absolute levels "
                  "are relative only", flush=True)
        # First step must be acoustically clean: the pre-sweep profile's fan
        # duty needs spool-down time (run 2 recorded its 25 % step while the
        # fans were still at the Gaming profile's ~2200 RPM).
        time.sleep(FIRST_SETTLE_S)
        with open(ROWS_PATH, "a", encoding="utf-8") as rows:
            for percent in steps:
                raw = math.ceil(percent / 100.0 * 227)
                print(f"  >>> now playing {percent:>2} % (raw {raw:>3}) - "
                      "listen", flush=True)
                set_custom_percent(percent)
                time.sleep(SETTLE_S)
                rpm_seen: list[int] = []
                # Probe RPMs first (nvidia-smi spawns are audible clicks the
                # median analysis would have to reject), then pure audio.
                # Three samples: one decode glitch must not break the median.
                for _ in range(3):
                    row = probe.sample_once()
                    row["step_percent"] = percent
                    row["phase"] = "listen"
                    rows.write(json.dumps(row) + "\n")
                    rows.flush()
                    rpm_seen.append(row["rpm1"])
                    time.sleep(SAMPLE_S)
                if recorder is not None:
                    path = record_step(recorder, percent, args.record_s)
                    recorded[percent] = path
                median_rpm = statistics.median(rpm_seen) if rpm_seen else -1
                spread = (max(rpm_seen) - min(rpm_seen)) if len(rpm_seen) > 1 else 0
                print(f"      {percent:>2} % (raw {raw:>3}): rpm median "
                      f"{median_rpm:.0f}, jitter {spread}", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted - restoring previous profile")
    finally:
        if previous != PROFILE:
            try:
                restore(previous, prev_mode)
            except appctl.ApplyError as exc:
                print(f"FAILED to restore {previous!r}: {exc} - "
                      "re-apply it manually", file=sys.stderr)
    if recorded:
        print("\nspectral scoring of the recordings...", flush=True)
        analyze_recordings(recorded)
    else:
        print("pick the step that sounded quietest; winners usually sit 1-3 raw "
              "duty steps off the 57 floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
