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
7. **Ramp-shaping A/B (noise).** v2.3.1 glides curve-driven up-ramps
   (`UP_STEP_PERCENT = 10`/tick, guard floors bypass) and staggers same-tick
   start surges one channel apart. Thermals are covered by unit tests + the
   guard bypass; what needs ears is perception: run a workload that crosses a
   curve knee with and without the shaping (`tools/bench/split_ab.py` or a
   plain game), confirm the ramp reads as a glide and no transition got
   *more* annoying (longer mid-glide at moderate duty is the known tradeoff).
   If `UP_STEP_PERCENT = 10` still surges audibly, try 7; if it feels
   laggy on burst launches, the NVML feed-forward already leads — leave it.
8. **Ladder inversion is guard-dominated under load (2026-11-19).** In the
   night matrix every commanded duty 25-70 % ran an actual ~80 % under `all`/
   `dual` (the guard floors own the ceiling), and all-core steady temps are
   89-91 °C at any duty — so curve synthesis inverts to a flat ~89 % curve
   ("Tuned AC/Battery") and validate FAILs the 86 °C cap. Do not derive
   curves from guard-dominated rows; use idle/dual rows only, or fix the
   derivation. The 2026-11-19 derived profiles were removed from the config.
9. **Guard flap fix landed and deployed (verified 2026-11-19 07:45).** The
   pre-fix flap_watch block measured the stepwise tiers pumping 60↔80 %
   (21 duty steps ≥45 raw = 20 points, ceiling raw 182 = 80 %).
   `guard_floor` is now continuous between the same anchors (80→40, 85→60,
   90→80; never below the old tier in between). Post-deploy re-run: ceiling
   raw 155 (68 %), max duty step 64 (was 91), steps ≥45 raw 15 (was 21),
   ON-hot median duty 137 (was 182) at the same CPU temps — quieter and
   smaller steps, no tier plateaus. Residual 40↔68 oscillation in the
   synthetic burst block tracks the **EC CPU temp readback flapping**
   (1 s samples: 86→63→72→87 °C; physically impossible; 117 vs 29 jumps
   ≥15 °C pre/post, and the harness adds a second WMI client to the EC
   mailbox). That is sensor/mailbox reality, not floor math — do NOT add
   control filtering on synthetic evidence. Real-world check: use the
   machine normally for a day, then count `thermal_guard.log` forces (should
   be few and small). If audible surges persist, add asymmetric glitch
   rejection to the floor temp path (fast up, bounded decay ~8 points/tick)
   and mirror it in the watchdog. Bench metric updated: flap_watch now
   reports `duty_steps` (event counts are meaningless with continuous
   floors). Split A/B with mic (same night): split won the acoustic ranking
   (dual band −77.8 vs −73.3 dB, GPU fan off, +0.0 °C CPU) — installed
   Silent now runs `split_fan_control: true` (verified).

10. **Guard stands down while the loop cycles (2026-11-19, second pass).**
    A day of real use on the continuous floors still logged 820 CPU forces
    (0 GPU): the loop and the guard sample CPU temperature independently,
    ±1-2 °C apart, and straddle the 80 °C knee (loop reads 79 → curve duty
    30 %, guard reads 81 → floor 44 %) — two masters, ~820 rewrites/day of
    4-8 duty points. No readback slack can absorb a 0-vs-44 disagreement at
    the anchor edge. Fix: the loop marks liveness on every successful cycle
    (`_mark_loop_alive`); the watchdog stands down entirely while the mark
    is fresh (`LOOP_ALIVE_GRACE_S = 5 s`) and only bridges real stalls
    (starved worker, dead cycles, NameError-style failures — those never
    mark). Unknown age (never cycled) keeps the guard armed. The deployed
    build must show near-zero `thermal_guard.log` forces during normal use;
    a burst of forces now means a genuine loop stall and pairs with
    `cycle failed` lines in `control_loop.log`.

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
