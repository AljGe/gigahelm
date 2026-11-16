# Roadmap — shipped-but-unfinished and not-yet-built control work

Control-loop optimizations that were analyzed against SOTA fan controllers
(FanControl, thinkfan, nbfc-linux, G-Helper) but deliberately **not shipped**
in the quiet-control-loop change. Each entry: why it matters, design sketch,
effort. Ordered by expected value.

## 1. NVML GPU power feed-forward — **shipped**

- **Why:** temperature lags load by 10–30 s (heatsink time constant). NVIDIA's
  driver reports the real thing: `power.draw` and utilization via NVML. The
  dGPU is the dominant noise source under gaming load — power-based
  pre-spin reacts to load directly instead of waiting for heat to arrive.
- **Done:** optional `pynvml` dependency (graceful no-op when driver/NVML is
  absent); NVML lives on the WMI worker thread with vendor-filtered device
  enumeration; `_get_core_sensors` carries `gpu_power_w` (+utilization);
  `_on_cycle_temps` lifts the GPU curve input by
  `k * max(0, P_gpu - P_idle)` (30 W idle floor above video playback,
  10 °C cap, per-profile toggle `gpu_power_feedforward`, default on).
  NOT done here: NVML-based dGPU-asleep detection (EC-returns-0 heuristic
  kept as primary — needs live D3cold validation) and a utilization % UI.
- **Risks:** Optimus hybrid enumeration (pick the right NVML device);
  NVML init must live in the worker thread, not the GUI thread.
- **Effort:** M. Per-profile opt-in toggle recommended.

## 2. Closed-loop target-temperature mode

- **Why:** curves are open-loop mappings (temp → rpm). Laptop firmware and
  the big vendor tools instead *regulate*: hold a target temperature at the
  minimum rpm that achieves it. Same comfort across workloads, less noise
  at light load, no curve tuning burden on users.
- **How:** per-profile target temp per sensor. P-only controller on the
  conditioned temperature: `percent = clamp(base + k_p * (T - T_target))`
  with the existing hysteresis on the output and all raw-temperature safety
  overrides untouched. UI: "Regulate to X °C" mode beside Auto/Custom.
- **Risks:** stability tuning (P-only, no integral, clamp everywhere);
  interacts with the spin floor (T_target unreachable below 25 %).
- **Effort:** M-L.

## 3. Floor-relative curve remap (sub-floor points)

- **Why:** EC duty below the model spin floor (25 % on YD/XD) is clamped by
  `percent_to_raw`, so curve points at 1–24 % are dead range (the default
  `[60, 15]` point effectively sits on the floor plateau). The editor now
  *shows* the floor line; the points themselves still waste curve range.
- **How:** opt-in interpretation where curve percent maps onto the usable
  band: `[0..100] -> [floor..100]` (15 % of usable ≈ 36 % absolute).
  Behavior change → needs a per-profile setting and a migration note.
- **Effort:** S.

## 4. Idle fan-off: validate and advertise

- **Why:** the EC accepts raw duty 0 (fans fully off — the set is
  `{0} ∪ 57..227`), and the default curve already targets 0 below 50 °C,
  but raw-0 = full-stop behavior is only inferred from BIOS Silent policy,
  not verified as a software write on this chassis.
- **How:** live test — write 0 via a probe, watch RPM readback and temps for
  10 min; verify the spin-up path recovers (kick). If solid, document
  "idle-off" as a feature and consider a Silence preset curve
  (`[55, 0], [65, 25], [75, 40], [82, 60], [88, 85], [95, 100]`).
- **Effort:** S (one elevated probe session) + docs.

## 5. Tuned per-profile default curves

- **Why:** one default curve set serves all three profiles today; presets
  are the cheapest noise/thermals win for new users.
- **How:** factory defaults per profile — Silent (idle-off + knee at 70 °C),
  Balanced (current defaults), Performance (curve shifted −5 °C, cap 100 %).
  First-run values only; never overwrite user edits.
- **Effort:** S.

## 6. Deploy race: poll the mutex after Start-Task — **shipped**

- ~~the deploy script starts the scheduled task while the previous instance
  may still be dying and holding the global mutex — the new instance then
  exits silently and the deploy *looks* successful.~~
- **Done:** `tools/deploy_gfb.ps1` polls the single-instance mutex for 20 s
  after `Start-ScheduledTask` and retries the task start once before failing
  the deploy (the EC probe alone fails late and without recovery).

## 7. RPM byte-swap heuristic documentation

- **Why (review minor):** the RPM reader's false-zero/byte-swap handling
  (`0x0109 → 0x0901` → 2470, garbage → −1) is correct and test-covered but
  undocumented — the heuristic looks like a bug to the next reader.
- **How:** comment block + short section in the EC notes; no behavior change.
- **Effort:** XS.

## Housekeeping (non-code, user decisions pending)

- Flip the GitHub repo visibility to public when ready.
- Retire the obsolete `Temp\gfb-center` working clone (runtime state is
  duplicated in `C:\Tools\GFBC`).
- Delete merged remote branches from the repo UI (local ones are cleaned).
