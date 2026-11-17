# Gigahelm bench harness
| `split_ab.py` | A/B: mirrored vs split fan duty (`split_fan_control`) on the active profile; idle/dual/all × 240 s, interleaved arms; writes `results/split-ab-<arm>-<trial>.jsonl` + `split-ab-summary.json`; exit 0 = split control CPU-safe to default-on. NB: its nvidia-smi sampling pins the dGPU awake, so the idle trial cannot reach the 0-fan state — use `split_idle_check.py` for that proof |
| `split_idle_check.py` | EC-only (NVML disabled) quiet-idle proof: waits for dGPU sleep/≤55 °C, then 240 s steady window where gpu_duty_raw and rpm2 must read 0; exit 0 = the split 0-fan promise holds |

Empirical fan-curve characterization for the installed Gigahelm controller.
Everything here is standalone (imports nothing from the app package tree)
and expects elevation for EC (`root\WMI GB_WMIACPI_*`) access.

## Files

| File | Purpose |
|---|---|
| `ec_probe.py` | Sampler library: EC temps/RPMs (decoded), duty readbacks, nvidia-smi, battery, processor performance. CLI: `--once`, `--watch N [--interval S]` |
| `appctl.py` | Config edit (atomic + one backup), profile apply via `--minimized` reload + mode/heartbeat assertion, power/charge gates, restore |
| `loadgen.py` | CPU burner processes (`idle`=0, `dual`=2, `all`=N threads) |
| `bench.py` | Orchestrator: `--phase selftest\|ladder\|profiles\|validate\|all`, `--power ac\|battery\|both`, `--full/--quick`, `--resume`, `--reps`, `--duties`, `--out`, `--external` (manual GPU sampling) |
| `analyze.py` | Steady-window stats, ladder inversion, curve synthesis, report. CLI: `--in` (multiple files OK) `--out-dir` |
| `check_probe.py` | Sanity gate: exits 0 iff a probe sample is plausible |
| `run_full.ps1` | Full AC+battery matrix, one UAC (plan Phase B) |
| `run_battery.cmd` | Battery phase only, interactive prompts on console |
| `bench_chain.cmd` | Elevated chain: probe -> selftest -> bench |
| `deploy_regress.cmd` | Deploy `dist\Gigahelm` + run the Gaming regression |
| `experiment_gaming.py` | Regression: Gaming profile under 16-core load, 3 s sampling |

## Usage

```powershell
# full run (one UAC, ~4-5 h, battery phase needs you for plug/unplug)
powershell -ExecutionPolicy Bypass -File tools\bench\run_full.ps1

# battery phase only (~45 min, prompts on console)
# then: python tools\bench\bench.py --phase all --power battery --quick --resume --reps 1 --duties 40,70,100 --out results/bench-battery.jsonl

# final report from AC + battery rows
python tools\bench\analyze.py --in tools\bench\results\bench-small.jsonl tools\bench\results\bench-battery.jsonl --out-dir tools\bench\results
```

## Safety

- Aborts on CPU >= 95 C for 8 consecutive samples, fans-commanded-but-0-rpm
  for 6, or two failed profile applies: emergency 100 % fans, load killed,
  previous profile restored, `results/ABORT.txt`, exit 2.
- Battery trials stop below 40 % capacity (60 % for profile trials); battery
  phase gates on >= 90 % charge first.
- Config edits are atomic; the first write of a run keeps a
  `control_config.json.bench-bak-<ts>` backup. The pre-run active profile is
  restored at the end.

## Known machine facts (Aorus 15P, i7-11800H, RTX 3080 Laptop)

- Sustained CPU load pins ~89-91 C at any duty on AC; curves control noise,
  not temperature. The EC thermal guard floors (80/85/90 C -> 40/60/80 %)
  and `core/thermal_guard.py` own the hot regime.
- `nvidia-smi` costs ~1 s per call: sampled every 4th poll.
- `BatteryStatus.DischargeRate` emits occasional implausible spikes
  (excluded, <= 250 W cap, median used for endurance).
