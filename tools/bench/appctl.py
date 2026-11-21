# -*- coding: utf-8 -*-
"""Gigahelm app control for the bench harness (must run elevated).

Disk edits only reach the running app via COMMAND_RELOAD_ONLY
(`Gigahelm.exe --minimized`), which re-reads the config and force-applies the
active profile; CLI profile activation (`--profile NAME`) acts on in-memory
state instead. The harness therefore always writes the config, sets
`active_profile_name`, invokes the exe with --minimized, and verifies a fresh
heartbeat plus the expected `last_mode.state`.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time

INSTALL = os.environ.get("GIGAHELM_HOME", r"C:\Tools\Gigahelm")
CONFIG = os.path.join(INSTALL, "control_config.json")
EXE = os.path.join(INSTALL, "Gigahelm.exe")
STARTUP_TASK = "Gigahelm Startup Task"

HEARTBEAT = os.path.join(INSTALL, "heartbeat.state")
LAST_MODE = os.path.join(INSTALL, "last_mode.state")

_backup_made = False


class ApplyError(RuntimeError):
    """Profile application failed after the internal retry."""


class SkipPhase(Exception):
    """Precondition (power/charge/console) unmet -> skip the phase gracefully."""


def load_cfg() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def save_cfg_atomic(cfg: dict) -> None:
    """Write the config via temp+replace; keep one per-process backup first."""
    global _backup_made
    if not _backup_made:
        backup = f"{CONFIG}.bench-bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(CONFIG, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
        _backup_made = True
        print(f"[appctl] backup written: {backup}", file=sys.stderr)
    tmp = CONFIG + ".bench-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG)


def ensure_profile(cfg: dict, name: str, defaults: dict) -> dict:
    profiles = cfg.setdefault("profiles", {})
    order = cfg.setdefault("profile_order", [])
    if name not in profiles:
        profiles[name] = copy.deepcopy(defaults)
        if name not in order:
            order.append(name)
    return profiles[name]


def edit_profile(cfg: dict, name: str, **fields) -> dict:
    profile = cfg["profiles"][name]
    profile.update(fields)
    return profile


def app_is_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Gigahelm.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    return "Gigahelm.exe" in out.stdout


def heartbeat_age() -> float:
    try:
        return max(0.0, time.time() - os.path.getmtime(HEARTBEAT))
    except OSError:
        return float("inf")


def last_mode() -> str:
    try:
        with open(LAST_MODE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def start_app(timeout: float = 30.0) -> None:
    """Start the app via its scheduled task; wait for a fresh heartbeat."""
    subprocess.run(["schtasks", "/Run", "/TN", STARTUP_TASK],
                   capture_output=True, timeout=20)
    end = time.time() + timeout
    while time.time() < end:
        if heartbeat_age() < 10.0:
            return
        time.sleep(0.5)
    raise ApplyError("app did not produce a fresh heartbeat after start")


def ensure_app_running() -> None:
    if not app_is_running():
        start_app()


def _reload_via_cli() -> None:
    try:
        subprocess.run([EXE, "--minimized"], timeout=30, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise ApplyError("Gigahelm.exe --minimized did not return within 30 s") from exc


def _wait_mode(expect_mode: str, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if heartbeat_age() < 3.0 and last_mode() == expect_mode:
            return True
        time.sleep(0.5)
    return False


def apply_profile(name: str, expect_mode: str) -> None:
    """Persist active_profile_name, reload from disk, verify mode + heartbeat.

    One internal retry: on failure the fans are forced to 100 %, the app is
    restarted via its scheduled task, and the reload is attempted again.
    """
    cfg = load_cfg()
    if name not in cfg.get("profiles", {}):
        raise ApplyError(f"profile not in config: {name}")
    cfg["active_profile_name"] = name
    save_cfg_atomic(cfg)

    def attempt() -> bool:
        ensure_app_running()
        _reload_via_cli()
        return _wait_mode(expect_mode)

    if attempt():
        return
    # Contingency for a hung app: make the EC safe, restart, retry once.
    from ec_probe import ECProbe, write_emergency_100
    try:
        probe = ECProbe()
        probe.connect()
        write_emergency_100(probe)
    except Exception as exc:
        print(f"[appctl] emergency write failed: {exc}", file=sys.stderr)
    try:
        start_app()
    except ApplyError as exc:
        raise ApplyError(f"app restart failed after failed apply: {exc}") from exc
    if attempt():
        return
    raise ApplyError(
        f"apply failed twice: mode={last_mode()!r} expected={expect_mode!r}")


def power_source(probe) -> bool:
    """True when on AC (BatteryStatus.PowerOnline)."""
    return bool(probe.sample_once()["power_online"])


def wait_cool(probe, target_c: float = 60.0, timeout: float = 240.0) -> bool:
    """Block until cpu_t <= target_c; False on timeout (caller decides)."""
    end = time.time() + timeout
    while time.time() < end:
        temp = probe.sample_once()["cpu_t"]
        if 0 <= temp <= target_c:
            return True
        time.sleep(2.0)
    return False


def await_power(probe, want_online: bool, timeout: float = 600.0) -> None:
    """Interactive power-source gate; raises SkipPhase on timeout."""
    if power_source(probe) == want_online:
        return
    verb = "Plug in AC" if want_online else "Unplug AC"
    print(f"\n*** {verb}, then press Enter here ***", flush=True)
    try:
        input()
    except EOFError as exc:
        raise SkipPhase("no interactive console to confirm power switch") from exc
    end = time.time() + timeout
    while time.time() < end:
        if power_source(probe) == want_online:
            print(f"[appctl] power source confirmed: "
                  f"{'AC' if want_online else 'battery'}", flush=True)
            return
        time.sleep(2.0)
    raise SkipPhase(f"power source still not {'AC' if want_online else 'battery'} "
                    f"after {timeout:.0f} s")


def await_charge(probe, min_charge_frac: float, timeout: float = 600.0) -> None:
    """Interactive gate: wait until charge >= fraction; SkipPhase on timeout."""
    full = probe.full_charged_capacity()

    def charge_frac() -> float:
        sample = probe.sample_once()
        remaining = sample["remaining_mwh"]
        return remaining / full if remaining >= 0 else -1.0

    if charge_frac() >= min_charge_frac:
        return
    while True:
        print(f"\n*** Charge to {min_charge_frac:.0%} "
              f"(now {charge_frac():.0%}), then press Enter here ***", flush=True)
        try:
            input()
        except EOFError as exc:
            raise SkipPhase("no interactive console to confirm charging") from exc
        end = time.time() + timeout
        while time.time() < end:
            frac = charge_frac()
            if frac >= min_charge_frac:
                return
            time.sleep(2.0)
        raise SkipPhase(
            f"charge did not reach {min_charge_frac:.0%} within {timeout:.0f} s")


def restore_active(original_name: str) -> None:
    """Best-effort restore of the pre-run active profile."""
    try:
        cfg = load_cfg()
        if original_name not in cfg.get("profiles", {}):
            print(f"[appctl] cannot restore, profile missing: {original_name}",
                  file=sys.stderr)
            return
        cfg["active_profile_name"] = original_name
        save_cfg_atomic(cfg)
        ensure_app_running()
        _reload_via_cli()
        mode = cfg["profiles"][original_name].get("fan_mode", "auto")
        _wait_mode(mode, timeout=15.0)
        print(f"[appctl] restored active profile: {original_name}", flush=True)
    except Exception as exc:
        print(f"[appctl] restore failed: {exc}", file=sys.stderr)
