# -*- coding: utf-8 -*-
"""apply_park.py guards the user's installed profile curves.

A regression here silently rewrites real fan tables (the app's whistle fix),
so the transform rules get the same pinning as core logic: sub-30 spin
points move to the stable beat-free park, zero plateaus tighten spin-onset
knees, and an already-tight knee stays untouched."""
import sys
import unittest
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1] / "tools" / "bench"
sys.path.insert(0, str(BENCH_DIR))

import apply_park  # noqa: E402


def transform(table):
    """Mirror of main()'s per-table rules (bump + plateau insert)."""
    out = [list(pt) for pt in table]
    for pt in out:
        if 0 < pt[1] < apply_park.NEW_PARK:
            pt[1] = apply_park.NEW_PARK
    ins = apply_park.plateau_point(out, apply_park.NEW_PARK)
    if ins is not None and not any(p[0] == ins[0] for p in out):
        out.append(list(ins))
        out.sort(key=lambda p: p[0])
    return out


class PlateauTests(unittest.TestCase):
    def test_gap_knee_gets_plateau(self):
        self.assertEqual(apply_park.plateau_point(
            [[50, 0], [60, 30]], 30), [58, 0])

    def test_tight_knee_untouched(self):
        self.assertIsNone(apply_park.plateau_point(
            [[50, 0], [55, 0], [60, 0], [65, 0], [68, 30]], 30))

    def test_nearest_zero_counts_not_the_first(self):
        # AC-shaped table: the first zero (50) is far below, the relevant
        # one (65) is tight against the knee.
        self.assertIsNone(apply_park.plateau_point(
            [[50, 0], [55, 0], [60, 0], [65, 0], [68, 30]], 30))

    def test_no_zeros_below_knee(self):
        self.assertIsNone(apply_park.plateau_point([[68, 30]], 30))


class TransformTests(unittest.TestCase):
    def test_silent_profile_shape(self):
        self.assertEqual(
            transform([[40, 0], [55, 0], [65, 25], [73, 35]]),
            [[40, 0], [55, 0], [63, 0], [65, 30], [73, 35]])

    def test_gaming_cpu_shape(self):
        self.assertEqual(
            transform([[50, 0], [60, 25], [70, 29], [80, 55]]),
            [[50, 0], [58, 0], [60, 30], [70, 30], [80, 55]])

    def test_all_fiction_values_normalized(self):
        # 1-29 all collapse onto the 30 park; the onset at 50 also gains
        # its plateau (first spin [50,30], nearest zero [40,0], gap 10).
        self.assertEqual(
            transform([[40, 0], [50, 12], [60, 25], [70, 29], [80, 31]]),
            [[40, 0], [48, 0], [50, 30], [60, 30], [70, 30], [80, 31]])

    def test_already_parked_table_untouched(self):
        table = [[40, 0], [55, 0], [63, 0], [65, 30], [73, 35]]
        self.assertEqual(transform(table), table)


if __name__ == "__main__":
    unittest.main()
