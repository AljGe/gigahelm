# -*- coding: utf-8 -*-
"""Guard-tier flap + EC readback-sag characterization (must run elevated).

Reproduces the 82-87 degC tier-crossing regime deterministically and
quantifies the two flaws the 2026-11-18 audit found in thermal_guard.log:

1. Guard-tier flap. The control loop keeps the custom Ladder-40 duty (raw
   91) alive via its flag-reassert keepalive while the EC-level guard
   thread overlays 40/60/80 % floors at 80/85/90 degC (release 3 degC
   below). Burst-modulated all-core load (ON 30 s / OFF 25 s) walks the
   CPU across every tier boundary each cycle, so the guard re-forces and
   releases repeatedly - the same pumping the desktop showed all day
   (337x floor-40, 108x floor-60 on 2026-11-18). Output: tier-transition
   timeline, force cadence, and the RPM swing each transition causes.
2. Readback sag. TODO.md documents GetFixedFanSpeed reading below the
   commanded duty under load (148 vs 227). During the ON phases the
   commanded raw is a known constant (91), so the fixed_raw distribution
   measures the sag exactly; OFF phases give the recovery baseline.

The app keeps controlling throughout (custom mode + its own guard thread
+ the external BIOS watchdog), so this is observation, not override.

Run ELEVATED from the repo root:
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\flap_watch.py
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appctl
import bench
from ec_probe import ECProbe
from loadgen import Burners, LOAD_N

DUTY = 40                       # custom duty percent -> commanded raw 91
CMD_RAW = 91                    # percent_to_raw(40); floor_sweep pins the table
CYCLES = 12
ON_S = 30.0                     # all-core burst
OFF_S = 25.0                    # cool-down crossing every tier boundary
INTERVAL = 1.0
ABORT_CPU_C = 93.0              # sustained custom 40% pins ~90; leave margin
RESULTS = os.path.join(HERE, "results")
GUARD_LOG = os.path.join(appctl.INSTALL, "thermal_guard.log")
GUARD_EVENT_PREFIX = "forced guard floor"


def count_guard_lines() -> int:
    try:
        with open(GUARD_LOG, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def read_new_guard_events(offset: int) -> list[dict]:
    """Guard force events appended after `offset` lines."""
    events: list[dict] = []
    try:
        with open(GUARD_LOG, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i < offset:
                    continue
                if GUARD_EVENT_PREFIX not in line:
                    continue
                head, _, tail = line.partition(GUARD_EVENT_PREFIX)
                floor_token = tail.split("%", 1)[0].strip()
                channel = "gpu" if "(gpu," in tail else "cpu"
                events.append({
                    "ts": head.strip("[] "),
                    "floor": int(floor_token),
                    "channel": channel,
                })
    except OSError:
        pass
    return events


def sample_rows(probe: ECProbe, fh, seconds: float, phase: str,
                cycle: int, state: dict) -> bool:
    """Sample for `seconds`; returns False when the abort trips."""
    started = time.time()
    hot = 0
    while time.time() - started < seconds:
        mark = time.time()
        row = probe.sample_once()
        row["phase"] = phase
        row["cycle"] = cycle
        row["loop_t"] = time.time() - started
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        if row["cpu_t"] >= ABORT_CPU_C:
            hot += 1
            if hot >= 5:
                print(f"  ABORT: cpu_t {row['cpu_t']:.1f} >= {ABORT_CPU_C} "
                      f"for 5 samples", flush=True)
                return False
        else:
            hot = 0
        time.sleep(max(0.0, INTERVAL - (time.time() - mark)))
    state["elapsed"] = state.get("elapsed", 0.0) + seconds
    return True


def analyze(rows_path: str, events: list[dict], elapsed_s: float) -> dict:
    rows = [json.loads(line) for line in open(rows_path, encoding="utf-8")]
    on_hot = [r for r in rows
              if r["phase"] == "on" and isinstance(r["cpu_t"], (int, float))
              and r["cpu_t"] >= 85.0
              and isinstance(r["fixed_raw"], (int, float))
              and r["fixed_raw"] > 0]
    off = [r for r in rows if r["phase"] == "off"
           and isinstance(r["fixed_raw"], (int, float)) and r["fixed_raw"] > 0]

    def raw_stats(samples: list) -> dict:
        if not samples:
            return {}
        values = [r["fixed_raw"] for r in samples]
        return {"n": len(values), "min_raw": min(values),
                "median_raw": statistics.median(values),
                "sag_below_cmd": sum(1 for v in values if v < CMD_RAW)}

    cpu_events = [e for e in events if e["channel"] == "cpu"]
    transitions = 0
    for prev, cur in zip(cpu_events, cpu_events[1:]):
        if prev["floor"] != cur["floor"]:
            transitions += 1
    gaps = []
    for prev, cur in zip(cpu_events, cpu_events[1:]):
        try:
            t0 = datetime.strptime(prev["ts"], "%Y-%m-%dT%H:%M:%S")
            t1 = datetime.strptime(cur["ts"], "%Y-%m-%dT%H:%M:%S")
            gaps.append((t1 - t0).total_seconds())
        except ValueError:
            pass

    # Continuous floors change value on every degree, so event counts no
    # longer separate "smooth tracking" from "pumping". What matters
    # acoustically is the duty trajectory itself: raw duty steps >= 18
    # (= 8 points ~ 2 degC) and >= 45 (= the old 20-point tier step).
    duty = [r["fixed_raw"] for r in rows
            if isinstance(r["fixed_raw"], (int, float)) and r["fixed_raw"] > 0]
    deltas = [abs(b - a) for a, b in zip(duty, duty[1:])]
    duty_steps = {}
    if deltas:
        ordered = sorted(deltas)
        duty_steps = {
            "n": len(deltas),
            "p50": statistics.median(deltas),
            "p95": ordered[int(len(ordered) * 0.95)],
            "max": max(deltas),
            "ge_18_raw": sum(1 for d in deltas if d >= 18),
            "ge_45_raw": sum(1 for d in deltas if d >= 45),
            "duty_min": min(duty),
            "duty_max": max(duty),
        }

    return {
        "elapsed_s": round(elapsed_s, 1),
        "commanded_raw": CMD_RAW,
        "on_hot_readback": raw_stats(on_hot),
        "off_readback": raw_stats(off),
        "guard_events_cpu": len(cpu_events),
        "guard_events_gpu": len(events) - len(cpu_events),
        "floor_timeline_cpu": [e["floor"] for e in cpu_events],
        "tier_transitions": transitions,
        "duty_steps": duty_steps,
        "force_gap_s_median": round(statistics.median(gaps), 1) if gaps else None,
        "force_gap_s_min": round(min(gaps), 1) if gaps else None,
        "cpu_t_max": max((r["cpu_t"] for r in rows
                          if isinstance(r["cpu_t"], (int, float))),
                         default=None),
        "cpu_t_min": min((r["cpu_t"] for r in rows
                          if isinstance(r["cpu_t"], (int, float))),
                         default=None),
        "rpm_max": max((max(r["rpm1"], r["rpm2"]) for r in rows), default=None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()  # --help only; the run itself takes no options
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("not elevated - EC WMI access needs admin; aborting",
              file=sys.stderr)
        return 3
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(RESULTS, exist_ok=True)
    rows_path = os.path.join(RESULTS, f"flap-{stamp}.jsonl")
    summary_path = os.path.join(RESULTS, f"flap-{stamp}.json")

    cfg = appctl.load_cfg()
    original = cfg.get("active_profile_name", "")
    appctl.ensure_app_running()
    bench.ensure_ladder_profiles(cfg, {"ac": (DUTY,)})
    appctl.apply_profile(bench.ladder_profile_name(DUTY), "custom")
    print(f"[flap] profile: Ladder-{DUTY} (custom, raw {CMD_RAW}); "
          f"{CYCLES}x {ON_S:.0f}s on / {OFF_S:.0f}s off", flush=True)

    probe = ECProbe()
    probe.connect()
    burners = Burners()
    guard_offset = count_guard_lines()
    state: dict = {}
    aborted = False
    try:
        with open(rows_path, "w", encoding="utf-8") as fh:
            for cycle in range(CYCLES):
                burners.start(LOAD_N["all"], ON_S + 5.0)
                print(f"  cycle {cycle + 1}/{CYCLES}: ON", flush=True)
                if not sample_rows(probe, fh, ON_S, "on", cycle, state):
                    aborted = True
                    break
                burners.stop()
                print(f"  cycle {cycle + 1}/{CYCLES}: OFF", flush=True)
                if not sample_rows(probe, fh, OFF_S, "off", cycle, state):
                    aborted = True
                    break
    finally:
        burners.stop()

    events = read_new_guard_events(guard_offset)
    bench.remove_bench_owned_profiles()
    appctl.restore_active(original)

    summary = analyze(rows_path, events, state.get("elapsed", 0.0))
    summary["aborted"] = aborted
    summary["rows"] = rows_path
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"[flap] cpu events {summary['guard_events_cpu']}, "
          f"tier transitions {summary['tier_transitions']}, "
          f"gap median {summary['force_gap_s_median']} s")
    on_r = summary["on_hot_readback"]
    off_r = summary["off_readback"]
    if on_r:
        print(f"[flap] readback ON-hot: median {on_r['median_raw']} / min "
              f"{on_r['min_raw']} (cmd {CMD_RAW}), below-cmd "
              f"{on_r['sag_below_cmd']}/{on_r['n']}")
    if off_r:
        print(f"[flap] readback OFF:    median {off_r['median_raw']} / min "
              f"{off_r['min_raw']} (cmd {CMD_RAW}), below-cmd "
              f"{off_r['sag_below_cmd']}/{off_r['n']}")
    print(f"summary -> {summary_path}")
    print(f"FLAP-{'ABORT' if aborted else 'OK'}")
    return 2 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
