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

The app stays alive the whole time (custom mode = its control loop keeps
writing, heartbeat stays fresh, the thermal-guard thread and the external
BIOS-revert watchdog stay armed). Each step is a config write + --minimized
reload, the established appctl pattern.

Run ELEVATED from the user's terminal, desktop quiet:
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\floor_sweep.py

Judge each step by ear. The JSONL log records RPM per step so tonal
instability (hunting) shows up as RPM jitter alongside what you hear.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time

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


def main() -> int:
    os.makedirs(RESULTS, exist_ok=True)
    cfg = appctl.load_cfg()
    previous = cfg.get("active_profile_name", "")
    if not previous:
        print("no active profile recorded - refusing to guess a restore target")
        return 2
    prev_mode = str(cfg.get("profiles", {}).get(previous, {})
                    .get("fan_mode", "auto"))

    probe = ECProbe()
    probe.connect()
    first = probe.sample_once()
    if first["cpu_t"] >= ABORT_CPU_C:
        print(f"cpu at {first['cpu_t']:.0f} degC - too hot for a floor sweep, "
              "come back to an idle desktop")
        return 2

    try:
        set_custom_percent(STEPS[0])
        print(f"sweeping {STEPS[0]}..{STEPS[-1]} %, {DWELL_S:.0f} s per step; "
              "listen and note the quiet steps", flush=True)
        with open(ROWS_PATH, "a", encoding="utf-8") as rows:
            for percent in STEPS:
                raw = math.ceil(percent / 100.0 * 227)
                print(f"  >>> now playing {percent:>2} % (raw {raw:>3}) - "
                      "listen", flush=True)
                set_custom_percent(percent)
                time.sleep(SETTLE_S)
                rpm_seen: list[int] = []
                deadline = time.time() + DWELL_S
                while time.time() < deadline:
                    row = probe.sample_once()
                    row["step_percent"] = percent
                    row["phase"] = "listen"
                    rows.write(json.dumps(row) + "\n")
                    rows.flush()
                    rpm_seen.append(row["rpm1"])
                    time.sleep(SAMPLE_S)
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
    print("pick the step that sounded quietest; winners usually sit 1-3 raw "
          "duty steps off the 57 floor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
