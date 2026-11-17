# -*- coding: utf-8 -*-
"""Independent EC-level thermal fail-safe.

Runs in its own thread with its own WMI connection, deliberately decoupled
from the main control loop and its WMI worker: when the worker starves under
all-core CPU saturation (cycles stall, heartbeat goes stale, the EC latches
the last written duty), this thread still reads the CPU temperature every
couple of seconds and forces the thermal-guard floor duty whenever the EC
sits below it.

The decision rule mirrors core/fan_target.select_fan_target, so a healthy
control loop never fights this thread: it only ever writes what the loop
would already have written. BIOS/emergency modes stand down (the EC owns the
fans, or 100 % is latched).
"""
from __future__ import annotations

import os
import threading
import time
from typing import List, Optional, Tuple

import pythoncom
import wmi

from config.settings import (
    DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS, WMI_NAMESPACE,
    LAST_MODE_FILENAME,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_CUSTOM_FAN_STATUS,
    SET_GPU_FAN_DUTY, SET_STEP_FAN_STATUS, SET_SUPER_QUIET,
)
from core.fan_target import GUARD_RELEASE_C, THERMAL_GUARDS
from tools.system_utils import set_thread_priority_above_normal

#: EC duty word ceiling (percent_to_raw(100)).
DUTY_RAW_MAX: float = 227.0
DEFAULT_POLL_INTERVAL_S: float = 2.0
#: Modes in which the app owns fan control; everywhere else we stand down.
OWNING_MODES = ("auto", "custom")


def force_duty_percent(cpu_temp: Optional[float], fixed_raw: Optional[float],
                       duty_raw_max: float = DUTY_RAW_MAX,
                       thresholds: List[Tuple[float, int]] = THERMAL_GUARDS,
                       engaged_floor: int = 0,
                       release_c: float = GUARD_RELEASE_C
                       ) -> Optional[int]:
    """Guard floor duty (percent) to force, or None when all is well.

    None when the temperature is unreadable or below every threshold (or
    release hysteresis holds nothing), or the EC already sits at/above the
    floor. An unreadable duty readback forces the floor - unknown is treated
    as too low while hot. ``engaged_floor`` mirrors the engage/release rule of
    core/fan_target.guard_floor so the watchdog never fights the loop.
    """
    if cpu_temp is None:
        return None
    floor = 0
    for threshold, minimum in thresholds:
        if cpu_temp >= threshold:
            floor = max(floor, minimum)
    if engaged_floor:
        for threshold, minimum in thresholds:
            if minimum == engaged_floor and cpu_temp >= threshold - release_c:
                floor = max(floor, engaged_floor)
                break
    if floor == 0:
        return None
    if fixed_raw is None or fixed_raw < 0:
        return floor
    if fixed_raw / duty_raw_max * 100.0 >= floor:
        return None
    return floor


class ThermalGuardWatchdog(threading.Thread):
    """Above-normal-priority poll thread; forces the guard floor when hot."""

    def __init__(self, base_dir: str, namespace: str = WMI_NAMESPACE,
                 get_class: str = DEFAULT_WMI_GET_CLASS,
                 set_class: str = DEFAULT_WMI_SET_CLASS,
                 poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
                 duty_raw_max: float = DUTY_RAW_MAX):
        super().__init__(name="ThermalGuardWatchdog", daemon=True)
        self._base_dir = base_dir
        self._namespace = namespace
        self._get_class = get_class
        self._set_class = set_class
        self._poll_interval_s = poll_interval_s
        self._duty_raw_max = duty_raw_max
        self._last_mode_path = os.path.join(base_dir, LAST_MODE_FILENAME)
        self._stop_event = threading.Event()
        # Dual-purpose state: the last floor this thread forced. It throttles
        # same-or-lower re-forces to one write per 10 s (a higher floor forces
        # immediately) and carries engage/release hysteresis: the floor holds
        # until the CPU cools GUARD_RELEASE_C below its engage threshold, so
        # integer sensor noise at 80/85/90 degC cannot pump the fans.
        self._last_force_monotonic: float = 0.0
        self._last_forced_floor: int = 0
        self._owns_cache: Optional[tuple] = None

    def stop(self, join_s: float = 0.0) -> None:
        self._stop_event.set()
        if join_s > 0:
            self.join(timeout=join_s)

    # ------------------------------------------------------------- internals
    #: Rotate thermal_guard.log to .old once it exceeds this size.
    LOG_MAX_BYTES: int = 262144

    def _log(self, message: str) -> None:
        try:
            path = os.path.join(self._base_dir, "thermal_guard.log")
            try:
                if os.path.exists(path) and os.path.getsize(path) > self.LOG_MAX_BYTES:
                    os.replace(path, path + ".old")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}\n")
        except OSError:
            pass

    def _owns_fans(self) -> bool:
        # last_mode.state only changes on mode switches; key the read on the
        # mtime instead of reopening the file every 2 s.
        try:
            mtime = os.stat(self._last_mode_path).st_mtime_ns
        except OSError:
            self._owns_cache = None
            return False
        if self._owns_cache and self._owns_cache[0] == mtime:
            return self._owns_cache[1]
        try:
            with open(self._last_mode_path, encoding="utf-8") as fh:
                value = fh.read().strip() in OWNING_MODES
        except OSError:
            return False
        self._owns_cache = (mtime, value)
        return value

    def _init_com(self) -> Optional[tuple]:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            conn = wmi.WMI(namespace=self._namespace)
            getter = conn.query(f"SELECT * FROM {self._get_class}")[0]
            setter = conn.query(f"SELECT * FROM {self._set_class}")[0]
            return getter, setter
        except Exception as error:
            self._log(f"init failed: {error!r}")
            return None

    def _call(self, obj, name: str, **kwargs) -> Optional[float]:
        try:
            result = getattr(obj, name)(**kwargs)
            if isinstance(result, tuple):
                result = result[0] if result else None
            return float(result) if result is not None else None
        except Exception:
            return None

    def _force_floor(self, setter, floor: int) -> None:
        duty_raw = floor / 100.0 * self._duty_raw_max
        writes = (
            (SET_CUSTOM_FAN_STATUS, 1.0),
            (SET_SUPER_QUIET, 0.0),
            (SET_AUTO_FAN_STATUS, 0.0),
            (SET_STEP_FAN_STATUS, 0.0),
            (SET_CUSTOM_FAN_SPEED, duty_raw),
            (SET_GPU_FAN_DUTY, duty_raw),
        )
        for name, data in writes:
            self._call(setter, name, Data=data)

    def _check_once(self, getter, setter) -> Optional[int]:
        """One poll; returns the forced floor percent when a write happened."""
        if not self._owns_fans():
            return None
        cpu_temp = self._call(getter, "getCpuTemp")
        floor = force_duty_percent(cpu_temp, None, self._duty_raw_max,
                                   engaged_floor=self._last_forced_floor)
        if floor is None:
            # Cool past the release band: engagement ends, next engagement
            # at the threshold forces immediately again.
            self._last_forced_floor = 0
            return None
        fixed_raw = self._call(getter, "GetFixedFanSpeed")
        floor = force_duty_percent(cpu_temp, fixed_raw, self._duty_raw_max,
                                   engaged_floor=self._last_forced_floor)
        if floor is None:
            return None  # hot but the EC already sits at/above the floor
        now = time.monotonic()
        if floor <= self._last_forced_floor and now - self._last_force_monotonic < 10.0:
            return None  # same-or-lower floor re-forced recently: back off
        self._last_force_monotonic = now
        self._last_forced_floor = floor
        self._force_floor(setter, floor)
        return floor

    # ------------------------------------------------------------------ run
    def run(self):
        set_thread_priority_above_normal()
        connection = None
        while connection is None and not self._stop_event.is_set():
            connection = self._init_com()
            if connection is None:
                self._stop_event.wait(5.0)
        if connection is None:
            return
        getter, setter = connection
        self._log("watchdog active")
        while not self._stop_event.is_set():
            started = time.time()
            try:
                forced = self._check_once(getter, setter)
                if forced is not None:
                    self._log(f"forced guard floor {forced} % (cpu hot, EC below floor)")
            except Exception as error:
                self._log(f"poll failed: {error!r}")
            elapsed = time.time() - started
            self._stop_event.wait(max(0.5, self._poll_interval_s - elapsed))
