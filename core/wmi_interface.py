# -*- coding: utf-8 -*-
"""
提供一个极其轻薄、扁平化的WMI交互接口。
该模块是一个纯粹的、被动的服务提供者。
"""

import threading
import queue
import time
import math
import sys
from typing import Optional, Any, Dict, Tuple, Callable, List

from gui.qt import QObject, QTimer, Slot

_wmi_available = False
try:
    import wmi
    import pythoncom
    _wmi_available = True
except ImportError:
    print("Warning: 'wmi' or 'pythoncom' package not found. WMI features will be disabled.", file=sys.stderr)
    wmi = None
    pythoncom = None

# Optional: NVML gives the dGPU's real power draw for feed-forward fan control.
# Everything works without it (the EC temperature heuristic drives alone).
_nvml_available = False
try:
    import pynvml
    _nvml_available = True
except ImportError:
    pynvml = None

from .battery_health import get_battery_health
from tools.system_utils import set_thread_priority_above_normal

from config.settings import (
    WMI_NAMESPACE, DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS,
    GET_CPU_TEMP, GET_GPU_TEMP1, GET_GPU_TEMP2,
    GET_RPM1, GET_RPM2, GET_CHARGE_POLICY, GET_CHARGE_STOP,
    GET_FIXED_FAN_STATUS, GET_AUTO_FAN_STATUS, GET_STEP_FAN_STATUS,
    GET_FIXED_FAN_SPEED, GET_CPU_FAN_DUTY, GET_GPU_FAN_DUTY,
    GET_BATTERY_HEALTH, GET_BATT_CYC, GET_BATT_CYC1,
    SET_CUSTOM_FAN_STATUS, SET_SUPER_QUIET, SET_AUTO_FAN_STATUS,
    SET_STEP_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_GPU_FAN_DUTY,
    SET_CHARGE_POLICY, SET_CHARGE_STOP,
    WMI_REQUEST_TIMEOUT_S, WMI_ASYNC_RESULT_TIMEOUT_S,
    TEMP_READ_ERROR_VALUE, RPM_READ_ERROR_VALUE,
    NVML_FF_IDLE_W, NVML_FF_RESUME_TEMP_C, NVML_IDLE_SKIP_W,
    WMIInternalSignal
)

WMIResult = Tuple[Optional[Any], Optional[Exception]]
WMIRequest = Tuple[Any, Dict[str, Any], Optional[queue.Queue]]

class WMIError(Exception): pass
class WMIConnectionError(WMIError): pass
class WMIRequestTimeoutError(WMIError): pass
class WMICommandError(WMIError):
    def __init__(self, message, original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception

class WMIWorker(threading.Thread):
    """后台线程，处理所有阻塞的WMI调用。

    Every worker owns its queue.  A hung worker can therefore never consume a
    STOP sentinel intended for its replacement, and a replacement never shares
    a queue with the old thread.
    """
    def __init__(self, request_queue: queue.Queue, wmi_get_class: str, wmi_set_class: str):
        super().__init__(name="WMIWorkerThread", daemon=True)
        self._request_queue = request_queue
        self._wmi_get_class_name = wmi_get_class
        self._wmi_set_class_name = wmi_set_class
        self._wmi_get_obj: Optional[Any] = None
        self._wmi_set_obj: Optional[Any] = None
        self._com_initialized: bool = False
        # NVML (optional): handle lives on this worker thread only.
        self._nvml_handle = None
        self._nvml_ok: bool = False
        # Sleep guard: when False (or when the EC reports the dGPU asleep)
        # the sensor sweep makes NO NVML calls, so polling can never keep
        # the GPU out of D3cold. AppServices mirrors the active profile's
        # gpu_power_feedforward flag into this every cycle.
        self._nvml_poll_enabled: bool = True
        # Utilization is display-only: skip the extra NVML call while no UI
        # is watching (AppServices mirrors UI visibility into this).
        self._nvml_util_enabled: bool = True
        # Idle skip: an idle-floor reading pauses polling until genuine load
        # (EC GPU temp) or a sleep/wake cycle re-arms it.
        self._nvml_last_power_w: Optional[float] = None
        self._nvml_idle_paused: bool = False
        # GPU-asleep probe latch: while the dGPU is off, the EC reports 0/0
        # for both GPU sensors, so re-probing them every sweep buys nothing.
        # A fresh worker (rebuild/resume) starts unlatched.
        self._gpu_asleep_latched: bool = False
        self._gpu_sleep_probes_left: int = 0
        self.initialization_error: Optional[Exception] = None
        self.initialization_complete = threading.Event()
        self._stop_event = threading.Event()
        self._latest_core_sensor_data: Dict[str, Any] = {}
        self._data_lock = threading.Lock()
        self._call_pending = False

    def request_stop(self):
        """Ask the loop to exit; also used when a hung worker is retired."""
        self._stop_event.set()
        try:
            self._request_queue.put_nowait((WMIInternalSignal.STOP, {}, None))
        except queue.Full:
            pass

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    def _init_wmi(self) -> bool:
        if not _wmi_available or not pythoncom or not wmi:
            self.initialization_error = WMIConnectionError("WMI或pythoncom库未找到。")
            return False
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            self._com_initialized = True
            wmi_conn = wmi.WMI(namespace=WMI_NAMESPACE)
            self._wmi_get_obj = self._get_wmi_instance(wmi_conn, self._wmi_get_class_name)
            self._wmi_set_obj = self._get_wmi_instance(wmi_conn, self._wmi_set_class_name)
            if not self._wmi_get_obj or not self._wmi_set_obj:
                raise WMIConnectionError("Could not obtain WMI object. Ensure Gigabyte drivers are installed.")
            self._init_nvml()
            return True
        except Exception as e:
            self.initialization_error = e
            self._cleanup_com()
            return False

    def _get_wmi_instance(self, conn: Any, class_name: str) -> Optional[Any]:
        try:
            instances = conn.query(f"SELECT * FROM {class_name}")
            return instances[0] if instances else None
        except Exception: return None

    def _init_nvml(self):
        """Optional NVML setup; runs on the worker thread (NVML is
        thread-affine like COM). Any failure just disables feed-forward."""
        if not _nvml_available:
            return
        try:
            pynvml.nvmlInit()
            for index in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name = pynvml.nvmlDeviceGetName(handle)
                name = name.decode() if isinstance(name, bytes) else str(name)
                lowered = name.lower()
                if "intel" in lowered or "amd" in lowered or "radeon" in lowered:
                    continue  # iGPU / non-target vendor: feed-forward tracks the NVIDIA dGPU
                self._nvml_handle = handle
                self._nvml_ok = True
                return
        except Exception:
            self._nvml_ok = False

    def _read_nvml_gpu(self):
        """(power_w, util_percent) from NVML, or (None, None).

        A dGPU in D3cold sleep makes NVML reads raise; transient driver
        errors do the same. Neither disables NVML for the session - they
        only skip feed-forward for that cycle and fall back to the EC
        temperature heuristic."""
        if not self._nvml_ok:
            return None, None
        try:
            power_w = pynvml.nvmlDeviceGetPowerUsage(self._nvml_handle) / 1000.0
        except Exception:
            return None, None
        util = None
        if self._nvml_util_enabled:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle).gpu
            except Exception:
                pass
        return power_w, util

    def _cleanup_com(self):
        if self._nvml_ok and pynvml:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._nvml_ok = False
        if self._com_initialized and pythoncom:
            try: pythoncom.CoUninitialize()
            except Exception: pass
            self._com_initialized = False

    def run(self):
        """WMI工作线程的主循环，仅处理自己的请求队列。"""
        # Above-normal priority: under all-core CPU saturation a normal-
        # priority worker starves, cycles stall, and the EC latches the last
        # duty (observed as fans parked at 0-25 % at 90 C). The control loop
        # is the app's reason to exist - it outranks burner-class load.
        set_thread_priority_above_normal()
        if not self._init_wmi():
            self.initialization_complete.set()
            return
        self.initialization_complete.set()

        while not self._stop_event.is_set():
            # Blocking get: request_stop() always enqueues a STOP sentinel,
            # so a poll timeout would only add 4 idle wakeups per second.
            request = self._request_queue.get()
            if request[0] is WMIInternalSignal.STOP:
                break
            if self._stop_event.is_set():
                break
            self._process_request(request)

        self._cleanup_com()

    def get_latest_core_sensor_data(self) -> Dict[str, Any]:
        with self._data_lock:
            return self._latest_core_sensor_data.copy()

    def _dispatch(self, method_or_signal: Any, params: Dict[str, Any]) -> Any:
        """Smart dispatch: choose the WMI object/executor for one request."""
        if method_or_signal is WMIInternalSignal.POLL_CORE_SENSORS:
            result = self._get_core_sensors()
            with self._data_lock: self._latest_core_sensor_data = result
            return result
        if method_or_signal is WMIInternalSignal.APPLY_FAN_CONTROL:
            return self._apply_fan_control(
                float(params.get("duty_raw", 0.0)),
                bool(params.get("reassert_flags", True)),
            )
        if method_or_signal is WMIInternalSignal.APPLY_BIOS_CONTROL:
            return self._apply_bios_control()
        if method_or_signal is WMIInternalSignal.APPLY_CHARGE_POLICY:
            return self._apply_charge_policy(
                float(params.get("policy", 0.0)),
                params.get("threshold"),
            )
        if method_or_signal == "_get_all_sensors":
            return self._get_all_sensors()
        if method_or_signal == "_get_charge_status":
            return self._get_charge_status()
        if method_or_signal == "_get_temperatures":
            return self._get_temperatures()
        if method_or_signal == "_get_core_sensors":
            return self._get_core_sensors()
        if isinstance(method_or_signal, str):
            method_name = method_or_signal
            target_obj = self._wmi_set_obj if method_name.startswith("Set") else self._wmi_get_obj
            return self._execute_wmi_method(target_obj, method_name, **params)
        raise ValueError("无效的WMI请求类型")

    def _process_request(self, request: WMIRequest):
        """处理单个请求：受限时间执行。EC/驱动可能无限期阻塞 COM 调用，
        因此每个请求在独立线程中执行并有硬超时；超时后该调用被放弃，
        工作线程保持响应（周期看门狗/外部看门狗负责后续恢复）。
        """
        method_or_signal, params, callback_queue = request

        # A previously abandoned call may still be blocked in the driver.
        # Fail fast instead of stacking runner threads.
        if self._call_pending:
            exception: Optional[Exception] = WMIRequestTimeoutError(
                "previous WMI call still pending")
            if callback_queue:
                try: callback_queue.put((None, exception), block=False)
                except queue.Full: print("Warning: WMI response queue is full.", file=sys.stderr)
            return

        outcome: Dict[str, Any] = {}
        done = threading.Event()

        def _execute() -> None:
            result, exc = None, None
            try:
                result = self._dispatch(method_or_signal, params)
            except Exception as e:
                exc = e
            outcome["result"], outcome["exception"] = result, exc
            done.set()

        self._call_pending = True
        runner = threading.Thread(target=_execute, daemon=True,
                                  name="WMICallRunner")
        try:
            runner.start()
            runner.join(WMI_REQUEST_TIMEOUT_S)
        except Exception:
            self._call_pending = False
            raise
        if done.is_set():
            self._call_pending = False   # completed: EC slot free again
            result = outcome.get("result")
            exception = outcome.get("exception")
        else:
            # Abandoned call still blocked in the driver: keep the pending
            # flag until it drains so later requests fail fast instead of
            # stacking runner threads behind it.
            result = None
            exception = WMIRequestTimeoutError(
                f"WMI call timed out after {WMI_REQUEST_TIMEOUT_S:.0f} s")

            def _reap() -> None:
                runner.join()
                self._call_pending = False

            threading.Thread(target=_reap, daemon=True,
                             name="WMICallReaper").start()

        if callback_queue:
            try: callback_queue.put((result, exception), block=False)
            except queue.Full: print("Warning: WMI response queue is full.", file=sys.stderr)

    def _execute_wmi_method(self, wmi_obj: Any, method_name: str, **kwargs) -> Any:
        """执行任何WMI方法的唯一入口。"""
        if not wmi_obj: raise WMIConnectionError("WMI object is unavailable.")
        method_func = getattr(wmi_obj, method_name)
        raw_result = method_func(**kwargs)
        # 内联结果解析
        return raw_result[0] if isinstance(raw_result, tuple) and len(raw_result) > 0 else raw_result

    def _validate_temp(self, value: Any) -> float:
        try:
            temp = float(value)
            return temp if 0 < temp < 150 and not math.isnan(temp) else TEMP_READ_ERROR_VALUE
        except (ValueError, TypeError): return TEMP_READ_ERROR_VALUE

    def _validate_rpm(self, value: Any) -> int:
        """Decode an EC RPM word: raw-plausible-first.

        Trusts a raw word within plausibility (0..8000) first; the
        byte-swapped word is used only when raw is implausible. Matches
        tools/bench/ec_probe.decode_rpm so app display, stall detection and
        bench analysis agree. Values <= 50 decode to fans-off; implausible
        words are RPM_READ_ERROR.
        """
        try:
            raw = int(value) & 0xFFFF
        except (ValueError, TypeError):
            return RPM_READ_ERROR_VALUE
        swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
        if 0 <= raw <= 8000:
            chosen = raw
        elif 0 <= swapped <= 8000:
            chosen = swapped
        else:
            return RPM_READ_ERROR_VALUE
        return 0 if chosen <= 50 else chosen

    def _read_optional_int(self, method_name: str) -> int:
        try:
            return int(self._execute_wmi_method(self._wmi_get_obj, method_name))
        except Exception:
            return -1

    def _to_int(self, value: Any) -> Optional[int]:
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def _apply_fan_control(self, duty_raw: float, reassert_flags: bool) -> Dict[str, Any]:
        """Worker-side fan write: reassert software control flags, then write the duty to both fans."""
        if reassert_flags:
            self._execute_wmi_method(self._wmi_set_obj, SET_CUSTOM_FAN_STATUS, Data=1.0)
            self._execute_wmi_method(self._wmi_set_obj, SET_SUPER_QUIET, Data=0.0)
            self._execute_wmi_method(self._wmi_set_obj, SET_AUTO_FAN_STATUS, Data=0.0)
            self._execute_wmi_method(self._wmi_set_obj, SET_STEP_FAN_STATUS, Data=0.0)
        self._execute_wmi_method(self._wmi_set_obj, SET_CUSTOM_FAN_SPEED, Data=float(duty_raw))
        self._execute_wmi_method(self._wmi_set_obj, SET_GPU_FAN_DUTY, Data=float(duty_raw))
        return {"applied": True}

    def _apply_bios_control(self) -> Dict[str, Any]:
        """Hand the fans back to the EC's automatic mode."""
        self._execute_wmi_method(self._wmi_set_obj, SET_AUTO_FAN_STATUS, Data=1.0)
        self._execute_wmi_method(self._wmi_set_obj, SET_CUSTOM_FAN_STATUS, Data=0.0)
        return {"applied": True}

    def _apply_charge_policy(self, policy: float, threshold: Any) -> Dict[str, Any]:
        self._execute_wmi_method(self._wmi_set_obj, SET_CHARGE_POLICY, Data=float(policy))
        if threshold is not None:
            self._execute_wmi_method(self._wmi_set_obj, SET_CHARGE_STOP, Data=float(threshold))
        return {"applied": True, "threshold": threshold}

    def _get_temperatures(self) -> Dict[str, Any]:
        cpu_temp = self._validate_temp(self._execute_wmi_method(self._wmi_get_obj, GET_CPU_TEMP))
        if self._gpu_asleep_latched and self._gpu_sleep_probes_left > 0:
            self._gpu_sleep_probes_left -= 1
            return {'cpu_temp': cpu_temp, 'gpu_temp': TEMP_READ_ERROR_VALUE, 'gpu_asleep': True}
        raw_gpu = [self._execute_wmi_method(self._wmi_get_obj, m) for m in [GET_GPU_TEMP1, GET_GPU_TEMP2] if hasattr(self._wmi_get_obj, m)]
        temps = [self._validate_temp(v) for v in raw_gpu]
        gpu_temp = max([t for t in temps if t != TEMP_READ_ERROR_VALUE], default=TEMP_READ_ERROR_VALUE)
        # EC reports 0 for both GPU sensors exactly when the dGPU is powered down (Advanced Optimus);
        # that is a sleep state, not a sensor failure. Unparseable raw values keep gpu_asleep False.
        gpu_asleep = gpu_temp == TEMP_READ_ERROR_VALUE and bool(raw_gpu) and all(self._to_int(v) == 0 for v in raw_gpu)
        # Latch: while asleep, skip the two GPU getters for the next 19 sweeps
        # (~12 s visible / ~20 s hidden wake-detection latency).
        self._gpu_asleep_latched = gpu_asleep
        self._gpu_sleep_probes_left = 19 if gpu_asleep else 0
        return {'cpu_temp': cpu_temp, 'gpu_temp': gpu_temp, 'gpu_asleep': gpu_asleep}

    def _poll_nvml_if_allowed(self, data: Dict[str, Any]) -> None:
        """Attach gpu_power_w/gpu_util when NVML may be polled.

        Measured on the reference machine: any NVML client activity pins an
        idle dGPU at P0 and keeps it out of D3cold. Three gates prevent
        that: the profile flag (_nvml_poll_enabled), the EC sleep heuristic,
        and the idle skip (stop reading once power sits at/below the
        feed-forward floor; resume on real load or a sleep/wake cycle)."""
        if not self._nvml_ok or not self._nvml_poll_enabled:
            return
        if data.get('gpu_asleep', False):
            # Powered down: never touch NVML; re-arm so the first reading
            # after wake is taken fresh.
            self._nvml_last_power_w = None
            self._nvml_idle_paused = False
            return
        gpu_temp = data.get('gpu_temp', TEMP_READ_ERROR_VALUE)
        if self._nvml_idle_paused:
            if gpu_temp == TEMP_READ_ERROR_VALUE or gpu_temp < NVML_FF_RESUME_TEMP_C:
                return  # still idle and cool: keep the GPU alone
            self._nvml_idle_paused = False  # genuine load: resume reading
        power_w, gpu_util = self._read_nvml_gpu()
        self._nvml_last_power_w = power_w
        # 31-34 W pinned idle is above the 30 W offset floor, so the skip
        # threshold is its own constant (see NVML_IDLE_SKIP_W).
        self._nvml_idle_paused = power_w is not None and power_w <= NVML_IDLE_SKIP_W
        if power_w is not None:
            data['gpu_power_w'] = power_w
        if gpu_util is not None:
            data['gpu_util'] = gpu_util

    def _get_core_sensors(self) -> Dict[str, Any]:
        data = self._get_temperatures()
        data['fan1_rpm'] = self._validate_rpm(self._execute_wmi_method(self._wmi_get_obj, GET_RPM1))
        data['fan2_rpm'] = self._validate_rpm(self._execute_wmi_method(self._wmi_get_obj, GET_RPM2))
        self._poll_nvml_if_allowed(data)
        return data

    def _get_charge_status(self) -> Dict[str, Any]:
        """Lightweight charge-only read for the periodic watch (2 getters
        instead of the full 12-getter sensor sweep)."""
        return {
            "charge_policy": self._read_optional_int(GET_CHARGE_POLICY),
            "charge_threshold": self._read_optional_int(GET_CHARGE_STOP),
        }

    def _get_all_sensors(self) -> Dict[str, Any]:
        try:
            data = self._get_core_sensors()
        except Exception:
            data = {}
        policy = self._read_optional_int(GET_CHARGE_POLICY)
        if policy >= 0:
            data["charge_policy"] = policy
        threshold = self._read_optional_int(GET_CHARGE_STOP)
        if threshold >= 0:
            data["charge_threshold"] = threshold
        data["ec_fixed_fan_speed_raw"] = self._read_optional_int(GET_FIXED_FAN_SPEED)
        data["battery_health"] = get_battery_health(self._read_optional_int(GET_BATTERY_HEALTH))
        cycles = [value for value in (
            self._read_optional_int(GET_BATT_CYC),
            self._read_optional_int(GET_BATT_CYC1),
        ) if value >= 0]
        data["battery_cycles"] = max(cycles) if cycles else -1
        data["fixed_fan_status"] = self._read_optional_int(GET_FIXED_FAN_STATUS)
        data["auto_fan_status"] = self._read_optional_int(GET_AUTO_FAN_STATUS)
        data["step_fan_status"] = self._read_optional_int(GET_STEP_FAN_STATUS)
        data["cpu_fan_duty"] = self._read_optional_int(GET_CPU_FAN_DUTY)
        data["gpu_fan_duty"] = self._read_optional_int(GET_GPU_FAN_DUTY)
        return data

class WMIInterface(QObject):
    """提供一个极简的、线程安全的WMI操作公共API。"""
    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._request_queue: Optional[queue.Queue] = None
        self._worker_thread: Optional[WMIWorker] = None
        self._retired_workers: List[WMIWorker] = []
        self._is_running = False
        self._initialization_error: Optional[Exception] = None
        # One shared 50 ms drain timer for all in-flight async results,
        # instead of a 20 ms QTimer per request.
        self._pending_results: list = []
        self._result_timer = QTimer(self)
        self._result_timer.setInterval(50)
        self._result_timer.timeout.connect(self._drain_results)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return (
                self._is_running
                and self._worker_thread is not None
                and self._worker_thread.is_alive()
            )

    def start(self, init_timeout: float = 15.0) -> bool:
        with self._lock:
            if self.is_running:
                return self._initialization_error is None
            if not _wmi_available:
                self._initialization_error = WMIConnectionError("WMI库未找到。")
                return False

            # Never reuse the queue of a worker that may still be hung in COM:
            # that is how a stale STOP token used to kill its replacement.
            self._initialization_error = None
            request_queue: queue.Queue = queue.Queue(maxsize=50)
            worker = WMIWorker(request_queue, DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS)
            self._request_queue = request_queue
            self._worker_thread = worker

        worker.start()
        worker.initialization_complete.wait(timeout=init_timeout)

        with self._lock:
            if worker.initialization_error or not worker.is_alive():
                self._initialization_error = worker.initialization_error or WMIConnectionError("WMI工作线程未能启动。")
                worker.request_stop()
                if self._worker_thread is worker:
                    self._worker_thread = None
                    self._request_queue = None
                    self._is_running = False
                return False
            self._is_running = True
            return True

    def set_nvml_poll_enabled(self, enabled: bool) -> None:
        """Mirror the active profile's feed-forward flag onto the worker;
        plain bool attribute write, read at the top of each sensor sweep."""
        worker = self._worker_thread
        if worker is not None:
            worker._nvml_poll_enabled = bool(enabled)

    def set_nvml_util_enabled(self, enabled: bool) -> None:
        """Mirror UI visibility onto the worker: no NVML utilization read
        while nothing displays it. Same plain attribute write as above."""
        worker = self._worker_thread
        if worker is not None:
            worker._nvml_util_enabled = bool(enabled)

    def stop(self, join_timeout: float = WMI_REQUEST_TIMEOUT_S):
        with self._lock:
            worker = self._worker_thread
            self._is_running = False
            self._worker_thread = None
            self._request_queue = None
        if not worker:
            return

        worker.request_stop()
        worker.join(timeout=join_timeout)
        if worker.is_alive():
            # The COM call is still stuck.  The stop event makes sure this
            # thread exits as soon as it returns and never processes another
            # request; the replacement owns a fresh queue.
            print(
                f"WMI worker did not stop within {join_timeout:.1f}s; retiring it.",
                file=sys.stderr,
            )
            with self._lock:
                self._retired_workers.append(worker)
                self._retired_workers = [w for w in self._retired_workers if w.is_alive()][-8:]

    def get_initialization_error(self) -> Optional[Exception]:
        return self._initialization_error

    def _current_queue(self) -> Optional[queue.Queue]:
        with self._lock:
            if not self.is_running:
                return None
            return self._request_queue

    def _execute_sync(self, method_or_signal: Any, params: Optional[Dict] = None, timeout: float = WMI_REQUEST_TIMEOUT_S) -> Any:
        request_queue = self._current_queue()
        if request_queue is None:
            raise self._initialization_error or WMIConnectionError("WMI interface is not running.")
        result_queue = queue.Queue(maxsize=1)
        request: WMIRequest = (method_or_signal, params or {}, result_queue)
        try:
            request_queue.put(request, block=True, timeout=1.0)
        except queue.Full:
            raise WMIRequestTimeoutError("WMI request queue is full.")
        try:
            result, exc = result_queue.get(block=True, timeout=timeout)
            if exc: raise WMICommandError(f"WMI method '{method_or_signal}' failed.", original_exception=exc)
            return result
        except queue.Empty:
            raise WMIRequestTimeoutError(f"WMI request '{method_or_signal}' timed out.")

    def submit_async(self, method_or_signal: Any, params: Optional[Dict] = None,
                     on_done: Optional[Callable[[Any, Optional[Exception]], None]] = None) -> bool:
        """Fire a WMI request without blocking the caller (GUI) thread.
        The callback fires exactly once, on the caller's thread, with (result, exception).
        A periodic QTimer drains the result queue - no cross-thread signal marshalling needed."""
        request_queue = self._current_queue()
        if request_queue is None:
            if on_done: on_done(None, self._initialization_error or WMIConnectionError("WMI interface is not running."))
            return True
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        try:
            request_queue.put_nowait((method_or_signal, params or {}, result_queue))
        except queue.Full:
            if on_done: on_done(None, WMIRequestTimeoutError("WMI request queue is full."))
            return True
        if on_done is None:
            return True
        deadline = time.monotonic() + WMI_ASYNC_RESULT_TIMEOUT_S
        self._pending_results.append(
            (result_queue, on_done, deadline, str(method_or_signal)))
        if not self._result_timer.isActive():
            self._result_timer.start()
        return True

    @Slot()
    def _drain_results(self):
        """Fire exactly one callback per completed request, on the GUI
        thread (the timer lives on WMIInterface, a GUI-thread QObject)."""
        now = time.monotonic()
        still = []
        for result_queue, on_done, deadline, label in self._pending_results:
            try:
                result, exc = result_queue.get_nowait()
            except queue.Empty:
                if now >= deadline:
                    on_done(None, WMIRequestTimeoutError(f"WMI request '{label}' timed out."))
                else:
                    still.append((result_queue, on_done, deadline, label))
                continue
            on_done(result, exc)
        self._pending_results = still
        if not still:
            self._result_timer.stop()

    # --- 公共API方法 ---
    def execute_method(self, method_name: str, **kwargs) -> Any:
        """通用的、阻塞式的WMI方法执行器；仅用于退出等必须同步的路径。"""
        if 'Data' in kwargs and isinstance(kwargs['Data'], (int, float)):
             kwargs['Data'] = float(kwargs['Data'])
        return self._execute_sync(method_name, kwargs)

    def get_latest_core_sensor_data(self) -> Dict[str, Any]:
        """非阻塞地从缓存获取最新的核心传感器数据（温度、转速）。"""
        with self._lock:
            worker = self._worker_thread
        if not worker: return {}
        return worker.get_latest_core_sensor_data()

    def get_all_sensors_sync(self) -> Dict[str, Any]:
        """阻塞式地获取所有传感器数据（包括电池）。"""
        return self._execute_sync("_get_all_sensors")

    def get_temperatures_sync(self) -> Dict[str, float]:
        """阻塞式地仅获取CPU和GPU温度。"""
        return self._execute_sync("_get_temperatures")

    def request_core_sensor_poll(self):
        """由AppServices调用的非阻塞方法，用于请求一次后台核心传感器轮询。"""
        request_queue = self._current_queue()
        if request_queue is None:
            return
        try:
            request_queue.put_nowait((WMIInternalSignal.POLL_CORE_SENSORS, {}, None))
        except queue.Full:
            # 队列满是正常情况，意味着后台繁忙，忽略本次请求即可
            pass
