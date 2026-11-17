# Big AC bench — staged, one paste needed

`run_big_ac.py` is ready but needs an elevated shell (EC WMI access), and
nothing can elevate itself while you're away: unelevated `schtasks /Create
/RL HIGHEST` is denied, and UIPI drops injected input into the elevated
terminal. So it's one paste in your admin terminal when you're back:

```powershell
.venv\Scripts\python.exe -X utf8 tools\bench\run_big_ac.py
```

What it does, unattended (~4 h):
1. Blocks sleep/hibernate for its own lifetime (`SetThreadExecutionState`,
   ES_CONTINUOUS | ES_SYSTEM_REQUIRED) — fail-safe: closed terminal or crash
   re-enables sleep automatically, no power settings are modified.
2. Gate: full unit suite.
3. Deploys the current build (`deploy_gfb.ps1`, must print PASS).
4. Demotes background apps (Firefox/Ferdium/Everything/... to BelowNormal,
   recorded to `big-ac-priorities.json`, restored at the end) so the machine
   actually cools between trials — today's quick run was contaminated by
   87–90 °C background load.
5. Full AC matrix: 6 duties × 3 loads × 2 reps ladder + profiles × 2 reps +
   validate → `results/bench-ac-full-20261116.jsonl`, then the analyze report.
6. Restores priorities, re-enables sleep, writes `results/report.md`.

Battery trials need hands on the plug — excluded. Later, with the laptop on
battery and you nearby:

```powershell
.venv\Scripts\python.exe -X utf8 tools\bench\bench.py --phase all --power battery --full --reps 1 --resume --out results/bench-ac-full-20261116.jsonl
```

Progress: `results/big-ac-status.txt` (per-step) and `results/big-ac-run.log`
(full output). The raw-temp guard-floor fix is in the deployed build only
after the wrapper's deploy step (or deploy manually first).
