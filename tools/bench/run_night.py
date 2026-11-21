# -*- coding: utf-8 -*-
"""Unattended overnight measurement run: the whole audit follow-up.

Composes the 2026-11-18 audit's open questions into one elevated run
(reuses run_big_ac's scaffolding: admin gate, sleep block, unit-suite
gate, deploy, background demotion, shared log/status files):

  matrix   bench.py AC matrix (ladder x loads x reps + profiles + validate,
           reps 2) - the post-fix profile re-run TODO.md asks for (~3 h)
  split-ab split_ab.py --mic - mirrored vs split on the ACTIVE profile with
           30 s mic captures of the dual-trial park, tonality/beat ranked
           (~50-60 min) - decides the audit's "Silent runs without the
           noise shaping" config question
  flap     flap_watch.py - burst-modulated all-core load at a fixed 40 %
           custom duty; quantifies guard-tier flap cadence and the
           GetFixedFanSpeed readback sag under load (~15 min)
  analyze  analyze.py on the matrix rows + a night-report-<date>.md that
           pulls the split-ab verdicts and the flap summary together

Battery and real-game GPU blocks stay out (both need hands on the machine).

Run ELEVATED (one paste, then walk away):
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\run_night.py
Progress: results/big-ac-status.txt (per-step), results/big-ac-run.log.
"""
import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from run_big_ac import (
    REPO,
    RESULTS,
    demote_background_apps,
    log,
    restore_background_apps,
    run_step,
    status,
)

STAMP = datetime.now().strftime("%Y%m%d")
MATRIX_OUT = f"tools/bench/results/bench-night-{STAMP}.jsonl"
MATRIX_OUT_PATH = RESULTS / f"bench-night-{STAMP}.jsonl"


def write_night_report() -> Path | None:
    """Best-effort pull of the three blocks' key results into one file."""
    lines = [f"# Night run {STAMP}", ""]
    split_path = RESULTS / "split-ab-summary.json"
    if split_path.exists():
        try:
            data = json.loads(split_path.read_text(encoding="utf-8"))
            lines += [f"## Split A/B (active profile: {data.get('active_profile')})",
                      f"pass: {data.get('pass')}"]
            for trial, t in data.get("trials", {}).items():
                m = t.get("mirrored", {})
                s = t.get("split", {})
                lines.append(
                    f"- {trial}: verdict {t.get('verdict')}, "
                    f"cpu_t {m.get('cpu_t')} -> {s.get('cpu_t')} degC, "
                    f"rpm1 {m.get('rpm1')} -> {s.get('rpm1')}, "
                    f"rpm2 {m.get('rpm2')} -> {s.get('rpm2')}")
            ac = data.get("acoustic")
            if ac:
                lines.append(f"- acoustic ({ac.get('trial')}): quietest arm "
                             f"= {ac.get('quietest')} (beat/tonality-ranked)")
            for note in data.get("notes", []):
                lines.append(f"- note: {note}")
            lines.append("")
        except Exception as exc:
            lines += [f"## Split A/B", f"(summary unreadable: {exc!r})", ""]
    flap_files = sorted(RESULTS.glob("flap-*.json"))
    if flap_files:
        try:
            data = json.loads(flap_files[-1].read_text(encoding="utf-8"))
            lines += ["## Guard flap / readback sag",
                      f"- cpu guard events: {data.get('guard_events_cpu')}, "
                      f"tier transitions: {data.get('tier_transitions')}, "
                      f"force gap median: {data.get('force_gap_s_median')} s",
                      f"- floor timeline: {data.get('floor_timeline_cpu')}",
                      f"- readback ON-hot: {data.get('on_hot_readback')}",
                      f"- readback OFF: {data.get('off_readback')}",
                      f"- cpu_t range: {data.get('cpu_t_min')} - "
                      f"{data.get('cpu_t_max')} degC", ""]
        except Exception as exc:
            lines += ["## Guard flap / readback sag",
                      f"(summary unreadable: {exc!r})", ""]
    if (RESULTS / "report.md").exists():
        lines += ["## AC matrix", "report: tools/bench/results/report.md",
                  f"rows: {MATRIX_OUT}", ""]
    out = RESULTS / f"night-report-{STAMP}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    t0 = time.time()
    log(f"night run starting; repo={REPO}")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        status("ABORT not-admin (harmless if launched unelevated)")
        log("not elevated - aborting (bench needs EC WMI access)")
        return 3
    status("admin ok, holding system awake")
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    log("sleep/hibernate blocked for this process lifetime")

    record = None
    report = None
    try:
        if not run_step("unit-suite", [sys.executable, "-m", "unittest",
                                       "discover", "-s", "tests"]):
            return 1
        if not run_step("deploy", ["powershell", "-NoProfile",
                                   "-ExecutionPolicy", "Bypass", "-File",
                                   "tools/deploy_gfb.ps1"], check_pass="PASS"):
            return 1
        record = demote_background_apps(RESULTS)
        if not run_step("bench", [sys.executable, "-X", "utf8",
                                  "tools/bench/bench.py",
                                  "--phase", "all", "--power", "ac",
                                  "--full", "--reps", "2", "--resume",
                                  "--out", MATRIX_OUT]):
            return 1
        if not run_step("split-ab", [sys.executable, "-X", "utf8",
                                     "tools/bench/split_ab.py", "--mic"]):
            log("split-ab failed; continuing with flap block")
        if not run_step("flap", [sys.executable, "-X", "utf8",
                                 "tools/bench/flap_watch.py"]):
            log("flap block failed; continuing with analyze")
        run_step("analyze", [sys.executable, "-X", "utf8",
                             "tools/bench/analyze.py",
                             "--in", str(MATRIX_OUT_PATH),
                             "--out-dir", "tools/bench/results"])
        report = write_night_report()
        hours = (time.time() - t0) / 3600.0
        status(f"DONE in {hours:.1f} h"
               + (f" - {report.name} written" if report else ""))
        log(f"night run complete in {hours:.2f} h, report={report}")
        return 0
    finally:
        restore_background_apps(record)
        kernel32.SetThreadExecutionState(0x80000000)
        log("sleep/hibernate re-enabled (process exiting)")


if __name__ == "__main__":
    sys.exit(main())
