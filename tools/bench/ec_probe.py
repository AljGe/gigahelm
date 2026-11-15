# -*- coding: utf-8 -*-
"""EC / telemetry sampling library for the Gigahelm bench harness.

Standalone on purpose: imports nothing from the gfb-center package tree so
the harness runs against any installed app build. Requires elevation
(root\\WMI EC access) and the `wmi` package.

CLI:
    python ec_probe.py --once
    python ec_probe.py --watch 30
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import queue
import time

import pythoncom
import wmi

RPM_READ_ERROR = -1
TEMP_READ_ERROR = -1.0

#: Full-charged capacity of this machine's battery pack (mWh), denominator for
#: charge fractions. Overridden at runtime by BatteryFullChargedCapacity.
FULL_CHARGED_CAPACITY_MWH = 99013.0

#: EC duty word ceiling: percent_to_raw(100) == 227 (core/fan_duty.py).
DUTY_RAW_MAX = 227


def _com_init() -> None:
    """Initialize COM as MTA; tolerate an apartment that is already set up."""
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except pythoncom.com_error:
        try:
            pythoncom.CoInitialize()
        except pythoncom.com_error:
            pass


def decode_rpm(value) -> int:
    """Decode a raw EC RPM word.

    The EC reports RPM big-endian inside a little-endian WMI word. A raw word
    within plausibility (0..8000) is trusted first; the byte-swapped word is
    used only when the raw word is implausible. Values <= 50 decode to 0
    (fan stopped); implausible words decode to RPM_READ_ERROR (-1).
    """
    try:
        raw = int(value) & 0xFFFF
    except (ValueError, TypeError):
        return RPM_READ_ERROR
    swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    if 0 <= raw <= 8000:
        chosen = raw
    elif 0 <= swapped <= 8000:
        chosen = swapped
    else:
        return RPM_READ_ERROR
    return 0 if chosen <= 50 else chosen


def _num(value) -> float:
    """Best-effort float conversion; failures become TEMP_READ_ERROR."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return TEMP_READ_ERROR


def read_nvidia_gpu(path: str | None) -> tuple[float, float]:
    """GPU temperature + utilization via nvidia-smi; (-1, -1) on any failure."""
    try:
        out = subprocess.run(
            [path, "--query-gpu=temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, check=True,
        )
        line = out.stdout.strip().splitlines()[0]
        temp_s, util_s = (part.strip() for part in line.split(","))
        return float(temp_s), float(util_s)
    except Exception:
        return -1.0, -1.0


#: Hard timeout for one EC COM call. The EC driver can block calls
#: indefinitely under load (observed: 30+ min); without this the whole bench
#: wedges inside a single read.
CALL_TIMEOUT_S = 5.0
#: Consecutive failed CPU reads before the WMI connection is rebuilt.
RECONNECT_AFTER_READ_FAILURES = 10


class ECTeleworker(threading.Thread):
    """Persistent thread that OWNS the EC COM connection.

    COM interface pointers are apartment-bound: the GB_WMIACPI objects must
    be created AND called in the same thread (the app's WMIWorker does this
    with CoInitializeEx(MULTITHREADED); calling them from an un-apartmented
    runner thread fails fast with RPC_E_WRONG_THREAD). Executing calls here
    lets the main thread time-bound each request and stay responsive.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="ECTeleworker")
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._getter = None
        self._setter = None
        self._failures = 0
        self._ready = threading.Event()
        self._ready_ok = False

    def wait_ready(self, timeout: float) -> bool:
        return self._ready_ok if self._ready.wait(timeout) else False

    def submit(self, name: str, kwargs: dict) -> tuple[bool, object, bool]:
        """Run one EC call; returns (ok, value, timed_out)."""
        box: dict = {}
        done = threading.Event()
        self._jobs.put((box, done, name, kwargs))
        if not done.wait(CALL_TIMEOUT_S):
            return False, None, True   # zombie: worker stuck inside a call
        return box.get("ok", False), box.get("value"), False

    def run(self) -> None:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        try:
            self._connect()
        except Exception:
            self._ready.set()          # connect() surfaces the failure
            return
        self._ready_ok = True
        self._ready.set()
        while True:
            box, done, name, kwargs = self._jobs.get()
            if name is None:           # shutdown sentinel
                break
            ok, value = False, None
            if name == "__rebuild__":
                try:
                    self._connect()
                    ok = True
                except Exception:
                    pass
            else:
                try:
                    owner = self._setter if name.startswith("Set") \
                        else self._getter
                    method = getattr(owner, name)
                    raw = method(**kwargs) if kwargs else method()
                    if isinstance(raw, tuple):
                        raw = raw[0] if raw else None
                    value = _num(raw)
                    ok = True
                except Exception:
                    pass
            if ok:
                self._failures = 0
            else:
                self._failures += 1
                if self._failures >= RECONNECT_AFTER_READ_FAILURES:
                    try:
                        self._connect()
                    except Exception:
                        pass
                    self._failures = 0
            box["ok"], box["value"] = ok, value
            done.set()
        pythoncom.CoUninitialize()

    def _connect(self) -> None:
        conn = wmi.WMI(namespace="root\\WMI")
        self._getter = conn.query("SELECT * FROM GB_WMIACPI_Get")[0]
        self._setter = conn.query("SELECT * FROM GB_WMIACPI_Set")[0]


class ECProbe:
    """Elevated EC + Windows telemetry sampler.

    Cadence budget: ladder-critical fields (temps, RPMs, duty readback,
    processor performance) are sampled every call; nvidia-smi and battery
    refresh every 2nd call and status flags every 4th (cached in between),
    keeping a full sample well under the 1.0 s interval. Every EC COM call is
    time-bounded; a wedged call degrades one sample instead of the bench.
    """

    #: EC getter methods pre-bound at connect (attribute resolution on the
    #: wmi wrapper is not free and is paid once here).
    _GETTERS = ("getCpuTemp", "getGpuTemp1", "getGpuTemp2", "getRpm1",
                "getRpm2", "GetFixedFanStatus", "GetAutoFanStatus",
                "GetStepFanStatus", "GetFixedFanSpeed", "GetCPUFanDuty",
                "GetGPUFanDuty")

    def __init__(self) -> None:
        self._worker: ECTeleworker | None = None
        self._root_w = None     # root\\wmi   (battery) - main thread
        self._cimv2 = None      # root\\cimv2 (CPU perf counters) - main thread
        self._nvidia_path: str | None = None
        self._seq = 0
        self._nv_cache = (-1.0, -1.0)
        self._battery_cache: dict | None = None
        self._status_cache: dict | None = None
        self._cpu_fail_streak = 0
        self._call_pending = False

    def call_set(self, name: str, data: float) -> bool:
        """Write one EC set-method; False on failure or timeout."""
        if self._worker is None:
            return False
        ok, _, _ = self._worker.submit(name, {"Data": data})
        return ok

    def connect(self) -> None:
        _com_init()
        self._worker = ECTeleworker()
        self._worker.start()
        if not self._worker.wait_ready(10.0):
            raise RuntimeError(
                "EC WMI objects unavailable (GB_WMIACPI); run elevated")
        try:
            self._root_w = wmi.WMI(namespace="root\\wmi")
        except Exception:
            self._root_w = None
        self._cimv2 = wmi.WMI(namespace="root\\cimv2")
        self._nvidia_path = (shutil.which("nvidia-smi")
                             or r"C:\Windows\System32\nvidia-smi.exe")

    def _call_get(self, name: str) -> float:
        """Bounded EC read via the teleworker; failures degrade to -1.

        Single-flight rule: while a previous call is wedged in the worker
        (submit timed out), return the read error immediately instead of
        queueing more work - piling up abandoned EC transactions can wedge
        the ACPI method interface machine-wide (observed after force-
        killing a process mid-call).
        """
        if self._worker is None or self._call_pending:
            if name == "getCpuTemp":
                self._cpu_fail_streak += 1
            return TEMP_READ_ERROR
        ok, value, timed_out = self._worker.submit(name, {})
        if timed_out:
            self._call_pending = True   # worker wedged: fail fast until it drains
            if name == "getCpuTemp":
                self._cpu_fail_streak += 1
                if self._cpu_fail_streak >= RECONNECT_AFTER_READ_FAILURES:
                    self._worker.submit("__rebuild__", {})
            return TEMP_READ_ERROR
        if not ok:
            if name == "getCpuTemp":
                self._cpu_fail_streak += 1
                if self._cpu_fail_streak >= RECONNECT_AFTER_READ_FAILURES:
                    self._worker.submit("__rebuild__", {})
            return TEMP_READ_ERROR
        self._call_pending = False      # worker answered: not wedged
        if name == "getCpuTemp":
            if value >= 0:
                self._cpu_fail_streak = 0
            else:
                self._cpu_fail_streak += 1
                if self._cpu_fail_streak >= RECONNECT_AFTER_READ_FAILURES:
                    self._worker.submit("__rebuild__", {})
        return value

    def _proc_perf(self) -> float:
        """% Processor Performance (_Total) - the throttle proxy."""
        if self._cimv2 is None:
            return TEMP_READ_ERROR
        try:
            infos = self._cimv2.query(
                "SELECT Name, PercentProcessorPerformance "
                "FROM Win32_PerfFormattedData_Counters_ProcessorInformation")
        except Exception:
            return TEMP_READ_ERROR
        total = None
        others = []
        for inst in infos:
            try:
                val = float(inst.PercentProcessorPerformance)
            except (TypeError, ValueError):
                continue
            if inst.Name == "_Total":
                total = val
            else:
                others.append(val)
        if total is not None:
            return total
        return sum(others) / len(others) if others else TEMP_READ_ERROR

    def _battery(self) -> dict:
        out = {
            "power_online": True,   # assume AC when telemetry is unavailable
            "remaining_mwh": TEMP_READ_ERROR,
            "discharge_mw": TEMP_READ_ERROR,
            "charge_mw": TEMP_READ_ERROR,
        }
        if self._root_w is None:
            return out
        try:
            status = self._root_w.query(
                "SELECT PowerOnline, DischargeRate, ChargeRate, RemainingCapacity "
                "FROM BatteryStatus")
        except Exception:
            return out
        if not status:
            return out
        battery = status[0]
        try:
            out["power_online"] = bool(battery.PowerOnline)
            out["remaining_mwh"] = float(battery.RemainingCapacity)
            out["discharge_mw"] = float(battery.DischargeRate)
            out["charge_mw"] = float(battery.ChargeRate)
        except (TypeError, ValueError):
            pass
        return out

    def full_charged_capacity(self) -> float:
        if self._root_w is not None:
            try:
                caps = self._root_w.query(
                    "SELECT FullChargedCapacity FROM BatteryFullChargedCapacity")
                if caps:
                    return float(caps[0].FullChargedCapacity)
            except Exception:
                pass
        return FULL_CHARGED_CAPACITY_MWH

    def sample_once(self) -> dict:
        started = time.perf_counter()
        self._seq += 1
        seq = self._seq
        # nvidia-smi: ~950 ms per call (process startup) - refresh every 4th
        # sample; a failing call backs off to every 8th so a 2 s timeout
        # cannot wreck the cadence.
        nv_ok = self._nv_cache[0] >= 0
        if (nv_ok and seq % 4 == 1) or (not nv_ok and seq % 8 == 1):
            self._nv_cache = read_nvidia_gpu(self._nvidia_path)
        gpu_temp_nv, gpu_util_nv = self._nv_cache
        # battery: every 2nd sample, or immediately while still unknown.
        if self._battery_cache is None or seq % 2 == 0:
            self._battery_cache = self._battery()
        battery = self._battery_cache
        # mode flags: informational only, every 4th sample.
        if self._status_cache is None or seq % 4 == 1:
            self._status_cache = {
                "fixed_status": self._call_get("GetFixedFanStatus"),
                "auto_status": self._call_get("GetAutoFanStatus"),
                "step_status": self._call_get("GetStepFanStatus"),
            }
        return {
            "ts": time.time(),
            "cpu_t": self._call_get("getCpuTemp"),
            "gpu1": self._call_get("getGpuTemp1"),
            "gpu2": self._call_get("getGpuTemp2"),
            "rpm1": decode_rpm(self._call_get("getRpm1")),
            "rpm2": decode_rpm(self._call_get("getRpm2")),
            **self._status_cache,
            "fixed_raw": self._call_get("GetFixedFanSpeed"),
            "cpu_duty_raw": self._call_get("GetCPUFanDuty"),
            "gpu_duty_raw": self._call_get("GetGPUFanDuty"),
            "gpu_temp_nv": gpu_temp_nv,
            "gpu_util_nv": gpu_util_nv,
            "proc_perf": self._proc_perf(),
            **battery,
            "sample_ms": (time.perf_counter() - started) * 1000.0,
        }


def write_emergency_100(probe: "ECProbe") -> None:
    """Force both fans to 100 % fixed duty (deploy-probe write order)."""
    writes = [
        ("SetFixedFanStatus", 1.0),
        ("SetSuperQuiet", 0.0),
        ("SetAutoFanStatus", 0.0),
        ("SetStepFanStatus", 0.0),
        ("SetFixedFanSpeed", 227.0),
        ("SetGPUFanDuty", 227.0),
    ]
    errors = []
    for name, data in writes:
        if not probe.call_set(name, data):
            errors.append(f"{name}: write failed")
    if len(errors) == len(writes):
        raise RuntimeError("emergency fan write failed: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="EC sampler CLI")
    parser.add_argument("--once", action="store_true", help="sample once, print JSON line")
    parser.add_argument("--watch", type=float, default=0.0, metavar="SECONDS",
                        help="sample for SECONDS, JSON lines")
    parser.add_argument("--interval", type=float, default=1.0, metavar="SECONDS",
                        help="sampling interval for --watch (default 1.0)")
    args = parser.parse_args()
    if not args.once and args.watch <= 0:
        parser.error("choose --once or --watch SECONDS")
    probe = ECProbe()
    try:
        probe.connect()
    except Exception as exc:
        print(f"EC WMI unavailable ({exc}); run this elevated", file=sys.stderr)
        return 4

    def emit() -> None:
        print(json.dumps(probe.sample_once()), flush=True)

    if args.once:
        emit()
        return 0
    end = time.time() + args.watch
    while time.time() < end:
        started = time.time()
        emit()
        time.sleep(max(0.0, args.interval - (time.time() - started)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
