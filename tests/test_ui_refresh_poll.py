# -*- coding: utf-8 -*-
"""UI refresh adaptive poll.

The footer's UI-refresh setting (250 ms - 2 s) must drive the *visible*
sensor cadence. In auto/custom the EC control cycle decides at its own
controller_update_interval_ms (1.5 s default) and never feeds the display
cache, so on_gui_tick used to re-push stale data; now it requests a
read-only sweep whenever the cache is older than the UI interval.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from core.path_manager import PathManager  # noqa: E402
from core.state import AppState  # noqa: E402
from core.app_services import AppServices  # noqa: E402
from config.settings import FAN_MODE_AUTO, FAN_MODE_BIOS  # noqa: E402


class _PollWMI:
    """Minimal double covering exactly what on_gui_tick touches."""

    def __init__(self, age_ms: float = float("inf")):
        self.is_running = True
        self.age_ms = age_ms
        self.poll_requests = 0

    def request_core_sensor_poll(self):
        self.poll_requests += 1

    def get_core_sensor_age_ms(self):
        return self.age_ms

    def get_latest_core_sensor_data(self):
        return {}


class AdaptivePollTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState(path_manager=PathManager(
            base_dir=".", executable_path="x", main_script_path=""))
        self.svc = AppServices(state=self.state)
        self.wmi = _PollWMI()
        self.svc.wmi_interface = self.wmi
        self.svc._is_ui_visible = True
        self.state.set_gui_update_interval_ms(250)
        self.state.set_applied_fan_mode(FAN_MODE_AUTO)

    def test_stale_cache_in_auto_mode_requests_poll(self):
        self.wmi.age_ms = 10_000.0
        self.svc.on_gui_tick()
        self.assertEqual(self.wmi.poll_requests, 1)

    def test_fresh_cache_in_auto_mode_skips_poll(self):
        self.wmi.age_ms = 100.0  # fresher than the 250 ms UI cadence
        self.svc.on_gui_tick()
        self.assertEqual(self.wmi.poll_requests, 0)

    def test_never_swept_cache_requests_poll(self):
        self.wmi.age_ms = float("inf")
        self.svc.on_gui_tick()
        self.assertEqual(self.wmi.poll_requests, 1)

    def test_bios_mode_polls_regardless_of_age(self):
        self.state.set_applied_fan_mode(FAN_MODE_BIOS)
        self.wmi.age_ms = 0.0
        self.svc.on_gui_tick()
        self.assertEqual(self.wmi.poll_requests, 1)

    def test_hidden_ui_never_polls(self):
        self.svc._is_ui_visible = False
        self.wmi.age_ms = 10_000.0
        self.svc.on_gui_tick()
        self.assertEqual(self.wmi.poll_requests, 0)


if __name__ == "__main__":
    unittest.main()
