# -*- coding: utf-8 -*-
"""Quiet idle check: split mode must hold the GPU fan at 0 while the dGPU is
cool/asleep — proven with EC-ONLY sampling (NVML disabled).

Why this exists: tools/bench/split_ab.py's probe calls nvidia-smi every 4th
sample (~12 s). Any NVML client pins the dGPU at P0 (see
core/wmi_interface._poll_nvml_if_allowed), so during the A/B run the GPU
never drops below its ~60 degC pinned-idle temp, the GPU curve legitimately
commands raw 57 (25 %), and the bench's idle criterion (rpm1 == rpm2 == 0)
is structurally unpassable. This script removes the pin: no nvidia-smi, no
burners. With the desktop quiet, the dGPU either cools below the curve's
zero knee (<= 55 degC on the shipped AC table) or powers down entirely
(EC reports 0 degC for both GPU sensors = asleep); in both states the split
selector commands gpu_duty_raw 0, and the fan must read 0 rpm.

Pass = the split-specific promise: dGPU asleep (EC 0/0) or cool, then
gpu_duty_raw == 0 and rpm1 == 0 for EVERY sample of the steady window.
The CPU channel is intentionally NOT gated: split leaves CPU-fan behavior
untouched (the A/B measured it identical at +/-0.0 degC), and desktop
activity legitimately spins it. CPU rows are recorded for reference.

Run ELEVATED, desktop quiet (no video, no HW-accel heavy apps):
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\split_idle_check.py
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

INTERVAL = 3.0
WAIT_TIMEOUT_S = 600.0     # window for the dGPU to cool <= 55 or fall asleep
SETTLE_S = 60.0            # grace for the EC/fan to spin down after the state flips
STEADY_S = 240.0           # proof window
RESULTS = os.path.join(HERE, "results")
ROWS_PATH = os.path.join(RESULTS, "split-idle-check.jsonl")
SUMMARY_PATH = os.path.join(RESULTS, "split-idle-check-summary.json")
GPU_COOL_C = 55.0          # shipped AC GPU curve's zero knee


def gpu_state(sample: dict) -> str:
    """'asleep' | 'cool' | 'awake-warm' from EC readings only."""
    g1, g2 = sample.get("gpu1"), sample.get("gpu2")
    if g1 == 0 and g2 == 0:
        return "asleep"          # EC reports 0/0 exactly when powered down
    if g1 is not None and g1 <= GPU_COOL_C:
        return "cool"
    return "awake-warm"


def main() -> int:
    os.makedirs(RESULTS, exist_ok=True)
    cfg = appctl.load_cfg()
    original = cfg.get("active_profile_name", "Config 1")
    appctl.ensure_app_running()
    cfg = appctl.load_cfg()
    appctl.edit_profile(cfg, original, split_fan_control=True)
    appctl.save_cfg_atomic(cfg)
    appctl.apply_profile(original, "auto")

    probe = ECProbe()
    probe.connect()
    # EC-only: never spawn nvidia-smi (it would pin the dGPU awake).
    probe._nvidia_path = None
    probe._nv_cache = (-1.0, -1.0)

    verdict = "FAIL"
    reason = ""
    phase = ""
    steady_ok = True
    try:
        # Phase 1: wait for sleep-or-cool.
        print(f"waiting up to {WAIT_TIMEOUT_S:.0f} s for dGPU sleep or "
              f"<= {GPU_COOL_C:.0f} degC ...", flush=True)
        deadline = time.time() + WAIT_TIMEOUT_S
        state = "awake-warm"
        while time.time() < deadline:
            sample = probe.sample_once()
            state = gpu_state(sample)
            print(f"  [wait] gpu1 {sample['gpu1']:>5} gpu2 {sample['gpu2']:>5} "
                  f"({state}) cpu {sample['cpu_t']:>5} rpm2 {sample['rpm2']:>5} "
                  f"gpu_raw {sample['gpu_duty_raw']:>5}", flush=True)
            if state in ("asleep", "cool"):
                break
            time.sleep(INTERVAL)
        if state not in ("asleep", "cool"):
            reason = (f"dGPU stayed awake-warm for {WAIT_TIMEOUT_S:.0f} s "
                      f"(external NVML client or active desktop?)")
        else:
            # Phase 2: settle, then the steady proof window.
            print(f"dGPU is {state}; settling {SETTLE_S:.0f} s, then "
                  f"{STEADY_S:.0f} s proof window", flush=True)
            time.sleep(SETTLE_S)
            started = time.time()
            first_violation = None
            with open(ROWS_PATH, "w", encoding="utf-8") as fh:
                while time.time() - started < STEADY_S:
                    mark = time.time()
                    sample = probe.sample_once()
                    sample["phase"] = phase = "steady"
                    sample["loop_t"] = time.time() - started
                    fh.write(json.dumps(sample) + "\n")
                    fh.flush()
                    ok = (sample["gpu_duty_raw"] == 0 and sample["rpm1"] == 0)
                    if not ok and first_violation is None:
                        first_violation = sample
                        print(f"  VIOLATION at t+{sample['loop_t']:.0f}s: "
                              f"gpu_raw {sample['gpu_duty_raw']} rpm1 {sample['rpm1']} "
                              f"gpu1 {sample['gpu1']} (cpu {sample['cpu_t']} "
                              f"cpu_raw {sample['cpu_duty_raw']} rpm2 {sample['rpm2']})",
                              flush=True)
                    time.sleep(max(0.0, INTERVAL - (time.time() - mark)))
            if first_violation is None:
                verdict = "PASS"
            else:
                reason = (f"nonzero fan command/rpm in steady window, first at "
                          f"t+{first_violation['loop_t']:.0f} s")
    finally:
        appctl.restore_active(original)

    summary = {"verdict": verdict, "reason": reason, "state": state,
               "gpu_cool_c": GPU_COOL_C, "settle_s": SETTLE_S, "steady_s": STEADY_S}
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"SPLIT-IDLE-CHECK-{verdict}" + (f": {reason}" if reason else ""))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
