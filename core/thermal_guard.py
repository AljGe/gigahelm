# -*- coding: utf-8 -*-
"""Independent EC-level thermal fail-safe.

Runs in its own thread with its own WMI connection, deliberately decoupled
from the main control loop and its WMI worker: when the worker starves under
all-core CPU saturation (cycles stall, heartbeat goes stale, the EC latches
the last written duty), this thread still reads the CPU temperature every
couple of seconds and forces the thermal-guard floor duty whenever the EC
sits below it.

The decision rule mirrors core/fan_target.select_fan_target. While the
control loop is cycling (its liveness marks are fresh, see ``loop_alive``)
the loop owns the EC and this thread stands down completely - measured
2026-11-19, two writers with independent +-1-2 degC temperature samples
straddled the 80 degC knee ~820 times/day, each re-writing duty by 4-8
points. This thread exists for the stall case: starved worker, dead
cycles, stale heartbeat, EC latching the last written duty. BIOS/emergency
modes stand down too (the EC owns the fans, or 100 % is latched).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, List, Optional, Tuple

import pythoncom
import wmi

from config.settings import (
    DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS, WMI_NAMESPACE,
    LAST_MODE_FILENAME,
    SET_AUTO_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_CUSTOM_FAN_STATUS,
    SET_GPU_FAN_DUTY, SET_STEP_FAN_STATUS, SET_SUPER_QUIET,
)
from core.fan_target import THERMAL_GUARDS, guard_floor
from core.fan_duty import percent_to_raw
from tools.system_utils import set_thread_priority_above_normal

#: EC duty word ceiling (percent_to_raw(100)).
DUTY_RAW_MAX: float = 227.0
DEFAULT_POLL_INTERVAL_S: float = 2.0
#: A successful control cycle marks liveness; while the newest mark is
#: within this window the loop owns the EC and the watchdog stands down.
#: 5 s > one 1.5 s cycle + WMI slack, < the 8 s cycle-watchdog rebuild.
LOOP_ALIVE_GRACE_S: float = 5.0
#: Modes in which the app owns fan control; everywhere else we stand down.
OWNING_MODES = ("auto", "custom")


def force_duty_percent(cpu_temp: Optional[float], fixed_raw: Optional[float],
                       duty_raw_max: float = DUTY_RAW_MAX,
                       thresholds: List[Tuple[float, int]] = THERMAL_GUARDS,
                       ) -> Optional[int]:
    """Guard floor duty (percent) to force, or None when all is well.

    Mirrors core/fan_target.guard_floor exactly (continuous anchors), so a
    healthy control loop never fights this thread.  None when the
    temperature is unreadable or below the first anchor, or the EC already
    sits at/above the floor. An unreadable duty readback forces the floor -
    unknown is treated as too low while hot.
    """
    floor = guard_floor(cpu_temp, thresholds) if cpu_temp is not None else 0
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
                 duty_raw_max: float = DUTY_RAW_MAX,
                 loop_alive: Optional[Callable[[], Optional[float]]] = None):
        super().__init__(name="ThermalGuardWatchdog", daemon=True)
        self._base_dir = base_dir
        self._namespace = namespace
        self._get_class = get_class
        self._set_class = set_class
        self._poll_interval_s = poll_interval_s
        self._duty_raw_max = duty_raw_max
        # Seconds since the control loop's last successful cycle, or None
        # when it never cycled. Fresh (<= LOOP_ALIVE_GRACE_S) = the loop is
        # applying its own guard floors and this thread must not write at
        # all; None/stale = bridge the gap with independent EC polls.
        self._loop_alive = loop_alive
        self._last_mode_path = os.path.join(base_dir, LAST_MODE_FILENAME)
        self._stop_event = threading.Event()
        # Per-channel memory of the last floor this thread forced. It
        # throttles same-or-lower re-forces to one write per 10 s (a higher
        # floor forces immediately). The floor curve itself is continuous
        # (core/fan_target.guard_floor), so there is no engage/release state
        # to carry - the 2026-11-19 night run measured the old stepwise
        # release band pumping 60<->80 % at the thermal ceiling.
        self._last_force_monotonic_cpu: float = 0.0
        self._last_force_monotonic_gpu: float = 0.0
        self._last_forced_floor_cpu: int = 0
        self._last_forced_floor_gpu: int = 0
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

    def _force_floor(self, setter, floor: int, gpu: bool = False) -> None:
        duty_raw = float(percent_to_raw(floor))
        writes = (
            (SET_CUSTOM_FAN_STATUS, 1.0),
            (SET_SUPER_QUIET, 0.0),
            (SET_AUTO_FAN_STATUS, 0.0),
            (SET_STEP_FAN_STATUS, 0.0),
            (SET_GPU_FAN_DUTY if gpu else SET_CUSTOM_FAN_SPEED, duty_raw),
        )
        for name, data in writes:
            self._call(setter, name, Data=data)

    def _poll_channel(self, getter, setter, temp: Optional[float],
                      readback_name: str, *, gpu: bool) -> Optional[int]:
        """One channel's guard poll; returns the forced floor when written.

        ``readback_name`` is only read after the temperature alone demands a
        floor, mirroring the original CPU channel's lazy EC access.
        """
        floor = force_duty_percent(temp, None, self._duty_raw_max)
        if floor is None:
            # Cooled below the first anchor: the next engagement (and any
            # re-force) is immediate again.
            if gpu:
                self._last_forced_floor_gpu = 0
            else:
                self._last_forced_floor_cpu = 0
            return None
        readback = self._call(getter, readback_name)
        floor = force_duty_percent(temp, readback, self._duty_raw_max)
        if floor is None:
            return None  # hot but the EC already sits at/above the floor
        engaged = self._last_forced_floor_gpu if gpu else self._last_forced_floor_cpu
        last_mono = self._last_force_monotonic_gpu if gpu else self._last_force_monotonic_cpu
        now = time.monotonic()
        if floor <= engaged and now - last_mono < 10.0:
            return None  # same-or-lower floor re-forced recently: back off
        if gpu:
            self._last_force_monotonic_gpu = now
            self._last_forced_floor_gpu = floor
        else:
            self._last_force_monotonic_cpu = now
            self._last_forced_floor_cpu = floor
        self._force_floor(setter, floor, gpu=gpu)
        return floor

    def _check_once(self, getter, setter) -> Tuple[Optional[int], Optional[int]]:
        """One poll; returns ``(cpu_forced, gpu_forced)`` — the forced floor
        percent when that channel wrote, else None.

        The GPU channel stands down when both GPU sensors are unreadable
        (fault handling belongs to the control loop, which writes
        FAULT_TARGET_ONE to the GPU channel) and when they report 0 degC:
        the EC reports 0 for both GPU sensors exactly when the dGPU is
        powered down (same rule as wmi_interface._get_temperatures) — no die
        to cool, no airflow needed.
        """
        if not self._owns_fans():
            return None, None
        alive_s = self._loop_alive() if self._loop_alive is not None else None
        if alive_s is not None and alive_s <= LOOP_ALIVE_GRACE_S:
            # Fresh liveness mark: the loop owns the EC. Its own floors
            # (same guard_floor curve) are already in the applied duty, and
            # this thread's independent temperature sample can only fight
            # them (2026-11-19: ~820 disagrees/day at the 80 degC knee).
            return None, None
        cpu_temp = self._call(getter, "getCpuTemp")
        cpu_forced = self._poll_channel(getter, setter, cpu_temp,
                                        "GetFixedFanSpeed", gpu=False)
        t1 = self._call(getter, "getGpuTemp1")
        t2 = self._call(getter, "getGpuTemp2")
        gpu_forced: Optional[int] = None
        readings = [t for t in (t1, t2) if t is not None]
        if readings:
            gpu_temp = max(readings)
            if gpu_temp > 0.0:
                gpu_forced = self._poll_channel(getter, setter, gpu_temp,
                                                "GetGPUFanDuty", gpu=True)
        return cpu_forced, gpu_forced

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
                cpu_forced, gpu_forced = self._check_once(getter, setter)
                if cpu_forced is not None:
                    self._log(f"forced guard floor {cpu_forced} % (cpu, EC below floor)")
                if gpu_forced is not None:
                    self._log(f"forced guard floor {gpu_forced} % (gpu, EC below floor)")
            except Exception as error:
                self._log(f"poll failed: {error!r}")
            elapsed = time.time() - started
            self._stop_event.wait(max(0.5, self._poll_interval_s - elapsed))
