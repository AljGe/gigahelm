# -*- coding: utf-8 -*-
"""Standalone BIOS-revert watchdog.

Deliberately imports no Qt and no app service modules: this runs from the
Task Scheduler every minute and must stay cheap.  It reverts to BIOS when the
app owns fan control (last_mode auto/custom) and either no instance exists or
the instance stopped writing its heartbeat.
"""

import ctypes
import ctypes.wintypes
import os
import sys
import time
from datetime import datetime
from typing import Optional

from config.settings import (
    MUTEX_NAME, WMI_NAMESPACE, DEFAULT_WMI_SET_CLASS,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_STATUS,
    LAST_MODE_FILENAME, HEARTBEAT_FILENAME, WATCHDOG_HEARTBEAT_STALE_S,
)

ERROR_ALREADY_EXISTS = 183


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(base_dir: str, message: str) -> None:
    try:
        with open(os.path.join(base_dir, "bios_watchdog.log"), "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def _read_last_mode(base_dir: str) -> str:
    try:
        with open(os.path.join(base_dir, LAST_MODE_FILENAME), "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _heartbeat_age(base_dir: str) -> Optional[float]:
    try:
        return max(0.0, time.time() - os.path.getmtime(os.path.join(base_dir, HEARTBEAT_FILENAME)))
    except OSError:
        return None


def _instance_running() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.wintypes.LPVOID, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
    kernel32.ReleaseMutex.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if mutex:
        if not already_exists:
            kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)
    return bool(already_exists)


def _write_bios_state(base_dir: str) -> None:
    try:
        path = os.path.join(base_dir, LAST_MODE_FILENAME)
        with open(path, "w", encoding="ascii") as f:
            f.write("bios")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def _revert_to_bios(base_dir: str) -> None:
    import pythoncom
    import wmi

    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    try:
        conn = wmi.WMI(namespace=WMI_NAMESPACE)
        instances = conn.query(f"SELECT * FROM {DEFAULT_WMI_SET_CLASS}")
        if not instances:
            raise RuntimeError("could not obtain GB_WMIACPI_Set instance")
        setter = instances[0]
        getattr(setter, SET_AUTO_FAN_STATUS)(Data=1.0)
        getattr(setter, SET_CUSTOM_FAN_STATUS)(Data=0.0)
        _write_bios_state(base_dir)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def run_bios_revert_watchdog(base_dir: Optional[str] = None) -> int:
    base_dir = base_dir or _base_dir()
    last_mode = _read_last_mode(base_dir)

    if last_mode == "emergency":
        _log(base_dir, "skip: emergency fan speed is latched")
        return 0
    if last_mode not in ("auto", "custom"):
        _log(base_dir, f"skip: Gigahelm does not own fan control (last_mode={last_mode or '<empty>'})")
        return 0

    try:
        running = _instance_running()
    except Exception as error:
        _log(base_dir, f"error checking mutex: {error!r}")
        return 1
    if running:
        age = _heartbeat_age(base_dir)
        if age is not None and age <= WATCHDOG_HEARTBEAT_STALE_S:
            _log(base_dir, f"skip: app heartbeat is fresh ({age:.1f}s)")
            return 0
        _log(
            base_dir,
            "reverting: app is running but heartbeat is "
            + ("missing" if age is None else f"stale ({age:.1f}s)"),
        )
    else:
        _log(base_dir, "reverting: no app instance is running")

    last_error: Optional[BaseException] = None
    for attempt in range(1, 4):
        try:
            _revert_to_bios(base_dir)
            _log(base_dir, f"reverted fan control to BIOS (was {last_mode}) "
                           f"on attempt {attempt}")
            return 0
        except Exception as error:
            last_error = error
            _log(base_dir, f"revert attempt {attempt}/3 failed: {error!r}")
            time.sleep(2.0)
    _log(base_dir, f"error: revert failed after 3 attempts: {last_error!r}")
    return 1
