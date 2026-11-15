# -*- coding: utf-8 -*-
import unittest

from tools.task_scheduler import (
    build_startup_task_xml, build_watchdog_task_xml, _decode_schtasks_output,
)


class TaskXmlTests(unittest.TestCase):
    def test_watchdog_xml_is_a_repeating_highest_privilege_task(self):
        xml = build_watchdog_task_xml(r"C:\Tools\Gigahelm\Gigahelm.exe", "--revert-bios", r"C:\Tools\Gigahelm").decode("utf-16")
        self.assertIn("<Interval>PT1M</Interval>", xml)
        self.assertIn("<RunLevel>HighestAvailable</RunLevel>", xml)
        self.assertIn("--revert-bios", xml)
        self.assertIn("<Hidden>true</Hidden>", xml)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertNotIn("RestartOnFailure", xml)

    def test_startup_task_no_longer_restarts_after_crash(self):
        xml = build_startup_task_xml(r"C:\Tools\Gigahelm\Gigahelm.exe", "--minimized", r"C:\Tools\Gigahelm").decode("utf-16")
        self.assertIn("--minimized", xml)
        self.assertNotIn("RestartOnFailure", xml)
        self.assertIn("<RunLevel>HighestAvailable</RunLevel>", xml)

    def test_schtasks_output_decodes_utf16_and_console_text(self):
        xml_bytes = '<Task version="1.4"></Task>'.encode("utf-16")
        self.assertIn("<Task", _decode_schtasks_output(xml_bytes))
        self.assertIn("TaskName", _decode_schtasks_output(b"TaskName: test"))


if __name__ == "__main__":
    unittest.main()
