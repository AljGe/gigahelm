# -*- coding: utf-8 -*-
"""EC read validation: temp/RPM plausibility and the GPU-asleep rule
(both GPU sensors returning exactly 0 = dGPU powered down, not a failure)."""
import os
import queue
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.wmi_interface import WMIWorker
from config.settings import TEMP_READ_ERROR_VALUE


class StubGetter:
    def __init__(self, cpu=None, gpu1=None, gpu2=None, raise_gpu=False):
        self.cpu, self.gpu1, self.gpu2, self.raise_gpu = cpu, gpu1, gpu2, raise_gpu

    def getCpuTemp(self): return (self.cpu,)
    def getGpuTemp1(self):
        if self.raise_gpu:
            raise OSError("COM exploded")
        return (self.gpu1,)
    def getGpuTemp2(self): return (self.gpu2,)


def read_temps(cpu, gpu1, gpu2, raise_gpu=False):
    worker = WMIWorker(queue.Queue(), "GB_WMIACPI_Get", "GB_WMIACPI_Set")
    worker._wmi_get_obj = StubGetter(cpu, gpu1, gpu2, raise_gpu)
    return worker._get_temperatures()


class TemperatureValidationTests(unittest.TestCase):
    def test_both_gpu_zeros_mean_asleep(self):
        self.assertEqual(read_temps(55, 0, 0),
                         {"cpu_temp": 55.0, "gpu_temp": TEMP_READ_ERROR_VALUE, "gpu_asleep": True})

    def test_awake_gpu_reads_normally(self):
        self.assertEqual(read_temps(56, 46, 0),
                         {"cpu_temp": 56.0, "gpu_temp": 46.0, "gpu_asleep": False})

    def test_partial_gpu_read_is_not_asleep(self):
        result = read_temps(55, 0, 30)
        self.assertEqual(result["gpu_temp"], 30.0)
        self.assertFalse(result["gpu_asleep"])

    def test_garbage_gpu_value_is_not_asleep(self):
        result = read_temps(55, "x", 0)
        self.assertEqual(result["gpu_temp"], TEMP_READ_ERROR_VALUE)
        self.assertFalse(result["gpu_asleep"])

    def test_sensor_exception_propagates(self):
        with self.assertRaises(OSError):
            read_temps(55, 0, 0, raise_gpu=True)


class RpmValidationTests(unittest.TestCase):
    def setUp(self):
        self.worker = WMIWorker(queue.Queue(), "GB_WMIACPI_Get", "GB_WMIACPI_Set")

    def test_swap_preferred_when_in_range(self):
        # 0x0109 byte-swaps to 0x0901 (2305 RPM), both plausible -> heuristic picks the swap
        self.assertEqual(self.worker._validate_rpm(0x0109), 0x0901)

    def test_raw_fallback_when_swap_implausible(self):
        # 0x09A6 swaps to 0xA609 (42497) which exceeds 8000 -> raw 2470 wins
        self.assertEqual(self.worker._validate_rpm(0x09A6), 2470)

    def test_zero_is_zero(self):
        self.assertEqual(self.worker._validate_rpm(0), 0)

    def test_small_raw_gets_swapped_up(self):
        # heuristic prefers the byte-swapped reading: raw 30 -> 0x1E00 = 7680
        self.assertEqual(self.worker._validate_rpm(30), 7680)

    def test_implausible_is_error(self):
        self.assertEqual(self.worker._validate_rpm(0xFFFF), -1)

    def test_garbage_is_error(self):
        self.assertEqual(self.worker._validate_rpm("n/a"), -1)

    def test_to_int(self):
        self.assertEqual(self.worker._to_int("42"), 42)
        self.assertIsNone(self.worker._to_int("x"))
        self.assertIsNone(self.worker._to_int(None))


if __name__ == "__main__":
    unittest.main()
