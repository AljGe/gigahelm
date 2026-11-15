# -*- coding: utf-8 -*-
"""BIOS-revert watchdog: the safety net for hard kills.

`atexit`/excepthook never run when the process is killed outright (Task
Manager "End task", OOM, power loss). In that state the EC keeps whatever
fan mode was last written - potentially SetFixedFanStatus=1 with a low
duty and nothing left to manage it. This module is called by the hidden
scheduled task ("GFBC BIOS Revert Watchdog", every 5 minutes + at logon)
via `main.exe --revert-bios`:

* if ``last_mode.state`` says GFBC owns fan control (auto/custom) and no
  app instance holds the single-instance mutex, hand fans back to BIOS,
  write ``bios`` back to ``last_mode.state`` and exit 0;
* otherwise (app running, BIOS mode, missing file) skip silently.

Dependency-free on purpose: uses raw ``wmi``/``pythoncom`` and ctypes so
it stays independent from the GUI stack.
"""
import ctypes
import os
from datetime import datetime
from typing import Callable, Optional

from config.settings import (
    MUTEX_NAME, WMI_NAMESPACE, DEFAULT_WMI_SET_CLASS,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_STATUS,
)

OWNING_MODES = ("auto", "custom")
ERROR_ALREADY_EXISTS = 183


def read_owning_mode(base_dir: str) -> str:
    """Return the persisted fan mode, or '' when unreadable."""
    try:
        with open(os.path.join(base_dir, 'last_mode.state'), 'r') as f:
            return f.read().strip()
    except OSError:
        return ""


def app_instance_running() -> bool:
    """True when another instance holds the app's global mutex.

    Creates (and immediately releases) the mutex ourselves; on Windows this
    is the same mechanism the app uses for single-instancing, so there is no
    process-name guessing involved.
    """
    if os.name != 'nt':
        return True  # conservative: never touch fans when we cannot probe
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        return False  # cannot probe -> assume free; the EC write is guarded anyway
    running = (ctypes.get_last_error() == ERROR_ALREADY_EXISTS)
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)
    return running


def revert_via_wmi() -> None:
    """The BIOS handover EC sequence. Raises on any WMI failure."""
    import wmi
    import pythoncom
    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    try:
        conn = wmi.WMI(namespace=WMI_NAMESPACE)
        instances = conn.query(f"SELECT * FROM {DEFAULT_WMI_SET_CLASS}")
        if not instances:
            raise RuntimeError("could not obtain GB_WMIACPI_Set instance")
        setter = instances[0]
        getattr(setter, SET_AUTO_FAN_STATUS)(Data=1.0)
        getattr(setter, SET_CUSTOM_FAN_STATUS)(Data=0.0)
    finally:
        pythoncom.CoUninitialize()


def run_bios_revert_watchdog(
    base_dir: str,
    probe_running: Optional[Callable[[], bool]] = None,
    revert: Optional[Callable[[], None]] = None,
) -> int:
    """Entry point for ``main.exe --revert-bios``. Returns a process exit code.

    ``probe_running``/``revert`` are injectable for tests.
    """
    def log(message: str):
        try:
            with open(os.path.join(base_dir, 'bios_watchdog.log'), 'a', encoding='utf-8', errors='replace') as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
        except Exception:
            pass

    try:
        last_mode = read_owning_mode(base_dir)
        if last_mode not in OWNING_MODES:
            log(f"skip: GFBC does not own fan control (last_mode={last_mode or '<empty>'})")
            return 0
        if (probe_running or app_instance_running)():
            log("skip: an app instance is running")
            return 0
        (revert or revert_via_wmi)()
        # Only after a successful EC write: a failed write must leave the
        # file saying we still own control, so the next tick retries.
        with open(os.path.join(base_dir, 'last_mode.state'), 'w') as f:
            f.write('bios')
        log(f"reverted fan control to BIOS (was {last_mode})")
        return 0
    except Exception as error:
        log(f"error: {error!r}")
        return 1
