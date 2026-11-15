# -*- coding: utf-8 -*-
"""Model preset resolution and parameterized duty math."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.models import MODEL_PRESETS, DEFAULT_MODEL, get_preset, resolved_model_name
from core.fan_duty import percent_to_raw


class ModelPresetTests(unittest.TestCase):
    def test_known_model_resolves(self):
        self.assertEqual(get_preset("AORUS 15P YD"), {"fan_raw_max": 227, "fan_percent_min_spin": 25})

    def test_unknown_model_falls_back_with_note(self):
        self.assertEqual(get_preset("Some Other Laptop"), MODEL_PRESETS[DEFAULT_MODEL])
        self.assertIn("default", resolved_model_name("Some Other Laptop"))

    def test_all_presets_have_required_keys(self):
        for name, preset in MODEL_PRESETS.items():
            self.assertIn("fan_raw_max", preset, name)
            self.assertIn("fan_percent_min_spin", preset, name)
            self.assertTrue(100 < preset["fan_raw_max"] <= 255, name)


class PercentToRawTests(unittest.TestCase):
    def test_defaults_match_yd(self):
        self.assertEqual(percent_to_raw(0), 0)
        self.assertEqual(percent_to_raw(100), 227)
        self.assertEqual(percent_to_raw(40), 91)
        self.assertEqual(percent_to_raw(10), 57)   # spin floor
        self.assertEqual(percent_to_raw(25), 57)

    def test_custom_preset_changes_range(self):
        self.assertEqual(percent_to_raw(100, fan_raw_max=200), 200)
        self.assertEqual(percent_to_raw(10, fan_percent_min_spin=20), 46)  # ceil(20/100*227)
        self.assertEqual(percent_to_raw(0, fan_raw_max=200), 0)


if __name__ == "__main__":
    unittest.main()
