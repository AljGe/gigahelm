# TODO / Roadmap

## SSD / NVMe health monitoring

Integrate drive-health monitoring into Gigahelm (validated standalone at `C:\Tools\SSDWatch\`,
kept running there until this lands; see `ssd_watch.ps1` / `ssd_watch_task.xml`).

- **Read path:** poll `Get-PhysicalDisk` `HealthStatus` (unelevated OK, Windows storage stack —
  same verdict Windows itself uses: NVMe critical-warning bits, spare-block reserve, media errors).
  The deployed app runs `RunLevel HighestAvailable`, so it can additionally read
  `Get-StorageReliabilityCounter` (`Wear` = NVMe "Percentage Used", `Temperature`, error counts)
  that an unelevated process gets denied — better than the standalone version.
- **Display:** dashboard stat card (battery-card style footer pattern): per-drive
  `Healthy / Warning / Unhealthy`, wear %, temperature.
- **Warning path:** toast + WARN log entry when any drive leaves `Healthy` (WinRT
  `ToastNotificationManager`, PowerShell `AppId` pattern proven in the standalone script);
  no toast on the healthy path.
- **Cadence:** slow timer (daily + at start is enough; health transitions are gradual).
  Do NOT run in the EC control cycle — unrelated subsystem, different latency budget.
- **Limitations to document in-UI:** threshold-based, not trend-based; sudden controller/
  firmware death gives no SMART warning on any tool (backups remain the real protection).
- **Reference baseline (2026-11-15, elevated):** ESR 1 TB — 0 % wear, 35 °C; KIOXIA G3 2 TB —
  0 % wear, 29 °C. Power-on-hours / error counters not exposed by these controllers via
  Windows; don't design around them.

## Bench follow-ups / optimizations (2026-11-15 session)

Outcomes of the profile-benchmark session (`tools/bench/`); ordered by value.
Context: controller stall root-caused (app launched BELOW_NORMAL → WMI worker
starves under all-core load → EC latches stale duty) and fixed+deployed
(`SetPriorityClass(ABOVE_NORMAL)` in `main.py`, worker/guard threads above-normal,
new `core/thermal_guard.py` fail-safe); regression-proven via
`tools/bench/experiment_gaming.py`. Hardware truth: sustained load pins ~89–91 °C
at any duty on AC; 100 % buys ~1 °C over 40 % — no software curve beats physics.
Endurance: all-core median discharge 99.1 W → ~54 min from 90 % on battery.

1. **GPU-side characterization.** Tuned GPU tables are still Silent's verbatim
   guess. In games the shared fan duty = max(CPU, GPU target), so the GPU side
   decides gaming noise. Run a real game ~10 min with
   `bench.py --external 600 --interval 3` (sampling only) and decide whether the
   GPU table needs its own tuning. ~10 min of gameplay.
2. **Re-run AC profile comparison post-fix.** The shipped comparison table is
   tainted by the stall bug (Config1/Gaming/Silent showed 50/25/65 % under
   all-core load — collapsed states, not real curves). Re-run
   `bench.py --phase profiles --power ac --quick --resume --reps 1 --out
   results/bench-ac2.jsonl`, re-merge, regenerate `report.md`. ~25 min, one UAC.
3. **Align app RPM decode with the harness.** App `_validate_rpm` decodes a
   stalled-fan word (e.g. raw 12) as 3072 RPM → stall detection half-blind.
   Harness `ec_probe.decode_rpm` has the correct raw-plausible-first rule
   (raw 0..8000 trusted, else byte-swap, ≤50 → 0, else −1). Port + regression
   test + rebuild. ~10 lines; the one remaining safety blind spot.
4. **Bounded COM calls in the WMI worker.** `_execute_wmi_method` has no
   timeout; a truly hung EC call blocks the worker until the 8 s cycle-watchdog
   rebuild (churns under sustained load). Priority fix makes this rare, but a
   driver-level hang is still unbounded. Call-runner thread + abandon/rebuild
   pattern. Medium effort.
5. **BIOS-watchdog retry hardening.** One revert attempt failed silently
   (19:47:42 log line with no success/error follow-up); self-healed 2 min later
   but a retry/backoff inside a single run would close the gap. Small.
6. **Housekeeping.** `--merge` flag for the AC+battery report (currently a manual
   concat of `bench-small.jsonl` + `bench-battery.jsonl`); prune stale
   selftest rows from default `bench.jsonl`; optional short README in
   `tools/bench/`.

### Not worth optimizing (measured)

- 90 °C ceiling in software: no curve beats physics on AC; perf counter showed
  no throttling below turbo (min 120–160 %), so nothing is lost at 90 °C.
  Lower temps = repaste/airflow, not code.
- Charge-limit code: custom 80 % (Tuned Battery) is already longevity-optimal;
  Gaming/Silent sit at 83/84 % — aligning those down is a preference, not code.
- Battery curve refinement: already validated on battery (both trials PASS,
  82.7/78.1 °C vs 87.5 limit) with real battery data.

### Known EC quirks (documented, non-blocking)

- `GetFixedFanSpeed` readback can read below command under load (saw 148 vs
  commanded 227) — top-end ladder data has this uncertainty.
- EC BIOS-auto itself keeps fans quiet at 90 °C (25 % or 0 rpm) — with the new
  process priority + in-app guard this can no longer leave the machine fanless.
- `BatteryStatus.DischargeRate` glitches: 14 impossible >300 W samples during
  the battery run; endurance math filters them (`MAX_PLAUSIBLE_DISCHARGE_MW`).
