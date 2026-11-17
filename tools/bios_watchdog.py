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

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.wintypes.WCHAR * 260),
    ]


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


def _local_holder_running(base_dir: str) -> Optional[bool]:
    """True when a live process's image sits under base_dir (this install
    owns the Global mutex), False when the holder is from another data home
    (e.g. a script-mode run from the repo), None when the process walk fails
    (caller falls back to the legacy heartbeat-only behavior)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
    kernel32.Process32FirstW.restype = ctypes.wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
        ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == ctypes.c_size_t(-1).value:
        return None
    prefix = os.path.normcase(base_dir) + os.sep
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            pid = entry.th32ProcessID
            if pid and pid != os.getpid():
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        size = ctypes.wintypes.DWORD(1024)
                        image = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                            if os.path.normcase(image.value).startswith(prefix):
                                return True
                    finally:
                        kernel32.CloseHandle(handle)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return False
    finally:
        kernel32.CloseHandle(snapshot)


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
        holder_here = _local_holder_running(base_dir)
        if holder_here is False:
            _log(base_dir, "skip: mutex held by a process from another install/data home")
            return 0
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
