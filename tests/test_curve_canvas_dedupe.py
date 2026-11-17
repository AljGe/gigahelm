# -*- coding: utf-8 -*-
"""Curve canvas duplicate-point regression.

Boundary drags and double-click adds used to create same-temperature
points: two stacked dots on the canvas plus a zero-x-gap PCHIP failure
(ZeroDivisionError -> silent piecewise-linear fallback for the curve).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.qt import QApplication, QPointF, QRectF  # noqa: E402

# Widget construction needs an event dispatcher; CI runs offscreen.
_APP = QApplication.instance() or QApplication([])

from core.state import AppState  # noqa: E402
from core.path_manager import PathManager  # noqa: E402
from gui.theme import ThemeManager  # noqa: E402
from gui.lightweight_curve_canvas import LightweightCurveCanvas  # noqa: E402
from config.settings import MAX_TEMP_C, DEFAULT_MIN_DISPLAY_TEMP_C  # noqa: E402


class _StubProfileManager:
    """Canvas persistence sink; no disk, no timers. set_curve_data loops back
    through the canvas handler exactly like ProfileManager does in
    production (profile signal -> _on_*_curve_data_changed -> revalidate)."""

    def __init__(self):
        self.saved = []
        self.canvas = None

    def set_curve_data(self, curve_type, data):
        self.saved.append((curve_type, [list(p) for p in data]))
        if self.canvas is not None:
            handler = (self.canvas._on_cpu_curve_data_changed if curve_type == 'cpu'
                       else self.canvas._on_gpu_curve_data_changed)
            handler(data)


def _make_canvas(min_temp: int = DEFAULT_MIN_DISPLAY_TEMP_C):
    state = AppState(path_manager=PathManager(
        base_dir=".", executable_path="x", main_script_path=""))
    theme = ThemeManager(state, None)
    stub = _StubProfileManager()
    canvas = LightweightCurveCanvas(state, stub, theme)
    stub.canvas = canvas
    canvas._appearance_settings = {"min_display_temp_c": min_temp}
    return canvas


def _strictly_increasing(table):
    temps = [p[0] for p in table]
    return all(a < b for a, b in zip(temps, temps[1:]))


class ValidateDedupeTests(unittest.TestCase):
    def test_merges_duplicate_temps_keeping_higher_speed(self):
        canvas = _make_canvas()
        merged = canvas._validate_and_sort([[60, 20], [60, 35], [70, 50]])
        self.assertEqual(merged, [[60, 35], [70, 50]])

    def test_merges_boundary_collapse_pair(self):
        # The old drag chain clamped two points onto min_temp.
        canvas = _make_canvas()
        merged = canvas._validate_and_sort([[40, 0], [40, 15], [55, 30]])
        self.assertEqual(merged, [[40, 15], [55, 30]])

    def test_merges_max_boundary_pair(self):
        canvas = _make_canvas()
        merged = canvas._validate_and_sort([[90, 60], [100, 80], [100, 100]])
        self.assertEqual(merged, [[90, 60], [100, 100]])

    def test_result_always_strictly_increasing(self):
        canvas = _make_canvas()
        for table in ([[50, 0], [50, 10], [50, 20]],
                      [[40, 0], [40, 0], [100, 100], [100, 50]],
                      [[60, 10], [60, 10]]):
            self.assertTrue(_strictly_increasing(canvas._validate_and_sort(table)))


class DragClampTests(unittest.TestCase):
    def setUp(self):
        self.canvas = _make_canvas()
        self.canvas.active_curve_type = 'cpu'
        self.canvas._plot_area = QRectF(0, 0, 600, 300)
        self.canvas._update_plot_area = lambda: None  # keep the fixed rect

    def _drag_to_temp(self, table, index, target_temp, target_speed=50):
        self.canvas.cpu_curve_data = [list(p) for p in table]
        self.canvas._dragging_point_index = index
        self.canvas._drag_point(self.canvas._data_to_widget_coords(target_temp, target_speed))
        self.canvas._dragging_point_index = None
        return self.canvas.cpu_curve_data

    def test_drag_into_left_boundary_does_not_stack_points(self):
        # Old behaviour: dragging 60 -> 40 pushed the pinned 40 °C neighbour
        # nowhere and left BOTH points at 40 °C.
        result = self._drag_to_temp([[40, 0], [60, 25], [80, 50]], 1, 40)
        self.assertTrue(_strictly_increasing(result), result)
        self.assertEqual(result[0][0], DEFAULT_MIN_DISPLAY_TEMP_C)
        self.assertEqual(result[1][0], DEFAULT_MIN_DISPLAY_TEMP_C + 1)

    def test_drag_into_right_boundary_does_not_stack_points(self):
        result = self._drag_to_temp([[50, 0], [100, 100]], 0, MAX_TEMP_C)
        self.assertTrue(_strictly_increasing(result), result)
        self.assertEqual(result[0][0], MAX_TEMP_C - 1)
        self.assertEqual(result[1][0], MAX_TEMP_C)

    def test_drag_overtake_stops_at_neighbour(self):
        # The old push chain teleported the overtaken 70 °C point onto 71 °C.
        result = self._drag_to_temp([[50, 0], [70, 30], [90, 60]], 0, 71)
        self.assertEqual([p[0] for p in result], [69, 70, 90])
        self.assertTrue(_strictly_increasing(result), result)

    def test_free_drag_still_works(self):
        result = self._drag_to_temp([[50, 0], [70, 30], [90, 60]], 1, 60)
        self.assertEqual([p[0] for p in result], [50, 60, 90])


class AddPointSnapTests(unittest.TestCase):
    def setUp(self):
        self.canvas = _make_canvas()
        self.canvas.active_curve_type = 'cpu'
        self.canvas._plot_area = QRectF(0, 0, 600, 300)
        self.canvas._update_plot_area = lambda: None

    def _add_at_temp(self, table, target_temp, target_speed=40):
        self.canvas.cpu_curve_data = [list(p) for p in table]
        self.canvas._add_point(self.canvas._data_to_widget_coords(target_temp, target_speed))
        return self.canvas.cpu_curve_data

    def test_add_onto_existing_temp_snaps_to_free_slot(self):
        result = self._add_at_temp([[60, 20], [80, 50]], 60)
        temps = sorted(p[0] for p in result)
        self.assertEqual(temps, [59, 60, 80])  # nearest free slot wins

    def test_add_at_free_temp_is_exact(self):
        result = self._add_at_temp([[60, 20], [80, 50]], 70)
        self.assertEqual(sorted(p[0] for p in result), [60, 70, 80])

    def test_added_points_are_strictly_increasing(self):
        result = self._add_at_temp([[40, 0], [41, 5], [42, 10]], 41)
        self.assertTrue(_strictly_increasing(result), result)

    def test_interpolator_survives_after_edits(self):
        # The exact regression: same-temperature points made PchipInterpolator
        # raise and silently degraded the curve to piecewise linear.
        canvas = _make_canvas()
        canvas.cpu_curve_data = canvas._validate_and_sort([[60, 20], [60, 35], [80, 50]])
        interpolator = canvas._get_cached_interpolator('cpu')
        self.assertIsNotNone(interpolator)
        self.assertAlmostEqual(interpolator(60), 35)


if __name__ == "__main__":
    unittest.main()
