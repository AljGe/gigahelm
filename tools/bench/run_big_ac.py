# -*- coding: utf-8 -*-
"""Unattended full AC bench run (must run elevated).

Started from the user's admin terminal. Holds ES_SYSTEM_REQUIRED for its own
lifetime so the machine cannot sleep/hibernate while the matrix runs - the
state dies with this process, so a crash or a closed terminal always
re-enables sleep (fail-safe default, no system settings touched).

Steps: admin check -> unit-suite gate -> deploy current build -> deprioritize
background apps (recorded, restored at the end) -> full AC bench matrix
(6 duties x 3 loads x 2 reps + profiles x 2 reps + validate, ~4 h) -> report.
Battery trials are excluded (need hands on the plug); add them later with
--power battery --resume against the same output file.

Progress lands in tools/bench/results/big-ac-status.txt (rewritten per step,
last bench line included) and big-ac-run.log (full output).
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "tools" / "bench" / "results"
LOG_PATH = RESULTS / "big-ac-run.log"
STATUS_PATH = RESULTS / "big-ac-status.txt"
OUT_NAME = "tools/bench/results/bench-ac-full-20261116.jsonl"
OUT_PATH = RESULTS / "bench-ac-full-20261116.jsonl"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
DEMOTE_NAMES = "firefox","ferdium","everything","chrome","msedge","code","slack","discord"

_log_fh = None
def log(message: str) -> None:
    global _log_fh
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    if _log_fh is None:
        _log_fh = open(LOG_PATH, "a", encoding="utf-8")
    _log_fh.write(line + "\n")
    _log_fh.flush()

def status(text: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(f"{datetime.now():%H:%M:%S} {text}\n", encoding="utf-8")

def run_step(name: str, cmd, check_pass: str = None):
    """Run cmd, stream output into the log; abort the run on failure."""
    log(f"step {name}: {' '.join(str(c) for c in cmd)}")
    status(f"running {name}")
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    output = []
    for line in proc.stdout:
        output.append(line)
        log(f"  {name}| {line.rstrip()}")
        if name == "bench":
            status(f"bench running: {line.strip()[:100]}")
    proc.wait()
    combined = "".join(output)
    ok = proc.returncode == 0 and (check_pass is None or check_pass in combined)
    if ok:
        log(f"step {name}: OK (rc={proc.returncode})")
        status(f"{name} OK")
        return True
    log(f"step {name}: FAILED (rc={proc.returncode}, "
        f"expect={'PASS' if check_pass else 'rc0'})")
    status(f"ABORT at {name} rc={proc.returncode}")
    return False

def demote_background_apps(results_dir: Path) -> Path | None:
    """Record + set BelowNormal on known background CPU eaters. Non-fatal."""
    ps_script = (
        "$names = 'firefox','ferdium','everything','chrome','msedge','code',"
        "'slack','discord'\n"
        "$p = Get-Process -Name $names -ErrorAction SilentlyContinue\n"
        "$p | ForEach-Object { [pscustomobject]@{Id=$_.Id;"
        "Name=$_.ProcessName;P=[int]$_.PriorityClass} } |"
        " ConvertTo-Json | Set-Content -Encoding utf8"
        f" '{results_dir / 'big-ac-priorities.json'}'\n"
        "$p | ForEach-Object { try { $_.PriorityClass = 'BelowNormal' } catch {} }\n"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                          capture_output=True, text=True)
    record = results_dir / "big-ac-priorities.json"
    log(f"priorities demoted (rc={proc.returncode}, "
        f"record={record.exists()})")
    return record if record.exists() else None

def restore_background_apps(record: Path | None) -> None:
    if record is None or not record.exists():
        return
    try:
        entries = json.loads(record.read_text(encoding="utf-8-sig"))
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"try {{ (Get-Process -Id {e['Id']} "
                            f"-ErrorAction Stop).PriorityClass = "
                            f"[System.Diagnostics.ProcessPriorityClass]::"
                            f"{e['P']} }} catch {{}}"],
                           capture_output=True)
        log("background priorities restored")
    except Exception as exc:
        log(f"priority restore failed: {exc!r}")

def main() -> int:
    t0 = time.time()
    log(f"big AC run starting; repo={REPO}")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        status("ABORT not-admin (harmless if launched unelevated)")
        log("not elevated - aborting (bench needs EC WMI access)")
        return 3
    status("admin ok, holding system awake")
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    log("sleep/hibernate blocked for this process lifetime")

    record = None
    try:
        if not run_step("unit-suite", [sys.executable, "-m", "unittest",
                                       "discover", "-s", "tests"]):
            return 1
        if not run_step("deploy", ["powershell", "-NoProfile",
                                   "-ExecutionPolicy", "Bypass", "-File",
                                   "tools/deploy_gfb.ps1"], check_pass="PASS"):
            return 1
        record = demote_background_apps(RESULTS)
        bench_cmd = [sys.executable, "-X", "utf8", "tools/bench/bench.py",
                     "--phase", "all", "--power", "ac", "--full", "--reps", "2",
                     "--out", OUT_NAME]
        if not run_step("bench", bench_cmd):
            return 1
        if not run_step("analyze", [sys.executable, "-X", "utf8",
                                    "tools/bench/analyze.py",
                                    "--in", str(OUT_PATH),
                                    "--out-dir", "tools/bench/results"]):
            return 1
        status(f"DONE in {(time.time()-t0)/3600:.1f} h - report.md written")
        log(f"all steps complete in {(time.time()-t0)/3600:.2f} h")
        return 0
    finally:
        restore_background_apps(record)
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        log("sleep/hibernate re-enabled (process exiting)")

if __name__ == "__main__":
    sys.exit(main())
