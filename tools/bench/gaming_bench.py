# -*- coding: utf-8 -*-
"""Gaming-shaped load experiment: Unigine Heaven benchmark (GPU) + CPU burner
threads, under the user's Tuned AC profile, sampled at 2 s (EC + NVML).

Answers: does the installed Tuned AC profile (GPU table inherited from Silent)
hold the shared fan duty high enough under a real 3D workload? Rows go to
results/gaming-rows.jsonl; a compact timeline goes to stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appctl
from ec_probe import ECProbe
from loadgen import Burners

HEAVEN_CANDIDATES = [
    r"C:\Program Files (x86)\Unigine\Heaven Benchmark 4.0\bin\heaven.exe",
    r"C:\Program Files\Unigine\Heaven Benchmark 4.0\bin\heaven.exe",
]
DURATION_MAX = 480.0     # launcher + xtreme loop ~4 min + margin
INTERVAL = 2.0
CPU_THREADS = 6
ROWS_PATH = os.path.join(HERE, "results", "gaming-rows.jsonl")


def find_heaven() -> str | None:
    for path in HEAVEN_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def main() -> int:
    heaven = find_heaven()
    if heaven is None:
        print("HEAVEN-MISSING", flush=True)
        return 9
    cfg = appctl.load_cfg()
    original = cfg.get("active_profile_name", "Config 1")
    appctl.apply_profile("Tuned AC", "auto")
    probe = ECProbe()
    probe.connect()
    burners = Burners()
    # Launch the SUPPORTED path: the Heaven launcher UI. The user clicks
    # "Start benchmark" once; direct engine launches fatal-error on a fresh
    # install (bin/unigine.cfg is only created by a first launcher run).
    launcher = os.path.join(os.path.dirname(heaven), "browser_x86.exe")
    proc = subprocess.Popen([launcher, "-config", "../data/launcher/launcher.xml"],
                            cwd=os.path.dirname(launcher))
    print("[gaming] launcher window open - click Start Benchmark", flush=True)
    print("t gpu_nv gpu1 gpu2 cpu_t duty% rpm1 rpm2 gpu_util ms", flush=True)
    started = time.time()
    burners_started = False
    try:
        while time.time() - started < DURATION_MAX:
            mark = time.time()
            sample = probe.sample_once()
            sample["loop_t"] = time.time() - started
            sample["heaven_alive"] = proc.poll() is None
            # Align CPU burners with the actual benchmark start (GPU spin-up).
            if not burners_started and (sample.get("gpu_util_nv", 0) > 30
                                        or sample.get("gpu1", 0) >= 65):
                burners.start(CPU_THREADS, DURATION_MAX)
                burners_started = True
                print("[gaming] GPU load detected - CPU burners on",
                      flush=True)
            with open(ROWS_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample) + "\n")
            print(f"{sample['loop_t']:5.0f} {sample['gpu_temp_nv']:5.1f} "
                  f"{sample['gpu1']:5.1f} {sample['gpu2']:5.1f} "
                  f"{sample['cpu_t']:5.1f} "
                  f"{sample['fixed_raw'] / 227 * 100:5.1f} "
                  f"{sample['rpm1']:5d} {sample['rpm2']:5d} "
                  f"{sample['gpu_util_nv']:5.1f} "
                  f"{(time.time() - mark) * 1000:5.0f}", flush=True)
            time.sleep(max(0.0, INTERVAL - (time.time() - mark)))
    finally:
        if proc.poll() is None:
            proc.kill()
        burners.stop()
        appctl.restore_active(original)
    print("GAMING-DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
