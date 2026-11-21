# -*- coding: utf-8 -*-
"""A/B bench: mirrored vs split fan duty on the ACTIVE profile.

Arms toggle the per-profile ``split_fan_control`` key on the active profile
(mirrored = one duty to both EC channels = pre-split behavior; split = each
channel follows its own die). Trials idle/dual/all run 240 s each with 3 s
EC sampling, arms interleaved within each trial pair to cancel ambient drift.
Shared-fin-stack question this answers: does leaving the GPU fan at the GPU
curve's duty (0 while cool/asleep) raise CPU temps in curve-bound regimes?

Rows -> results/split-ab-<arm>-<trial>.jsonl; steady-window medians and
verdicts -> results/split-ab-summary.json. Exit 0 only if ALL pass:
- idle: both arms settle to rpm1 == rpm2 == 0 (sanity),
- dual: cpu_t median delta (split - mirrored) <= +3.0 degC (the
  discriminating curve-bound trial) AND the EC honors zero commands on the
  GPU channel: in every steady stretch of >= 2 consecutive samples with
  gpu_duty_raw == 0, rpm1 must read 0 from the 2nd sample on (1st sample
  may still show spin-down rpm);
- all: cpu_t medians within +/-2.0 degC AND rpm2 medians within +/-10 %
  (ceiling-bound regime: CPU pins ~89-91 degC on AC).

NB fan-channel pairing, bench-proven 2026-11-17: SET_GPU_FAN_DUTY drives
the fan reporting rpm1, SET_CUSTOM_FAN_SPEED drives rpm2. So rpm2 is the
CPU fan and rpm1 is the GPU fan in the criteria above; the GPU fan will
legitimately differ between arms (that is the feature). NB also: this
probe's nvidia-smi sampling pins the dGPU awake (~60 degC), so idle
rarely reaches the 0-fan state - the dedicated split_idle_check.py
proves that state EC-only.

Run ELEVATED from the repo root:
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\split_ab.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import acoustic_analysis
import appctl
import floor_sweep
from ec_probe import ECProbe
from loadgen import Burners, LOAD_N

DURATION = 240.0
INTERVAL = 3.0
STEADY_FROM_S = 120.0  # last half of each trial is the steady window
RESULTS = os.path.join(HERE, "results")
ARMS = (("mirrored", False), ("split", True))
TRIALS = (("idle", 0), ("dual", 2), ("all", LOAD_N["all"]))
METRICS = ("cpu_t", "gpu_t", "rpm1", "rpm2", "fixed_raw")
#: Mic capture rides on the dual trials: that is the regime where the
#: mirrored arm drives both fans at the same park duty (twin blade-pass)
#: while the split arm runs the GPU fan off and the CPU fan alone.  Idle
#: fans sit at 0 % in both arms, so there is nothing to hear there.
MIC_AT_S = 45.0        # settled park, well before the steady window
MIC_RECORD_S = floor_sweep.RECORD_S
MIC_CAPTURE_RATE = floor_sweep.CAPTURE_RATE


def rows_path(arm: str, trial: str) -> str:
    return os.path.join(RESULTS, f"split-ab-{arm}-{trial}.jsonl")


def record_wav(sd, path: str, seconds: float) -> None:
    """Capture mono 16-bit PCM through the laptop mic (16 kHz)."""
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(MIC_CAPTURE_RATE)
        stream = sd.RawInputStream(samplerate=MIC_CAPTURE_RATE, channels=1,
                                   dtype="int16", blocksize=MIC_CAPTURE_RATE)
        stream.start()
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                data, _overflowed = stream.read(MIC_CAPTURE_RATE)
                wav.writeframes(bytes(data))
        finally:
            stream.stop()
            stream.close()


def run_trial(probe: ECProbe, burners: Burners, arm: str, trial: str, n: int,
              mic: tuple | None = None) -> None:
    burners.start(n, DURATION)
    started = time.time()
    slow = 0
    mic_done = False
    try:
        with open(rows_path(arm, trial), "w", encoding="utf-8") as fh:
            while time.time() - started < DURATION:
                mark = time.time()
                sample = probe.sample_once()
                elapsed = time.time() - mark
                if elapsed > 6.0:
                    slow += 1
                sample["arm"] = arm
                sample["trial"] = trial
                sample["loop_t"] = time.time() - started
                fh.write(json.dumps(sample) + "\n")
                fh.flush()
                print(f"  [{arm}/{trial}] t+{sample['loop_t']:5.0f}s "
                      f"cpu {sample['cpu_t']:5.1f} gpu {sample['gpu1']:5.1f} "
                      f"rpm1 {sample['rpm1']:5d} rpm2 {sample['rpm2']:5d} "
                      f"raw {sample['fixed_raw']:5.0f}", flush=True)
                if mic is not None and not mic_done and sample["loop_t"] >= MIC_AT_S:
                    mic_done = True
                    sd_mod, wav_path = mic
                    print(f"  [{arm}/{trial}] mic recording "
                          f"{MIC_RECORD_S:.0f} s -> {os.path.basename(wav_path)}",
                          flush=True)
                    record_wav(sd_mod, wav_path, MIC_RECORD_S)
                time.sleep(max(0.0, INTERVAL - (time.time() - mark)))
    finally:
        burners.stop()
    if slow:
        print(f"  [{arm}/{trial}] {slow} slow samples (>6 s)", flush=True)


def steady_medians(arm: str, trial: str) -> dict:
    values: dict[str, list] = {m: [] for m in METRICS}
    with open(rows_path(arm, trial), "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["loop_t"] < STEADY_FROM_S:
                continue
            values["cpu_t"].append(row["cpu_t"])
            values["gpu_t"].append(row["gpu1"])
            values["rpm1"].append(row["rpm1"])
            values["rpm2"].append(row["rpm2"])
            values["fixed_raw"].append(row["fixed_raw"])
    return {m: statistics.median(v) for m, v in values.items() if v}


def print_pair(trial: str, mirrored: dict, split: dict) -> None:
    print(f"== {trial} == (steady window medians, last {STEADY_FROM_S:.0f} s)")
    print(f"{'metric':<10}{'mirrored':>10}{'split':>10}{'delta':>10}")
    for m in METRICS:
        a, b = mirrored.get(m), split.get(m)
        if a is None or b is None:
            print(f"{m:<10}{'n/a':>10}{'n/a':>10}")
            continue
        print(f"{m:<10}{a:>10.1f}{b:>10.1f}{b - a:>10.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mic", action="store_true",
                        help="record the dual-trial park acoustics through "
                             "the laptop mic and rank the arms "
                             "(tonality/beat, acoustic_analysis)")
    args = parser.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    mic_sd = floor_sweep.setup_recorder() if args.mic else None
    cfg = appctl.load_cfg()
    original = cfg.get("active_profile_name", "Config 1")
    original_split = bool(cfg.get("profiles", {}).get(original, {})
                          .get("split_fan_control", True))
    appctl.ensure_app_running()
    appctl.apply_profile(original, "auto")

    probe = ECProbe()
    probe.connect()
    burners = Burners()
    summary: dict = {"active_profile": original, "duration_s": DURATION,
                     "steady_from_s": STEADY_FROM_S, "trials": {}, "notes": []}
    all_pass = True
    try:
        for trial, n in TRIALS:
            pair: dict = {}
            for arm, flag in ARMS:
                cfg = appctl.load_cfg()
                appctl.edit_profile(cfg, original, split_fan_control=flag)
                appctl.save_cfg_atomic(cfg)
                appctl.apply_profile(original, "auto")
                cooled = appctl.wait_cool(probe, 60.0)
                if not cooled:
                    print(f"  [{arm}/{trial}] wait_cool timed out; "
                          f"starting trial anyway", flush=True)
                mic = None
                if mic_sd is not None and trial == "dual":
                    mic = (mic_sd, os.path.join(RESULTS,
                                                f"split-ab-{arm}-{trial}.wav"))
                run_trial(probe, burners, arm, trial, n, mic=mic)
                pair[arm] = steady_medians(arm, trial)
            mirrored, split = pair["mirrored"], pair["split"]
            print_pair(trial, mirrored, split)
            if mic_sd is not None and trial == "dual":
                metrics: dict = {}
                for arm_name in ("mirrored", "split"):
                    wav_path = os.path.join(RESULTS,
                                            f"split-ab-{arm_name}-{trial}.wav")
                    try:
                        metrics[arm_name] = acoustic_analysis.analyze_recording(
                            wav_path)
                    except Exception as exc:
                        summary["notes"].append(
                            f"acoustic {arm_name}: {exc!r}")
                if len(metrics) == 2:
                    ranking = acoustic_analysis.rank_steps(metrics)
                    for label, m in ranking:
                        print(f"  acoustic {label}: band {m['band_db']:.1f} dB, "
                              f"tonality {m['tonality_db']:.1f} dB, "
                              f"beat {m['beat_hz'] or '-'} Hz, "
                              f"twin split {m['split_hz'] or '-'} Hz",
                              flush=True)
                    print(f"  acoustic quietest: {ranking[0][0]}", flush=True)
                    summary["acoustic"] = {"trial": trial, "metrics": metrics,
                                           "quietest": ranking[0][0]}

            verdict = "FAIL"
            if trial == "idle":
                ok = (mirrored["rpm1"] == 0 and mirrored["rpm2"] == 0
                      and split["rpm1"] == 0 and split["rpm2"] == 0)
                if split["rpm2"] > 0 or mirrored["rpm2"] > 0:
                    summary["notes"].append(
                        "idle rpm2 > 0 while commanded 0: EC refuses the zero "
                        "duty on the GPU channel (min-spin floor)")
                verdict = "PASS" if ok else "FAIL"
            elif trial == "dual":
                delta_cpu = split["cpu_t"] - mirrored["cpu_t"]
                # EC honors zero GPU commands: in every steady stretch of >= 2
                # consecutive gpu_duty_raw == 0 samples, rpm1 (the GPU fan)
                # must read 0 from the 2nd sample on - the 1st may still show
                # spin-down rpm. Refusal would hold rpm high indefinitely.
                rows_split = [json.loads(line) for line in open(
                    rows_path("split", trial), encoding="utf-8")]
                steady = [r for r in rows_split if r["loop_t"] >= STEADY_FROM_S]
                ec_ok, refusal = True, None
                run = 0
                for r in steady + [{"gpu_duty_raw": 57.0, "rpm1": 0}]:  # sentinel closes a trailing run
                    if r["gpu_duty_raw"] == 0:
                        run += 1
                        if run >= 2 and r["rpm1"] != 0:
                            ec_ok, refusal = False, r
                    else:
                        run = 0
                ok = delta_cpu <= 3.0 and ec_ok
                if refusal is not None:
                    summary["notes"].append(
                        "dual: GPU fan held rpm while commanded 0 "
                        f"(t+{refusal['loop_t']:.0f} s) - EC refusal suspected; "
                        "fallback = floor the awake-GPU channel at 25 % and rerun dual")
                verdict = "PASS" if ok else "FAIL"
                print(f"  dual cpu_t delta (split - mirrored): "
                      f"{delta_cpu:+.1f} degC (limit +3.0), EC zero-duty "
                      f"compliance {'ok' if ec_ok else 'REFUSED'}")
            elif trial == "all":
                delta_cpu = split["cpu_t"] - mirrored["cpu_t"]
                rpm2_rel = (abs(split["rpm2"] - mirrored["rpm2"])
                            / max(1.0, mirrored["rpm2"]))
                ok = abs(delta_cpu) <= 2.0 and rpm2_rel <= 0.10
                verdict = "PASS" if ok else "FAIL"
                print(f"  all cpu_t delta {delta_cpu:+.1f} degC (limit +/-2.0), "
                      f"CPU-fan rpm2 rel {rpm2_rel:.1%} (limit 10%)")
            print(f"  verdict: {verdict}\n", flush=True)
            all_pass = all_pass and verdict == "PASS"
            summary["trials"][trial] = {
                "mirrored": mirrored, "split": split, "verdict": verdict}
    finally:
        burners.stop()
        # restore_active only re-activates the profile; put the A/B'd flag
        # back first so the user's profile leaves the run exactly as it was.
        try:
            cfg = appctl.load_cfg()
            if original in cfg.get("profiles", {}):
                appctl.edit_profile(cfg, original,
                                    split_fan_control=original_split)
                appctl.save_cfg_atomic(cfg)
        except Exception as exc:
            print(f"split flag restore failed: {exc!r}", file=sys.stderr)
        appctl.restore_active(original)

    summary["pass"] = all_pass
    out = os.path.join(RESULTS, "split-ab-summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"summary -> {out}")
    print(f"SPLIT-AB-{'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
