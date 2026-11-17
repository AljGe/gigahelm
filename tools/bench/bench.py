# -*- coding: utf-8 -*-
"""Gigahelm fan-profile benchmark orchestrator (run elevated).

Phases:
  selftest   create+apply Bench-Ladder (custom 40), sample a dual-core load
             for 20 s, verify rows + heartbeat, restore.
  ladder     fixed-duty matrix per power source (Bench-Ladder profile).
  profiles   existing profiles (Config 1, Gaming, Silent) under dual/all load.
  validate   derived Tuned AC / Tuned Battery under dual/all load.
  all        ladder -> profiles -> derive+install -> validate -> report.

Safety abort (results/ABORT.txt, exit 2): cpu_t >= 95 C for 8 consecutive
samples, commanded-but-stalled fans for 6 samples, or two failed profile
applications. Abort actions: emergency 100 % fans, load stopped, cooldown to
70 C, previous profile restored.

Rows append to --out as JSON lines; a per-trial end marker makes --resume
skip completed trials and rewrite the file without partial-trial leftovers.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appctl
import analyze
from ec_probe import ECProbe, write_emergency_100
from loadgen import LOAD_N, Burners

AC_DUTIES = (25, 40, 55, 70, 85, 100)
BATTERY_DUTIES = (40, 70, 100)
LADDER_LOADS = ("idle", "dual", "all")
PROFILE_LOADS = ("dual", "all")
PROFILES_PHASE = ("Config 1", "Gaming", "Silent")
STEP_S = {"full": {"ladder": 180.0, "profiles": 240.0, "validate": 240.0},
          "quick": {"ladder": 90.0, "profiles": 120.0, "validate": 120.0}}
WINDOW_S = {"full": 60.0, "quick": 30.0}
SAMPLE_INTERVAL = 1.0
ABORT_TEMP_C = 95.0
ABORT_STALL_RAW = 57.0
BATTERY_HARD_GUARD_FRAC = 0.40
PROFILES_BATTERY_MIN_FRAC = 0.60
BATTERY_START_MIN_FRAC = 0.90
TUNED_AC = "Tuned AC"
TUNED_BATTERY = "Tuned Battery"
# One profile per commanded duty: the 2026-11-16 night run showed that
# editing a single Bench-Ladder profile in place is racy - the app's
# debounced full-config save can clobber the bench's edit between the file
# write and the app's reload, freezing the EC at a stale duty. Switching
# profiles by NAME never failed, so the ladder switches between pre-created
# profiles instead (created idempotently per run, removed at the end).
LADDER_PROFILE_PREFIX = "Ladder-"
LADDER_PROFILE = "Bench-Ladder"  # legacy name, cleaned up with the rest


def ladder_profile_name(duty: int) -> str:
    return f"{LADDER_PROFILE_PREFIX}{duty}"


def ensure_ladder_profiles(cfg: dict, duties_by_power: dict) -> None:
    """Create/refresh one custom-mode profile per commanded duty. Idempotent;
    single atomic save."""
    _, defaults = phase_donor_profile(cfg)
    seen = set()
    for duties in duties_by_power.values():
        for duty in duties:
            name = ladder_profile_name(duty)
            if name in seen:
                continue
            seen.add(name)
            appctl.ensure_profile(cfg, name, copy.deepcopy(defaults))
            appctl.edit_profile(cfg, name, fan_mode="custom",
                                custom_fan_speed=duty)
    appctl.save_cfg_atomic(cfg)


def remove_bench_owned_profiles() -> None:
    """Drop Ladder-* (and the legacy Bench-Ladder) profiles after a run."""
    cfg = appctl.load_cfg()
    profiles = cfg.get("profiles", {})
    doomed = [n for n in profiles
              if n == LADDER_PROFILE or n.startswith(LADDER_PROFILE_PREFIX)]
    if not doomed:
        return
    for name in doomed:
        profiles.pop(name, None)
    cfg["profile_order"] = [n for n in cfg.get("profile_order", [])
                            if n not in doomed]
    if cfg.get("active_profile_name") in doomed:
        cfg["active_profile_name"] = next(
            (n for n in cfg["profile_order"] if n in profiles), "")
    appctl.save_cfg_atomic(cfg)
    appctl._reload_via_cli()
    print("[bench] bench-owned ladder profiles removed")


def resolve_profile_names(cfg: dict) -> tuple[str, ...]:
    """Profiles-phase set from the installed config.

    The 2025 tuning-session names (Config 1 / Gaming / Silent) are long gone
    from real configs; take up to three auto-mode profiles the user actually
    has, excluding bench-owned ones. Falls back to any profile when none is
    auto-mode (their fan_mode is left untouched; rows still characterize the
    profile as the app drives it)."""
    reserved = {LADDER_PROFILE, TUNED_AC, TUNED_BATTERY}
    profiles = cfg.get("profiles", {})
    names = [n for n, p in profiles.items() if n not in reserved]
    autos = [n for n in names if profiles[n].get("fan_mode") == "auto"]
    return tuple((autos or names)[:3])


def phase_donor_profile(cfg: dict) -> tuple[str, dict]:
    """(name, profile) donating defaults/gpu-table for bench-created profiles:
    the first auto-mode profile outside bench ownership, else the first."""
    profiles = cfg.get("profiles", {})
    reserved = {LADDER_PROFILE, TUNED_AC, TUNED_BATTERY}
    for name, profile in profiles.items():
        if name not in reserved and profile.get("fan_mode") == "auto":
            return name, profile
    for name, profile in profiles.items():
        return name, profile
    raise KeyError("installed config has no profiles")
REPS = {("ladder", "ac"): (1, 2), ("ladder", "battery"): (1,),
        ("profiles", "ac"): (1, 2), ("profiles", "battery"): (1,),
        ("validate", "ac"): (1,), ("validate", "battery"): (1,)}


class RowLog:
    def __init__(self, path: str) -> None:
        self.path = path

    def append(self, row: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_trials(phases: list[str], powers: list[str],
                 duties: dict[str, tuple[int, ...]] | None = None,
                 max_rep: int | None = None,
                 profile_names: tuple[str, ...] = PROFILES_PHASE) -> list[dict]:
    """Trial matrix, AC block first so plug/unplug happens once."""
    duties = duties or {}
    trials: list[dict] = []
    for power in powers:
        for phase in phases:
            if phase == "ladder":
                default = AC_DUTIES if power == "ac" else BATTERY_DUTIES
                duty_list = duties.get("ladder") or default
                for duty in duty_list:
                    for load in LADDER_LOADS:
                        for rep in REPS[("ladder", power)]:
                            if max_rep is not None and rep > max_rep:
                                continue
                            trials.append(dict(
                                trial_id=f"ladder-{power}-{load}-d{duty}-r{rep}",
                                phase="ladder", power=power,
                                profile=ladder_profile_name(duty), load=load,
                                duty_cmd=duty, rep=rep))
            elif phase == "profiles":
                for profile in (profile_names or PROFILES_PHASE):
                    for load in PROFILE_LOADS:
                        for rep in REPS[("profiles", power)]:
                            if max_rep is not None and rep > max_rep:
                                continue
                            trials.append(dict(
                                trial_id=f"profiles-{power}-{profile}-{load}-r{rep}",
                                phase="profiles", power=power, profile=profile,
                                load=load, duty_cmd=None, rep=rep))
            elif phase == "validate":
                tuned = TUNED_AC if power == "ac" else TUNED_BATTERY
                for load in PROFILE_LOADS:
                    trials.append(dict(
                        trial_id=f"validate-{power}-{tuned}-{load}-r1",
                        phase="validate", power=power, profile=tuned,
                        load=load, duty_cmd=None, rep=1))
    return trials


def prepare_resume(out_path: str) -> set[str]:
    """Drop rows of incomplete trials (they re-run); return completed ids."""
    if not os.path.exists(out_path):
        return set()
    rows = analyze.load_rows(out_path)
    done = analyze.completed_ids(rows)
    keep = [row for row in rows
            if row.get("meta") or row.get("trial_id") in done]
    if len(keep) != len(rows):
        tmp = out_path + ".resume-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in keep:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, out_path)
        print(f"[bench] resume: kept {len(keep)} rows, "
              f"dropped {len(rows) - len(keep)} partial rows")
    return done


class Bench:
    def __init__(self, args) -> None:
        self.args = args
        os.makedirs(RESULTS, exist_ok=True)
        self.out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(self.out_path), exist_ok=True)
        self.probe = ECProbe()
        self.burners = Burners()
        self.window_s = WINDOW_S["full" if args.full else "quick"]
        self.step_s = STEP_S["full" if args.full else "quick"]
        self.abort_reason: str | None = None
        self.apply_failures = 0
        self.log = RowLog(self.out_path)

    # ------------------------------------------------------------- sampling
    def sample_rows(self, trial: dict, duration: float,
                    log: RowLog | None = None) -> int:
        """Sample ~1 Hz, append rows, run safety monitors; returns row count."""
        log = log or self.log
        duty_cmd = trial["duty_cmd"]
        step_end = time.time() + duration
        hot = stall = 0
        count = 0
        while time.time() < step_end:
            started = time.time()
            sample = self.probe.sample_once()
            guard = analyze.compute_guard(sample.get("cpu_t"), duty_cmd,
                                          sample.get("fixed_raw"))
            row = {"trial_id": trial["trial_id"], "phase": trial["phase"],
                   "power": trial["power"], "profile": trial["profile"],
                   "load": trial["load"], "duty_cmd": duty_cmd,
                   "rep": trial["rep"], "guard_active": guard, **sample}
            log.append(row)
            count += 1
            cpu = sample.get("cpu_t", -1.0)
            fixed_raw = sample.get("fixed_raw", -1.0)
            rpm1 = sample.get("rpm1", -1)
            rpm2 = sample.get("rpm2", -1)
            if cpu >= ABORT_TEMP_C:
                hot += 1
            else:
                hot = 0
            if fixed_raw >= ABORT_STALL_RAW and rpm1 == 0 and rpm2 == 0:
                stall += 1
            else:
                stall = 0
            if hot >= 8:
                self.abort(f"cpu_t >= {ABORT_TEMP_C:.0f} C for 8 consecutive "
                           f"samples (last {cpu:.1f} C) in {trial['trial_id']}")
                return count
            if stall >= 6:
                self.abort(f"fans commanded (fixed_raw={fixed_raw:.0f}) but 0 rpm "
                           f"for 6 consecutive samples in {trial['trial_id']}")
                return count
            time.sleep(max(0.0, SAMPLE_INTERVAL - (time.time() - started)))
        return count

    def abort(self, reason: str) -> None:
        if self.abort_reason:
            return
        self.abort_reason = reason
        print(f"[bench] ABORT: {reason}", file=sys.stderr, flush=True)

    def abort_actions(self, original_active: str) -> None:
        try:
            self.burners.stop()
        except Exception:
            pass
        try:
            write_emergency_100(self.probe)
        except Exception as exc:
            print(f"[bench] emergency write failed: {exc}", file=sys.stderr)
        appctl.wait_cool(self.probe, target_c=70.0, timeout=600.0)
        appctl.restore_active(original_active)
        with open(os.path.join(RESULTS, "ABORT.txt"), "w", encoding="utf-8") as fh:
            fh.write(self.abort_reason + "\n")

    # --------------------------------------------------------------- trials
    def run_trial(self, trial: dict, duration: float, full_capacity: float
                  ) -> tuple[str, str]:
        """Returns (status, detail): ok | skip | skip-power."""
        power, phase = trial["power"], trial["phase"]
        if power == "battery":
            sample = self.probe.sample_once()
            remaining = sample["remaining_mwh"]
            if remaining < 0:
                return "skip-power", "battery telemetry unavailable"
            frac = remaining / full_capacity
            if frac < BATTERY_HARD_GUARD_FRAC:
                return "skip-power", (f"capacity {frac:.0%} < "
                                      f"{BATTERY_HARD_GUARD_FRAC:.0%} guard")
            if phase == "profiles" and frac < PROFILES_BATTERY_MIN_FRAC:
                return "skip", (f"charge {frac:.0%} < "
                                f"{PROFILES_BATTERY_MIN_FRAC:.0%} for profiles")
        try:
            appctl.await_power(self.probe, want_online=(power == "ac"))
        except appctl.SkipPhase as exc:
            return "skip-power", str(exc)
        appctl.wait_cool(self.probe, target_c=60.0, timeout=240.0)
        try:
            appctl.apply_profile(
                trial["profile"],
                "custom" if trial["duty_cmd"] is not None else "auto")
        except appctl.ApplyError as exc:
            self.apply_failures += 1
            if self.apply_failures >= 2:
                self.abort(f"apply_profile failed twice: {exc}")
            return "skip", f"apply failed: {exc}"
        self.apply_failures = 0
        n = LOAD_N[trial["load"]]
        if n:
            self.burners.start(n, duration)
        try:
            count = self.sample_rows(trial, duration)
        finally:
            if n:
                self.burners.stop()
        self.log.append({"trial_id": trial["trial_id"], "trial_end": True,
                         "rows": count, "ts": time.time()})
        return "ok", f"{count} rows"

    # ------------------------------------------------------------ tuned set
    def derive_and_install(self, cap: float, full_capacity: float) -> dict | None:
        """Derive curves from collected rows, write JSON, install Tuned profiles."""
        rows = analyze.load_rows(self.out_path)
        if not any(r.get("phase") == "ladder" for r in rows):
            return None
        existing = self.load_existing_curves() or {}
        curves = analyze.derive_curves(rows, cap, self.window_s, full_capacity,
                                       ac_fallback=existing.get("ac"),
                                       battery_fallback=existing.get("battery"))
        with open(os.path.join(RESULTS, "derived_curves.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(curves, fh, indent=2)
        ensure_tuned_profiles(curves)
        print("[bench] Tuned AC / Tuned Battery installed from derived curves",
              flush=True)
        return curves

    def load_existing_curves(self) -> dict | None:
        path = os.path.join(RESULTS, "derived_curves.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


def ensure_tuned_profiles(curves: dict) -> None:
    """Idempotently write Tuned AC / Tuned Battery into the installed config.

    Defaults (charge policy etc.) and the GPU table (no GPU burner was
    characterized) come from the first real auto-mode profile; Tuned Battery
    charges to a custom 80 % limit; Tuned AC keeps the donor's battery
    policy.
    """
    cfg = appctl.load_cfg()
    _, defaults = phase_donor_profile(cfg)
    defaults = copy.deepcopy(defaults)
    silent_gpu = defaults["gpu_fan_table"]
    appctl.ensure_profile(cfg, TUNED_AC, defaults)
    appctl.edit_profile(cfg, TUNED_AC, fan_mode="auto",
                        cpu_fan_table=curves["ac"]["curve"],
                        gpu_fan_table=silent_gpu)
    appctl.ensure_profile(cfg, TUNED_BATTERY, defaults)
    appctl.edit_profile(cfg, TUNED_BATTERY, fan_mode="auto",
                        cpu_fan_table=curves["battery"]["curve"],
                        gpu_fan_table=silent_gpu,
                        battery_charge_policy="custom",
                        battery_charge_threshold=80)
    appctl.save_cfg_atomic(cfg)


def run_selftest(bench: Bench) -> int:
    cfg = appctl.load_cfg()
    original_active = cfg.get("active_profile_name", "Config 1")
    _, defaults = phase_donor_profile(cfg)
    appctl.ensure_profile(cfg, ladder_profile_name(40), defaults)
    appctl.edit_profile(cfg, ladder_profile_name(40), fan_mode="custom",
                        custom_fan_speed=40)
    appctl.save_cfg_atomic(cfg)
    # apply_profile asserts last_mode == 'custom' and heartbeat age < 3 s.
    appctl.apply_profile(ladder_profile_name(40), "custom")
    trial = dict(trial_id="selftest-dual-r0", phase="selftest", power="ac",
                 profile=ladder_profile_name(40), load="dual", duty_cmd=40, rep=0)
    selftest_path = os.path.join(RESULTS, "selftest.jsonl")
    if os.path.exists(selftest_path):
        os.remove(selftest_path)  # a prior failed run must not pad the count
    selftest_log = RowLog(selftest_path)
    bench.burners.start(LOAD_N["dual"], 20.0)
    try:
        count = bench.sample_rows(trial, 20.0, log=selftest_log)
    finally:
        bench.burners.stop()
    selftest_log.append({"trial_id": trial["trial_id"], "trial_end": True,
                         "rows": count, "ts": time.time()})
    appctl.restore_active(original_active)
    rows = [r for r in analyze.load_rows(selftest_path)
            if r.get("trial_id") == "selftest-dual-r0" and not r.get("trial_end")]
    missing = [r for r in rows if "guard_active" not in r]
    period = ((rows[-1]["ts"] - rows[0]["ts"]) / (len(rows) - 1)
              if len(rows) >= 2 else float("nan"))
    if len(rows) < 18 or missing:
        print(f"[selftest] FAIL: {len(rows)} rows (< 18, period "
              f"{period:.2f} s) or {len(missing)} missing guard_active",
              file=sys.stderr)
        return 1
    print(f"[selftest] PASS: {len(rows)} rows, period {period:.2f} s, mode="
          f"{appctl.last_mode()!r}, heartbeat age "
          f"{appctl.heartbeat_age():.1f} s")
    return 0


def run_external(bench: Bench, seconds: float, profile: str | None) -> int:
    """Manual GPU phase: sampling only, no loadgen (you run the game)."""
    name = profile or appctl.load_cfg().get("active_profile_name", "unknown")
    power = "ac" if appctl.power_source(bench.probe) else "battery"
    trial = dict(trial_id=f"external-{int(time.time())}", phase="external",
                 power=power, profile=name, load="external", duty_cmd=None,
                 rep=0)
    count = bench.sample_rows(trial, seconds)
    bench.log.append({"trial_id": trial["trial_id"], "trial_end": True,
                      "rows": count, "ts": time.time()})
    print(f"[bench] external phase done: {count} rows")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gigahelm bench orchestrator")
    parser.add_argument("--phase", default="all",
                        choices=("selftest", "ladder", "profiles",
                                 "validate", "all"))
    parser.add_argument("--power", default="both",
                        choices=("ac", "battery", "both"))
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-derive", action="store_true",
                        help="keep installed Tuned profiles; do not re-derive "
                             "from rows (use when ladder rows are incomplete "
                             "and curves were installed by hand)")
    parser.add_argument("--out", default=os.path.join("results", "bench.jsonl"))
    parser.add_argument("--cap", type=float, default=86.0)
    parser.add_argument("--reps", type=int, default=None,
                        help="override: run at most N replicates")
    parser.add_argument("--duties", default=None,
                        help="override ladder duties, e.g. 40,70,100")
    parser.add_argument("--loads", default=None,
                        help="override trial loads, e.g. all (comma list of "
                             "idle/dual/all; default = plan's loads)")
    parser.add_argument("--external", type=float, default=0.0, metavar="SECONDS",
                        help="manual GPU phase: sample only for SECONDS")
    parser.add_argument("--profile", default=None,
                        help="profile name recorded for --external rows")
    args = parser.parse_args(argv)
    args.full = not args.quick  # default full

    bench = Bench(args)
    try:
        bench.probe.connect()
    except Exception as exc:
        print(f"[bench] EC WMI unavailable ({exc}); run elevated via "
              f"run_full.ps1", file=sys.stderr)
        return 4
    if bench.probe.sample_once()["cpu_t"] < 0:
        print("[bench] EC reads failed (cpu_t = -1); run elevated",
              file=sys.stderr)
        return 4
    full_capacity = bench.probe.full_charged_capacity()

    if args.phase == "selftest":
        return run_selftest(bench)
    if args.external > 0:
        return run_external(bench, args.external, args.profile)

    duties = ({"ladder": tuple(int(d) for d in args.duties.split(","))}
              if args.duties else None)
    cfg = appctl.load_cfg()
    profile_names = resolve_profile_names(cfg)
    if not profile_names:
        print("[bench] installed config has no profiles to characterize",
              file=sys.stderr)
        return 5
    phases = (["ladder", "profiles", "validate"] if args.phase == "all"
              else [args.phase])
    powers = ["ac", "battery"] if args.power == "both" else [args.power]
    if "ladder" in phases:
        per_power = {p: ((duties or {}).get("ladder")
                         or (AC_DUTIES if p == "ac" else BATTERY_DUTIES))
                     for p in powers}
        ensure_ladder_profiles(cfg, per_power)
    trials = build_trials(phases, powers, duties, args.reps,
                          profile_names=profile_names)
    if args.loads:
        wanted = {s.strip() for s in args.loads.split(",")}
        trials = [t for t in trials if t.get("load") in wanted]

    if args.resume:
        done = prepare_resume(bench.out_path)
        trials = [t for t in trials if t["trial_id"] not in done]
        write_meta = not any(r.get("meta") for r in
                             (analyze.load_rows(bench.out_path)
                              if os.path.exists(bench.out_path) else []))
    else:
        done = set()
        write_meta = True
        if os.path.exists(bench.out_path):
            os.remove(bench.out_path)  # a fresh run owns the file

    print(f"[bench] {len(trials)} trials to run "
          f"({'full' if args.full else 'quick'}, "
          f"{'/'.join(phases)}, {'/'.join(powers)})", flush=True)

    original_active = cfg.get("active_profile_name", "Config 1")
    if write_meta:
        bench.log.append({"meta": True, "ts": time.time(),
                          "window": bench.window_s, "cap": args.cap,
                          "full": args.full,
                          "full_charged_capacity": full_capacity,
                          "original_active": original_active,
                          "argv": " ".join(sys.argv[1:])})

    curves: dict | None = None
    power_dead: dict[str, str] = {}
    battery_gate_done = False
    try:
        for trial in trials:
            if bench.abort_reason:
                break
            # Battery block gates: charge first, then one dead-per-power memo.
            if trial["power"] == "battery" and not battery_gate_done:
                battery_gate_done = True
                try:
                    appctl.await_charge(bench.probe, BATTERY_START_MIN_FRAC)
                except appctl.SkipPhase as exc:
                    power_dead["battery"] = f"charge gate: {exc}"
            if trial["power"] in power_dead:
                why = power_dead[trial["power"]]
                bench.log.append({"trial_id": trial["trial_id"],
                                  "skipped": why})
                continue
            # Derive + install Tuned profiles before the first validate trial.
            if trial["phase"] == "validate" and curves is None \
                    and not args.skip_derive:
                curves = bench.derive_and_install(args.cap, full_capacity)
                if curves is None:
                    curves = bench.load_existing_curves()
                    if curves:
                        ensure_tuned_profiles(curves)
                if curves is None:
                    bench.log.append({"trial_id": trial["trial_id"],
                                      "skipped": "no derived curves"})
                    continue
            status, detail = bench.run_trial(trial, bench.step_s[trial["phase"]],
                                             full_capacity)
            print(f"[bench] {trial['trial_id']}: {status} ({detail})", flush=True)
            if status in ("skip", "skip-power"):
                bench.log.append({"trial_id": trial["trial_id"],
                                  "skipped": detail, "phase": trial["phase"],
                                  "power": trial["power"], "ts": time.time()})
            if status == "skip-power":
                power_dead[trial["power"]] = detail
    except KeyboardInterrupt:
        print("[bench] interrupted; restoring", file=sys.stderr)
        bench.burners.stop()
        appctl.restore_active(original_active)
        return 130

    if bench.abort_reason:
        bench.abort_actions(original_active)
        return 2

    if "validate" in phases and curves is None and not args.skip_derive:
        curves = bench.derive_and_install(args.cap, full_capacity)
    rows = analyze.load_rows(bench.out_path)
    analyze.write_report(rows, curves or bench.load_existing_curves(),
                         bench.window_s, args.cap,
                         os.path.join(RESULTS, "report.md"), full_capacity)
    appctl.restore_active(original_active)
    try:
        remove_bench_owned_profiles()
    except Exception as exc:
        print(f"[bench] ladder profile cleanup failed: {exc}", file=sys.stderr)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
