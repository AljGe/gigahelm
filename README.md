# Gigahelm

Fan curves & battery charge control for Gigabyte laptops — a Windows tray application speaking the reverse-engineered Gigabyte EC WMI interface.

Community continuation of [Ixmoon/Gigabyte-Fan-Battery-Center](https://github.com/Ixmoon/Gigabyte-Fan-Battery-Center) (MIT), discontinued by its author in 2025. This fork carries the project forward with hardening work focused on crash-safety, hardware safety nets, and honest multi-model support. Not affiliated with or endorsed by Gigabyte.

> **Warning:** This tool talks to your laptop's embedded controller (EC) through an undocumented, reverse-engineered WMI interface (`root\WMI`, `GB_WMIACPI_Get`/`GB_WMIACPI_Set`). It runs with administrator rights. Use at your own risk.

## Tested hardware

| Machine | State | EC preset |
|---|---|---|
| Gigabyte AORUS 15P YD (i7-11800H, RTX 3080 Laptop) | **Tested daily** — the reference machine | raw max 227, spin floor 25 % |
| Gigabyte AORUS 15P XD | Upstream's reference machine; constants carried over, **not re-tested** | same as YD |
| Anything else | Untested — the default preset applies with a warning; verify before trusting it | see below |

Run `Gigahelm.exe --diagnose` on your machine to dump which WMI methods your EC exposes, the current sensor readbacks, and which preset resolved. Attach that JSON when filing issues on untested models.

## Features over upstream

- **BIOS-revert watchdog** — a hidden scheduled task (`Gigahelm BIOS Revert Watchdog`, every 1 min) hands fans back to BIOS if the app owned fan control but was hard-killed (Task Manager, OOM, power loss). Hard kills bypass every in-process cleanup; this is the net. The app re-registers the task at startup if it is missing, and `tools/deploy_gfb.ps1` creates it on install.
- **Async control loop** — the fan heartbeat no longer blocks the GUI thread on WMI round-trips; a hung COM call can freeze or kill the loop silently no more (8 s watchdog rebuilds the WMI worker).
- **EC write throttling** — duty writes happen on change plus a ~12 s control-flag keepalive instead of 6 EC writes every 1.5 s (~99 % fewer writes).
- **Quiet control loop** — asymmetric temperature hysteresis (spin-ups apply at once, spin-downs need a sustained ~5 °C drop), rate-limited spin-ups with heat bypass, median+EMA temperature conditioning, and a dT/dt predictive boost that pre-spins fans when temperature rises fast. Curve editor shows the EC spin floor honestly.
- **GPU-asleep state** — Optimus laptops power the dGPU off when idle; the EC then reports 0 for both GPU temp sensors. Upstream displayed `ERR` and forced a 40 % fan floor. The fork recognizes the sleep state, shows `asleep`, and lets the CPU curve alone decide — idle fans can finally follow your curve.
- **Crash safety** — packaged-mode stdout crashes fixed, unhandled exceptions latch an emergency 80 % fan speed and append to `crash.log`, boot task self-heals if deleted, controller errors surface as tray balloons.
- **Atomic config saves** — a crash mid-save can no longer wipe all profiles.
- **English runtime diagnostics** — GUI stays bilingual (EN/中文).
- **One-command build** — `tools/build_gfb.ps1` (venv, deps, unit tests, PyInstaller, style copy).

## Install

Grab `gigahelm-windows.zip` from [Releases](../../releases), unzip anywhere (`C:\Tools\Gigahelm` is the documented layout), run `Gigahelm.exe`, confirm the UAC prompt, tick **Start on Boot**.

Requires: Windows 10/11, a Gigabyte laptop exposing the GB WMI EC interface, and (unless you follow the next section) whatever driver stack shipped with it.

### Running without Gigabyte Control Center

You do **not** need GCC installed. The app only needs the `WmiAcpi` kernel service plus the MOF provider `acpimof.dll` registered. If GCC was already uninstalled and the EC classes vanished:

1. Restore `acpimof.dll` (from a GCC install or backup) to `C:\Windows\SysWOW64\`.
2. Set `HKLM\SYSTEM\CurrentControlSet\Services\WmiAcpi` → `MofImagePath` = `\SystemRoot\SysWOW64\acpimof.dll`.
3. Reboot.

Beware: uninstalling GCC itself **deletes** `SysWOW64\acpimof.dll` and that registry value — back both up first. Gigahelm keeps working after an unclean uninstall only until the next reboot.

## CLI

| Command | Effect |
|---|---|
| `Gigahelm.exe --minimized` | Start hidden in tray (what the boot task runs) |
| `Gigahelm.exe --profile "Gaming"` | Switch profile in the running instance |
| `Gigahelm.exe --charge-policy custom --charge-threshold 80` | Set battery charge limit (60–100) |
| `Gigahelm.exe --revert-bios` | Watchdog entry point: hand fans to BIOS if the app is gone (requires admin) |
| `Gigahelm.exe --diagnose` | Write `diagnose_report.json` for issue reports (requires admin) |

## Building from source

```powershell
tools/build_gfb.ps1     # creates .venv, installs deps, runs the full test suite, builds dist\Gigahelm
tools/deploy_gfb.ps1    # (elevated) stop app, robocopy dist to C:\Tools\Gigahelm, re-register boot + watchdog tasks, probe
```

CI runs the unit tests and build on every push; tagged `v*` releases publish a zip + SHA256.

## Development

```
python -m unittest discover -s tests    # 60 tests, no EC access needed (worker + decision logic are stubbed)
```

The EC-facing layer (`core/wmi_interface.py`) is isolated behind a request queue; everything above it (`app_services`, `auto_temp_controller`, `control_smoothing`, `fan_ramp`, `fan_duty`, `bios_watchdog`) is testable without hardware. Add model presets in `config/models.py` — and only after verifying the raw range with `--diagnose` on real hardware.

Not-yet-built control optimizations (NVML power feed-forward, closed-loop temperature regulation, and more) live in [ROADMAP.md](ROADMAP.md).

## License

MIT — see [LICENSE](LICENSE). Upstream © 2025 Ixmoon.
