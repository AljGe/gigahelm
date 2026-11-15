# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from core import battery_health


class BatteryHealthTests(unittest.TestCase):
    def setUp(self):
        battery_health._cache_value = -1
        battery_health._cache_until = 0.0

    def test_capacity_parser(self):
        self.assertEqual(
            battery_health._extract_capacity(
                "DESIGN CAPACITY 99,013 mWh FULL CHARGE CAPACITY 90,000 mWh",
                "DESIGN CAPACITY",
            ),
            99013,
        )

    def test_windows_capacity_wins_over_ec_code(self):
        with patch.object(battery_health, "_read_from_wmi", return_value=None), \
             patch.object(battery_health, "_read_from_powercfg", return_value=100):
            self.assertEqual(battery_health.get_battery_health(1), 100)

    def test_ec_percentage_is_used_when_windows_data_missing(self):
        with patch.object(battery_health, "_read_from_wmi", return_value=None), \
             patch.object(battery_health, "_read_from_powercfg", return_value=None):
            battery_health._cache_until = 0.0
            self.assertEqual(battery_health.get_battery_health(80), 80)

    def test_small_ec_code_is_not_rendered_as_one_percent(self):
        with patch.object(battery_health, "_read_from_wmi", return_value=None), \
             patch.object(battery_health, "_read_from_powercfg", return_value=None):
            battery_health._cache_until = 0.0
            self.assertEqual(battery_health.get_battery_health(1), -1)


if __name__ == "__main__":
    unittest.main()
