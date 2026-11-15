# -*- coding: utf-8 -*-
"""Bench data analysis: steady-state stats, ladder inversion, curve synthesis.

Importable without side effects (bench.py calls into it mid-run); the CLI
re-derives derived_curves.json + report.md from an existing bench.jsonl.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

FULL_CHARGED_CAPACITY_MWH = 99013.0
DUTY_RAW_MAX = 227
GRID = list(range(50, 100, 5))
GUARD_FLOORS = ((80, 40), (85, 60), (90, 80))
BENCH_PHASES = ("ladder", "profiles", "validate")


def guard_floor(temp_c: float) -> int:
    """App thermal-guard floor in force at a temperature."""
    floor = 0
    for threshold, duty in GUARD_FLOORS:
        if temp_c >= threshold:
            floor = duty
    return floor


def compute_guard(cpu_t, duty_cmd, fixed_raw) -> bool:
    """App thermal-guard contamination: >= 80 C while the EC runs above command."""
    if duty_cmd is None or cpu_t is None or cpu_t < 80:
        return False
    if fixed_raw is None or fixed_raw < 0:
        return False
    return fixed_raw > math.ceil(duty_cmd / 100.0 * DUTY_RAW_MAX) + 10


def row_guard(row: dict) -> bool:
    return compute_guard(row.get("cpu_t"), row.get("duty_cmd"),
                         row.get("fixed_raw"))


# ---------------------------------------------------------------- data access

def load_rows(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def meta_of(rows: list[dict]) -> dict:
    for row in rows:
        if row.get("meta"):
            return row
    return {}


def skipped_ids(rows: list[dict]) -> dict[str, str]:
    return {row["trial_id"]: row.get("skipped", "?")
            for row in rows if row.get("skipped")}


def completed_ids(rows: list[dict]) -> set[str]:
    return {row["trial_id"] for row in rows if row.get("trial_end")}


def trial_groups(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("meta") or row.get("trial_end") or row.get("skipped"):
            continue
        if row.get("phase") in BENCH_PHASES:
            groups[row["trial_id"]].append(row)
    return dict(groups)


def steady_rows(trial: list[dict], window_s: float) -> list[dict]:
    if not trial:
        return []
    t_end = max(row["ts"] for row in trial)
    return [row for row in trial if row["ts"] >= t_end - window_s]


def trial_stats(rows: list[dict], trial_id: str, window_s: float) -> dict | None:
    trial = steady_rows(rows, window_s)
    if len(trial) < 3:
        return None
    cpu = [r["cpu_t"] for r in trial if r.get("cpu_t", -1) >= 0]
    duty = [r["fixed_raw"] for r in trial if r.get("fixed_raw", -1) >= 0]
    rpm1 = [r["rpm1"] for r in trial if r.get("rpm1", -1) >= 0]
    rpm2 = [r["rpm2"] for r in trial if r.get("rpm2", -1) >= 0]
    perf = [r["proc_perf"] for r in trial if r.get("proc_perf", -1) >= 0]
    if not cpu:
        return None
    return {
        "trial_id": trial_id,
        "phase": rows[0].get("phase"),
        "power": rows[0].get("power"),
        "profile": rows[0].get("profile"),
        "load": rows[0].get("load"),
        "duty_cmd": rows[0].get("duty_cmd"),
        "rep": rows[0].get("rep"),
        "steady_cpu_mean": statistics.fmean(cpu),
        "steady_cpu_max": max(cpu),
        "duty_pct_actual": (statistics.fmean(duty) / DUTY_RAW_MAX * 100.0
                            if duty else -1.0),
        "rpm1_mean": statistics.fmean(rpm1) if rpm1 else -1.0,
        "rpm2_mean": statistics.fmean(rpm2) if rpm2 else -1.0,
        "min_proc_perf": min(perf) if perf else -1.0,
        "guard_active": any(row_guard(r) for r in trial),
        "n_rows": len(trial),
    }


def all_trial_stats(rows: list[dict], window_s: float) -> list[dict]:
    stats = []
    for trial_id, group in sorted(trial_groups(rows).items()):
        st = trial_stats(group, trial_id, window_s)
        if st:
            stats.append(st)
    return stats


# ----------------------------------------------------------- ladder inversion

def duty_for(points, cap: float) -> tuple[int, bool]:
    """Smallest duty whose steady temp <= cap, inverted through the points.

    Returns (duty, cooling_limited): the ceil-interpolated crossing duty, the
    first point's duty when it already satisfies the cap, and (100, True)
    when no measured duty reaches the cap.
    """
    pts = sorted(points)
    below = [p for p in pts if p[1] <= cap]
    if not below:
        return 100, True
    first = below[0]
    idx = pts.index(first)
    if idx == 0:
        return int(first[0]), False
    prev = pts[idx - 1]
    span = prev[1] - first[1]
    if span <= 0:
        return int(first[0]), False
    duty = prev[0] + (prev[1] - cap) / span * (first[0] - prev[0])
    return math.ceil(duty), False


def synthesize_curve(points_by_load: dict[str, list[tuple[float, float]]]
                     ) -> tuple[list[list[int]], dict]:
    """Empirical fan curve: per grid temp, the max duty any load demands.

    Loads with < 2 valid points are ignored; at a temperature a load cannot
    reach even at 100 % (cooling-limited) it is excluded from that point; if
    every load is excluded the guard floor stands in and the temperature is
    reported as cooling-limited. The coldest grid point is 0 when every
    measured load's lowest duty already holds that temperature. Guard floors
    apply at >= 80/85/90 C and the curve is repaired to be nondecreasing.
    """
    loads = {name: sorted(pts) for name, pts in points_by_load.items()
             if len(pts) >= 2}
    curve: dict[int, int] = {}
    cooling_limited: list[int] = []
    for temp in GRID:
        candidates = []
        for pts in loads.values():
            duty, is_limited = duty_for(pts, temp)
            if not is_limited:
                candidates.append(duty)
        if candidates:
            value = max(candidates)
            if temp == GRID[0] and loads and all(
                    pts[0][1] <= GRID[0] for pts in loads.values()):
                value = 0
            value = max(value, guard_floor(temp))
        else:
            value = guard_floor(temp)
            cooling_limited.append(temp)
        curve[temp] = int(min(100, max(0, value)))
    monotone: list[list[int]] = []
    running = 0
    for temp in GRID:
        running = max(running, curve[temp])
        monotone.append([temp, running])
    meta = {
        "cooling_limited": cooling_limited,
        "loads_used": sorted(loads),
        "loads_ignored": sorted(name for name in points_by_load
                                if name not in loads),
    }
    return monotone, meta


def derive_curves(rows: list[dict], cap: float, window_s: float,
                  full_capacity_mwh: float = FULL_CHARGED_CAPACITY_MWH,
                  ac_fallback: dict | None = None,
                  battery_fallback: dict | None = None) -> dict:
    """Invert the ladder into per-power curves; merge battery with AC if partial.

    ac_fallback / battery_fallback: previously derived sections, used verbatim
    when the rows contain no ladder data for that power (single-power runs
    must not overwrite a good curve with a degenerate one).
    """
    stats = all_trial_stats(rows, window_s)
    result: dict = {"cap": cap, "grid": GRID}
    for power in ("ac", "battery"):
        by_load: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for st in stats:
            if (st["phase"] == "ladder" and st["power"] == power
                    and not st["guard_active"] and st["duty_pct_actual"] >= 0):
                by_load[st["load"]].append(
                    (st["duty_pct_actual"], st["steady_cpu_mean"]))
        curve, meta = synthesize_curve(by_load)
        meta["points"] = {load: sorted(pts) for load, pts in by_load.items()}
        meta["curve"] = curve
        result[power] = meta
    if ac_fallback and not result["ac"].get("points") and ac_fallback.get("curve"):
        result["ac"] = ac_fallback
    fresh_battery = result["battery"]
    if battery_fallback and not fresh_battery.get("points") and battery_fallback.get("curve"):
        result["battery"] = battery_fallback
        fresh_battery = None

    if fresh_battery is not None:
        battery_meta = fresh_battery
        skipped = skipped_ids(rows)
        battery_skipped = sorted(tid for tid in skipped
                                 if tid.startswith("ladder-battery"))
        loads_ok = all(len(battery_meta["points"].get(load, [])) >= 3
                       for load in ("idle", "dual", "all"))
        partial = bool(battery_skipped) or not loads_ok
        battery_meta["partial"] = partial
        battery_meta["skipped_trials"] = battery_skipped
        if partial:
            ac_by_t = dict(result["ac"]["curve"])
            battery_meta["curve"] = [
                [temp, max(duty, ac_by_t[temp])]
                for temp, duty in battery_meta["curve"]]
            battery_meta["merged_with_ac"] = True
    return result


# -------------------------------------------------------------------- report

#: Physically implausible discharge for this machine; BatteryStatus glitches
#: up to ~1 W·h/ms-scale garbage have been observed under load.
MAX_PLAUSIBLE_DISCHARGE_MW = 250000.0


def battery_endurance(rows: list[dict], full_capacity_mwh: float
                      ) -> Optional[dict]:
    """Median all-core discharge + estimated minutes from 90 % charge.

    Uses the median (robust to sensor glitches) over ladder+profile rows,
    keeping only plausible samples (>0 and <= MAX_PLAUSIBLE_DISCHARGE_MW).
    """
    dis = [r["discharge_mw"] for r in rows
           if r.get("power") == "battery" and r.get("load") == "all"
           and 0 < r.get("discharge_mw", -1) <= MAX_PLAUSIBLE_DISCHARGE_MW]
    if not dis:
        return None
    median_dis = statistics.median(dis)
    minutes = 0.9 * full_capacity_mwh / median_dis * 60.0
    return {"median_discharge_mw": median_dis, "minutes": minutes,
            "samples": len(dis)}


def write_report(rows: list[dict], curves: dict | None, window_s: float,
                 cap: float, out_path: str,
                 full_capacity_mwh: float = FULL_CHARGED_CAPACITY_MWH) -> None:
    meta = meta_of(rows)
    stats = all_trial_stats(rows, window_s)
    skipped = skipped_ids(rows)
    lines: list[str] = []
    add = lines.append

    add("# Gigahelm bench report")
    add("")
    add(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"- Steady window: {window_s:.0f} s (tail of each step)")
    add(f"- Sustained CPU cap: {cap:g} C (validate pass <= cap + 1.5)")
    if meta:
        add(f"- Run: {'full' if meta.get('full') else 'quick'}, started "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(meta.get('ts', 0)))}, "
            f"battery full charge "
            f"{float(meta.get('full_charged_capacity', full_capacity_mwh)):.0f} mWh")
    add("")
    add("## Controller dynamics (context)")
    add("")
    add("1.5 s control loop; instant step-up, -5 duty points per step-down tick; "
        "2 C target hysteresis; 3 C start-from-zero band; thermal guards "
        "80 C->40 %, 85 C->60 %, 90 C->80 %; duty raw = ceil(pct/100*227), min "
        "spin 25 % (raw 57); both fans share one duty = max(CPU, GPU target), "
        "so CPU-only characterization determines the shared duty.")
    add("")

    ladder = [s for s in stats if s["phase"] == "ladder"]
    if ladder:
        add("## Ladder (fixed duty per load)")
        for power in ("ac", "battery"):
            block = [s for s in ladder if s["power"] == power]
            if not block:
                continue
            add("")
            add(f"### {power.upper()}")
            add("")
            add("| cmd % | load | cpu C mean | cpu C max | duty % actual |"
                " rpm1 | rpm2 | perf min % | guard |")
            add("|---|---|---|---|---|---|---|---|---|")
            agg: dict[tuple, list[dict]] = defaultdict(list)
            for st in block:
                agg[(st["duty_cmd"], st["load"])].append(st)
            for (duty, load), group in sorted(agg.items()):
                cpu_mean = statistics.fmean(s["steady_cpu_mean"] for s in group)
                cpu_max = max(s["steady_cpu_max"] for s in group)
                duty_pct = statistics.fmean(s["duty_pct_actual"] for s in group)
                rpm1_vals = [s["rpm1_mean"] for s in group if s["rpm1_mean"] >= 0]
                rpm2_vals = [s["rpm2_mean"] for s in group if s["rpm2_mean"] >= 0]
                rpm1 = statistics.fmean(rpm1_vals) if rpm1_vals else -1
                rpm2 = statistics.fmean(rpm2_vals) if rpm2_vals else -1
                perf = min(s["min_proc_perf"] for s in group)
                guard = "YES" if any(s["guard_active"] for s in group) else ""
                add(f"| {duty:g} | {load} | {cpu_mean:.1f} | {cpu_max:.1f} | "
                    f"{duty_pct:.1f} | {rpm1:.0f} | {rpm2:.0f} | {perf:.0f} |"
                    f" {guard} |")

    if curves:
        add("")
        add("## Derived curves (grid 50..95 C step 5)")
        add("")
        add("| temp C | Tuned AC % | Tuned Battery % |")
        add("|---|---|---|")
        ac_curve = dict(curves["ac"]["curve"])
        bat_curve = dict(curves["battery"]["curve"])
        for temp in GRID:
            note = " (partial)" if (
                temp == GRID[-1] and curves["battery"].get("partial")) else ""
            add(f"| {temp} | {ac_curve[temp]} | {bat_curve[temp]}{note} |")
        add("")
        for power in ("ac", "battery"):
            meta_p = curves[power]
            parts = [f"loads used: "
                     f"{', '.join(meta_p.get('loads_used', [])) or 'none'}"]
            if meta_p.get("loads_ignored"):
                parts.append(f"ignored (insufficient points): "
                             f"{', '.join(meta_p['loads_ignored'])}")
            if meta_p.get("cooling_limited"):
                parts.append(f"cooling-limited at "
                             f"{', '.join(str(t) + ' C' for t in meta_p['cooling_limited'])}")
            if meta_p.get("partial"):
                parts.append("PARTIAL battery data -> merged with AC curve")
            if meta_p.get("skipped_trials"):
                parts.append(f"skipped trials: "
                             f"{', '.join(meta_p['skipped_trials'])}")
            add(f"- **{power}**: " + "; ".join(parts))
        add("")

    comp = [s for s in stats if s["phase"] in ("profiles", "validate")]
    if comp:
        add("## Profile comparison")
        add("")
        add("| profile | power | load | cpu C mean | cpu C range | rpm1 |"
            " perf min % | duty % actual |")
        add("|---|---|---|---|---|---|---|---|")
        agg = defaultdict(list)
        for st in comp:
            agg[(st["profile"], st["power"], st["load"])].append(st)
        for (profile, power, load), group in sorted(agg.items()):
            cpu_mean = statistics.fmean(s["steady_cpu_mean"] for s in group)
            lo = min(s["steady_cpu_mean"] for s in group)
            hi = max(s["steady_cpu_mean"] for s in group)
            rpm1_vals = [s["rpm1_mean"] for s in group if s["rpm1_mean"] >= 0]
            rpm1 = statistics.fmean(rpm1_vals) if rpm1_vals else -1
            perf = min(s["min_proc_perf"] for s in group)
            duty_pct = statistics.fmean(s["duty_pct_actual"] for s in group)
            rng = f"{lo:.1f}-{hi:.1f}" if hi > lo else f"{lo:.1f}"
            add(f"| {profile} | {power} | {load} | {cpu_mean:.1f} | {rng} | "
                f"{rpm1:.0f} | {perf:.0f} | {duty_pct:.1f} |")
        add("")

    val = [s for s in stats if s["phase"] == "validate"]
    if val:
        add("## Validation")
        add("")
        for st in val:
            ok = st["steady_cpu_mean"] <= cap + 1.5
            add(f"- {st['trial_id']}: steady {st['steady_cpu_mean']:.1f} C "
                f"(max {st['steady_cpu_max']:.1f} C) -> "
                f"{'PASS' if ok else 'FAIL'}")
        add("")

    endurance = battery_endurance(rows, full_capacity_mwh)
    if endurance:
        add("## Battery endurance estimate")
        add("")
        add(f"- Median all-core discharge: "
            f"{endurance['median_discharge_mw']:.0f} mW "
            f"({endurance['samples']} plausible samples)")
        add(f"- Estimated all-core minutes from 90 %: "
            f"{endurance['minutes']:.0f} min")
        add("")

    if skipped:
        add("## Skipped trials")
        add("")
        for tid, why in sorted(skipped.items()):
            add(f"- {tid}: {why}")
        add("")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ----------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Bench analysis CLI")
    parser.add_argument("--in", dest="inp", nargs="+",
                        default=[os.path.join(here, "results", "bench.jsonl")],
                        help="one or more bench JSONL files (concatenated)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--window", type=float, default=None,
                        help="steady window seconds (default: run meta)")
    parser.add_argument("--cap", type=float, default=None)
    args = parser.parse_args(argv)
    rows: list[dict] = []
    for path in args.inp:
        rows.extend(load_rows(path))
    if not rows:
        print(f"no rows in {args.inp}", file=sys.stderr)
        return 1
    meta = meta_of(rows)
    window = args.window or float(meta.get("window", 60))
    cap = args.cap or float(meta.get("cap", 86))
    full_cap = float(meta.get("full_charged_capacity",
                              FULL_CHARGED_CAPACITY_MWH))
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.inp))
    curves = derive_curves(rows, cap, window, full_cap)
    curves_path = os.path.join(out_dir, "derived_curves.json")
    with open(curves_path, "w", encoding="utf-8") as fh:
        json.dump(curves, fh, indent=2)
    write_report(rows, curves, window, cap,
                 os.path.join(out_dir, "report.md"), full_cap)
    print(f"wrote {curves_path} and {os.path.join(out_dir, 'report.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
