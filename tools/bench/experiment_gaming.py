# -*- coding: utf-8 -*-
"""Throwaway diagnostic: does the Gaming profile ramp under all-core load
when the EC is polled LIGHTLY (every 3 s) instead of the bench's 1 Hz?

Discriminates: (A) app mis-reads temp, (B) EC ignores high-duty writes under
load, (C) bench WMI contention starved the app during the bench run.

Applies Gaming, burns 16 threads for 240 s, samples every 3 s, restores the
previous active profile. Prints a compact timeline AND dumps full sample
rows (proc_perf, sample_ms, burner liveness) to a JSONL side file.
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appctl
from ec_probe import ECProbe
from loadgen import Burners, LOAD_N

DURATION = 240.0
INTERVAL = 3.0
ROWS_PATH = os.path.join(HERE, "results", "experiment-gaming-rows.jsonl")


def main() -> int:
    cfg = appctl.load_cfg()
    original = cfg.get("active_profile_name", "Config 1")
    appctl.apply_profile("Gaming", "auto")
    probe = ECProbe()
    probe.connect()
    burners = Burners()
    started = time.time()
    burners.start(LOAD_N["all"], DURATION)
    print("t+duty% rpm1 rpm2 cpu gpu1 nv perf samplems alive", flush=True)
    slow = 0
    try:
        end = time.time() + DURATION
        with open(ROWS_PATH, "w", encoding="utf-8") as rows_fh:
            while time.time() < end:
                mark = time.time()
                sample = probe.sample_once()
                elapsed = time.time() - mark
                if elapsed > 6.0:
                    slow += 1
                alive = sum(p.is_alive() for p in burners._procs)
                sample["perf_ms"] = elapsed * 1000.0
                sample["burners_alive"] = alive
                sample["loop_t"] = time.time() - started
                rows_fh.write(json.dumps(sample) + "\n")
                rows_fh.flush()
                print(f"{sample['ts'] % 100000:.0f} "
                      f"{sample['fixed_raw'] / 227 * 100:5.1f} "
                      f"{sample['rpm1']:5d} {sample['rpm2']:5d} "
                      f"{sample['cpu_t']:5.1f} {sample['gpu1']:5.1f} "
                      f"{sample['gpu_temp_nv']:5.1f} "
                      f"{sample['proc_perf']:5.0f} {elapsed:5.1f} "
                      f"{alive:2d}", flush=True)
                time.sleep(max(0.0, INTERVAL - (time.time() - mark)))
    finally:
        burners.stop()
        appctl.restore_active(original)
    print(f"EXPERIMENT-DONE slow_samples={slow}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
