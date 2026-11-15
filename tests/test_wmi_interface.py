# -*- coding: utf-8 -*-
import threading
import unittest
from unittest.mock import patch

import core.wmi_interface as wmi_module


class FakeWorker:
    """Minimal stand-in that can simulate a worker stuck in a COM call."""

    def __init__(self, request_queue, _get_class, _set_class):
        self.request_queue = request_queue
        self.initialization_complete = threading.Event()
        self.initialization_complete.set()
        self.initialization_error = None
        self.stop_requested = False
        self.alive = True
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def request_stop(self):
        self.stop_requested = True
        self.request_queue.put((wmi_module.WMIInternalSignal.STOP, {}, None))

    def join(self, timeout=None):
        return None

    def get_latest_core_sensor_data(self):
        return {}


class WmiWorkerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.workers = []

        def factory(queue, get_class, set_class):
            worker = FakeWorker(queue, get_class, set_class)
            self.workers.append(worker)
            return worker

        self.factory_patch = patch.object(wmi_module, "WMIWorker", side_effect=factory)
        self.available_patch = patch.object(wmi_module, "_wmi_available", True)
        self.factory_patch.start()
        self.available_patch.start()
        self.interface = wmi_module.WMIInterface()

    def tearDown(self):
        self.factory_patch.stop()
        self.available_patch.stop()

    def test_replacement_gets_a_fresh_queue_after_hung_stop(self):
        self.assertTrue(self.interface.start(init_timeout=0.1))
        old_worker = self.workers[0]
        old_queue = old_worker.request_queue

        # The old worker "hangs": stop() times out and retires it.
        self.interface.stop(join_timeout=0.01)
        self.assertTrue(old_worker.stop_requested)

        self.assertTrue(self.interface.start(init_timeout=0.1))
        new_worker = self.workers[1]
        self.assertIsNot(new_worker.request_queue, old_queue)
        self.assertTrue(new_worker.request_queue.empty())

        # The stale STOP sentinel is still in the retired queue and must never
        # be visible to the replacement.
        self.assertIs(old_queue.get_nowait()[0], wmi_module.WMIInternalSignal.STOP)
        self.assertTrue(new_worker.request_queue.empty())

    def test_core_poll_goes_to_current_worker(self):
        self.assertTrue(self.interface.start(init_timeout=0.1))
        self.interface.request_core_sensor_poll()
        method, _params, _callback = self.workers[0].request_queue.get_nowait()
        self.assertIs(method, wmi_module.WMIInternalSignal.POLL_CORE_SENSORS)


class ValidateRpmTests(unittest.TestCase):
    """App decode must match tools/bench/ec_probe.decode_rpm semantics."""

    @classmethod
    def setUpClass(cls):
        cls.worker = wmi_module.WMIWorker(__import__("queue").Queue(),
                                          "GB_WMIACPI_Get", "GB_WMIACPI_Set")

    def test_plan_cases(self):
        self.assertEqual(self.worker._validate_rpm(44042), 2732)
        self.assertEqual(self.worker._validate_rpm(50451), 5061)
        self.assertEqual(self.worker._validate_rpm(229), 229)
        self.assertEqual(self.worker._validate_rpm(12), 0)
        self.assertEqual(self.worker._validate_rpm(0), 0)

    def test_garbage(self):
        self.assertEqual(self.worker._validate_rpm(65535), -1)
        self.assertEqual(self.worker._validate_rpm(-1), -1)
        self.assertEqual(self.worker._validate_rpm("abc"), -1)
        self.assertEqual(self.worker._validate_rpm(None), -1)


class BoundedCallTests(unittest.TestCase):
    """_process_request must fail fast instead of hanging on a stuck EC."""

    def setUp(self):
        self.worker = wmi_module.WMIWorker(__import__("queue").Queue(),
                                           "GB_WMIACPI_Get", "GB_WMIACPI_Set")
        patcher = patch.object(wmi_module, "WMI_REQUEST_TIMEOUT_S", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _request(self, params=None):
        import queue
        callback = queue.Queue()
        self.worker._process_request(("_get_core_sensors", params or {},
                                      callback))
        return callback.get_nowait()

    def test_stuck_call_times_out(self):
        def stuck(**_kwargs):
            import time
            time.sleep(2.0)
        with patch.object(self.worker, "_get_core_sensors", stuck):
            result, exception = self._request()
        self.assertIsNone(result)
        self.assertIsInstance(exception, wmi_module.WMIRequestTimeoutError)

    def test_worker_stays_responsive_after_timeout(self):
        def stuck(**_kwargs):
            import time
            time.sleep(2.0)
        with patch.object(self.worker, "_get_core_sensors", stuck):
            result, exception = self._request()
        self.assertIsInstance(exception, wmi_module.WMIRequestTimeoutError)
        # next request while the abandoned call is pending: immediate timeout
        result, exception = self._request()
        self.assertIsNone(result)
        self.assertIsInstance(exception, wmi_module.WMIRequestTimeoutError)

    def test_fast_call_returns_result(self):
        with patch.object(self.worker, "_get_core_sensors",
                          lambda **kw: {"cpu_temp": 55.0}):
            result, exception = self._request()
        self.assertIsNone(exception)
        self.assertEqual(result["cpu_temp"], 55.0)


if __name__ == "__main__":
    unittest.main()
