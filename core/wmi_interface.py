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
from typing import Optional, Any, Dict, Tuple, Callable

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

from config.settings import (
    WMI_NAMESPACE, DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS,
    GET_CPU_TEMP, GET_GPU_TEMP1, GET_GPU_TEMP2,
    GET_RPM1, GET_RPM2, GET_CHARGE_POLICY, GET_CHARGE_STOP,
    GET_FIXED_FAN_STATUS, GET_AUTO_FAN_STATUS, GET_STEP_FAN_STATUS,
    GET_FIXED_FAN_SPEED, GET_CPU_FAN_DUTY, GET_GPU_FAN_DUTY,
    GET_BATTERY_HEALTH, GET_BATT_CYC, GET_BATT_CYC1,
    SET_CUSTOM_FAN_STATUS, SET_SUPER_QUIET, SET_AUTO_FAN_STATUS,
    SET_STEP_FAN_STATUS, SET_CUSTOM_FAN_SPEED, SET_GPU_FAN_DUTY,
    WMI_REQUEST_TIMEOUT_S, TEMP_READ_ERROR_VALUE, RPM_READ_ERROR_VALUE,
    CHARGE_POLICY_READ_ERROR_VALUE, CHARGE_THRESHOLD_READ_ERROR_VALUE,
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
    """后台线程，处理所有阻塞的WMI调用。"""
    def __init__(self, request_queue: queue.Queue, wmi_get_class: str, wmi_set_class: str):
        super().__init__(name="WMIWorkerThread", daemon=True)
        self._request_queue = request_queue
        self._wmi_get_class_name = wmi_get_class
        self._wmi_set_class_name = wmi_set_class
        self._wmi_get_obj: Optional[Any] = None
        self._wmi_set_obj: Optional[Any] = None
        self._com_initialized: bool = False
        self.initialization_error: Optional[Exception] = None
        self.initialization_complete = threading.Event()
        self._latest_core_sensor_data: Dict[str, Any] = {}
        self._data_lock = threading.Lock()

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

    def _cleanup_com(self):
        if self._com_initialized and pythoncom:
            try: pythoncom.CoUninitialize()
            except Exception: pass
            self._com_initialized = False

    def run(self):
        """WMI工作线程的主循环，仅处理请求队列。"""
        if not self._init_wmi():
            self.initialization_complete.set()
            return
        self.initialization_complete.set()

        while True:
            request = self._request_queue.get()
            if request[0] is WMIInternalSignal.STOP:
                break
            self._process_request(request)
        
        self._cleanup_com()

    def get_latest_core_sensor_data(self) -> Dict[str, Any]:
        with self._data_lock:
            return self._latest_core_sensor_data.copy()

    def _process_request(self, request: WMIRequest):
        """处理单个请求，智能选择WMI对象并调用核心执行器。"""
        method_or_signal, params, callback_queue = request
        result, exception = None, None
        try:
            if method_or_signal is WMIInternalSignal.POLL_CORE_SENSORS:
                result = self._get_core_sensors()
                with self._data_lock: self._latest_core_sensor_data = result
            elif method_or_signal is WMIInternalSignal.APPLY_FAN_CONTROL:
                result = self._apply_fan_control(
                    float(params.get("duty_raw", 0.0)),
                    bool(params.get("reassert_flags", True)),
                )
            elif method_or_signal == "_get_all_sensors":
                result = self._get_all_sensors()
            elif method_or_signal == "_get_temperatures":
                result = self._get_temperatures()
            elif isinstance(method_or_signal, str):
                method_name = method_or_signal
                target_obj = self._wmi_set_obj if method_name.startswith("Set") else self._wmi_get_obj
                result = self._execute_wmi_method(target_obj, method_name, **params)
            else:
                exception = ValueError("无效的WMI请求类型")
        except Exception as e:
            exception = e
        
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
        try:
            raw = int(value) & 0xFFFF
        except (ValueError, TypeError):
            return RPM_READ_ERROR_VALUE
        swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
        if 0 <= swapped <= 8000:
            chosen = swapped
        elif 0 <= raw <= 8000:
            chosen = raw
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

    def _get_temperatures(self) -> Dict[str, Any]:
        cpu_temp = self._validate_temp(self._execute_wmi_method(self._wmi_get_obj, GET_CPU_TEMP))
        raw_gpu = [self._execute_wmi_method(self._wmi_get_obj, m) for m in [GET_GPU_TEMP1, GET_GPU_TEMP2] if hasattr(self._wmi_get_obj, m)]
        temps = [self._validate_temp(v) for v in raw_gpu]
        gpu_temp = max([t for t in temps if t != TEMP_READ_ERROR_VALUE], default=TEMP_READ_ERROR_VALUE)
        # EC reports 0 for both GPU sensors exactly when the dGPU is powered down (Advanced Optimus);
        # that is a sleep state, not a sensor failure. Unparseable raw values keep gpu_asleep False.
        gpu_asleep = gpu_temp == TEMP_READ_ERROR_VALUE and bool(raw_gpu) and all(self._to_int(v) == 0 for v in raw_gpu)
        return {'cpu_temp': cpu_temp, 'gpu_temp': gpu_temp, 'gpu_asleep': gpu_asleep}

    def _get_core_sensors(self) -> Dict[str, Any]:
        data = self._get_temperatures()
        data['fan1_rpm'] = self._validate_rpm(self._execute_wmi_method(self._wmi_get_obj, GET_RPM1))
        data['fan2_rpm'] = self._validate_rpm(self._execute_wmi_method(self._wmi_get_obj, GET_RPM2))
        return data

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
        data["battery_health"] = self._read_optional_int(GET_BATTERY_HEALTH)
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
        self._request_queue = queue.Queue(maxsize=50)
        self._worker_thread: Optional[WMIWorker] = None
        self._is_running = False
        self._initialization_error: Optional[Exception] = None
        # 不再需要内部定时器

    @property
    def is_running(self) -> bool:
        return self._is_running and self._worker_thread is not None and self._worker_thread.is_alive()

    def start(self, init_timeout: float = 15.0) -> bool:
        if self.is_running: return self._initialization_error is None
        if not _wmi_available:
            self._initialization_error = WMIConnectionError("WMI库未找到。")
            return False

        self._initialization_error = None
        self._worker_thread = WMIWorker(self._request_queue, DEFAULT_WMI_GET_CLASS, DEFAULT_WMI_SET_CLASS)
        self._worker_thread.start()
        
        self._worker_thread.initialization_complete.wait(timeout=init_timeout)
        if self._worker_thread.initialization_error or not self._worker_thread.is_alive():
            self._initialization_error = self._worker_thread.initialization_error or WMIConnectionError("WMI工作线程未能启动。")
            self._worker_thread = None
            return False
        
        self._is_running = True
        return True

    def stop(self, join_timeout: float = WMI_REQUEST_TIMEOUT_S):
        if not self.is_running or not self._worker_thread: return
        self._is_running = False
        self._request_queue.put((WMIInternalSignal.STOP, {}, None))
        self._worker_thread.join(timeout=join_timeout)
        self._worker_thread = None

    def get_initialization_error(self) -> Optional[Exception]:
        return self._initialization_error

    def _execute_sync(self, method_or_signal: Any, params: Optional[Dict] = None, timeout: float = WMI_REQUEST_TIMEOUT_S) -> Any:
        if not self.is_running: raise self._initialization_error or WMIConnectionError("WMI interface is not running.")
        result_queue = queue.Queue(maxsize=1)
        request: WMIRequest = (method_or_signal, params or {}, result_queue)
        try:
            self._request_queue.put(request, block=True, timeout=1.0)
        except queue.Full:
            raise WMIRequestTimeoutError(f"WMI request queue is full.")
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
        if not self.is_running:
            if on_done: on_done(None, self._initialization_error or WMIConnectionError("WMI interface is not running."))
            return True
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        try:
            self._request_queue.put_nowait((method_or_signal, params or {}, result_queue))
        except queue.Full:
            if on_done: on_done(None, WMIRequestTimeoutError("WMI request queue is full."))
            return True
        if on_done is None:
            return True
        timer = QTimer(self)
        timer.setInterval(20)
        def _poll():
            try:
                result, exc = result_queue.get_nowait()
            except queue.Empty:
                return
            timer.stop()
            timer.deleteLater()
            on_done(result, exc)
        timer.timeout.connect(_poll)
        timer.start()
        return True

    # --- 公共API方法 ---
    def execute_method(self, method_name: str, **kwargs) -> Any:
        """通用的、阻塞式的WMI方法执行器，主要用于Setters。"""
        if 'Data' in kwargs and isinstance(kwargs['Data'], (int, float)):
             kwargs['Data'] = float(kwargs['Data'])
        return self._execute_sync(method_name, kwargs)

    def get_latest_core_sensor_data(self) -> Dict[str, Any]:
        """非阻塞地从缓存获取最新的核心传感器数据（温度、转速）。"""
        if not self.is_running or not self._worker_thread: return {}
        return self._worker_thread.get_latest_core_sensor_data()

    def get_all_sensors_sync(self) -> Dict[str, Any]:
        """阻塞式地获取所有传感器数据（包括电池）。"""
        return self._execute_sync("_get_all_sensors")

    def get_temperatures_sync(self) -> Dict[str, float]:
        """阻塞式地仅获取CPU和GPU温度。"""
        return self._execute_sync("_get_temperatures")

    def request_core_sensor_poll(self):
        """
        由AppServices调用的非阻塞方法，用于请求一次后台核心传感器轮询。
        """
        if self.is_running:
            try:
                self._request_queue.put_nowait((WMIInternalSignal.POLL_CORE_SENSORS, {}, None))
            except queue.Full:
                # 队列满是正常情况，意味着后台繁忙，忽略本次请求即可
                pass