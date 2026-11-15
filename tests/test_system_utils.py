# -*- coding: utf-8 -*-
import os
import unittest


class PriorityHelpersTests(unittest.TestCase):
    """On Windows both helpers must actually apply (return True)."""

    def test_process_priority_applies_on_windows(self):
        if os.name != 'nt':
            self.skipTest("windows only")
        from tools.system_utils import set_process_priority_above_normal
        self.assertTrue(set_process_priority_above_normal())

    def test_thread_priority_applies_on_windows(self):
        if os.name != 'nt':
            self.skipTest("windows only")
        from tools.system_utils import set_thread_priority_above_normal
        self.assertTrue(set_thread_priority_above_normal())


if __name__ == "__main__":
    unittest.main()
