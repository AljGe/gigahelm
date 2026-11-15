# -*- coding: utf-8 -*-
import unittest

from core.fan_duty import percent_to_raw


class FanDutyTests(unittest.TestCase):
    def test_locked_outputs(self):
        self.assertEqual(percent_to_raw(0), 0)
        self.assertEqual(percent_to_raw(1), 57)
        self.assertEqual(percent_to_raw(15), 57)
        self.assertEqual(percent_to_raw(25), 57)
        self.assertEqual(percent_to_raw(40), 91)
        self.assertEqual(percent_to_raw(80), 182)
        self.assertEqual(percent_to_raw(100), 227)


if __name__ == "__main__":
    unittest.main()
