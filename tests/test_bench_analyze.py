# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1] / "tools" / "bench"
sys.path.insert(0, str(BENCH_DIR))

import analyze  # noqa: E402
import ec_probe  # noqa: E402


class DecodeRpmTests(unittest.TestCase):
    """decode_rpm must match the plan's verified EC decode semantics."""

    def test_plan_cases(self):
        self.assertEqual(ec_probe.decode_rpm(44042), 2732)  # 0xAC0A -> 0x0AAC
        self.assertEqual(ec_probe.decode_rpm(50451), 5061)  # 0xC513 -> 0x13C5
        self.assertEqual(ec_probe.decode_rpm(229), 229)     # plausible raw stays
        self.assertEqual(ec_probe.decode_rpm(12), 0)        # <= 50 -> stopped
        self.assertEqual(ec_probe.decode_rpm(0), 0)

    def test_garbage(self):
        self.assertEqual(ec_probe.decode_rpm(65535), -1)
        self.assertEqual(ec_probe.decode_rpm(-1), -1)   # WMI error passthrough
        self.assertEqual(ec_probe.decode_rpm("abc"), -1)
        self.assertEqual(ec_probe.decode_rpm(None), -1)


class DutyForTests(unittest.TestCase):
    DUAL = [(25, 58), (40, 55), (55, 52), (70, 49), (85, 47), (100, 45)]
    ALL = [(25, 86), (40, 80), (55, 75), (70, 71), (85, 68), (100, 66)]

    def test_first_point_satisfying(self):
        duty, limited = analyze.duty_for(self.ALL, 87)
        self.assertEqual((duty, limited), (25, False))

    def test_exact_interpolation(self):
        # crossing cap 51 between (55, 52) and (70, 49): 55 + (1/3)*15 = 60
        duty, limited = analyze.duty_for(self.DUAL, 51)
        self.assertEqual((duty, limited), (60, False))

    def test_interpolation_rounds_up(self):
        # crossing cap 83 between (25, 86) and (40, 80): 32.5 -> 33
        duty, limited = analyze.duty_for(self.ALL, 83)
        self.assertEqual((duty, limited), (33, False))

    def test_cooling_limited(self):
        duty, limited = analyze.duty_for(self.ALL, 60)
        self.assertEqual((duty, limited), (100, True))


class SynthesizeTests(unittest.TestCase):
    IDLE = [(25, 45), (40, 44), (55, 43), (70, 42), (85, 41), (100, 40)]
    DUAL = DutyForTests.DUAL
    ALL = DutyForTests.ALL

    def test_curve_monotone_with_floors(self):
        curve, meta = analyze.synthesize_curve(
            {"idle": self.IDLE, "dual": self.DUAL, "all": self.ALL})
        temps = [t for t, _ in curve]
        duties = [d for _, d in curve]
        self.assertEqual(temps, analyze.GRID)
        self.assertEqual(duties, sorted(duties))
        self.assertTrue(all(0 <= d <= 100 for d in duties))
        by_t = dict(curve)
        self.assertGreaterEqual(by_t[80], 40)   # guard floor 80 C -> 40 %
        self.assertGreaterEqual(by_t[85], 60)   # guard floor 85 C -> 60 %
        self.assertGreaterEqual(by_t[90], 80)   # guard floor 90 C -> 80 %
        self.assertEqual(by_t[50], 65)  # 'all' cooling-limited; dual demands 65
        self.assertEqual(by_t[90], 80)
        self.assertEqual(by_t[95], 80)

    def test_zero_point_and_visible_floors(self):
        curve, _ = analyze.synthesize_curve({"idle": [(25, 45), (100, 43)]})
        by_t = dict(curve)
        self.assertEqual(by_t[50], 0)   # lowest duty holds 50 C -> 0
        self.assertEqual(by_t[80], 40)
        self.assertEqual(by_t[85], 60)
        self.assertEqual(by_t[90], 80)
        duties = [d for _, d in curve]
        self.assertEqual(duties, sorted(duties))

    def test_single_point_load_ignored(self):
        curve, meta = analyze.synthesize_curve(
            {"idle": [(25, 45)], "dual": self.DUAL})
        self.assertIn("idle", meta["loads_ignored"])
        self.assertIn("dual", meta["loads_used"])


class GuardTests(unittest.TestCase):
    """App guard contamination: cpu >= 80 C while EC runs above command + 10."""

    def test_guard_flag(self):
        self.assertTrue(analyze.compute_guard(82, 40, 120))  # ceil(91)+10 = 101
        self.assertFalse(analyze.compute_guard(82, 40, 100))
        self.assertFalse(analyze.compute_guard(79, 40, 120))
        self.assertFalse(analyze.compute_guard(82, None, 227))  # auto rows
        self.assertFalse(analyze.compute_guard(95, 100, 227))   # cannot exceed 100 %
        self.assertFalse(analyze.compute_guard(82, 40, -1))     # read error


class DeriveFallbackTests(unittest.TestCase):
    """Battery-only runs must keep the previously derived AC curve."""

    def _row(self, power, duty, load, temp):
        tid = f"ladder-{power}-{load}-d{duty}-r1"
        return {"trial_id": tid, "phase": "ladder", "power": power,
                "profile": "Bench-Ladder", "load": load, "duty_cmd": duty,
                "rep": 1, "guard_active": False, "ts": 1000.0,
                "cpu_t": temp, "fixed_raw": round(duty / 100 * 227),
                "rpm1": 3000, "rpm2": 3000, "proc_perf": 100,
                "discharge_mw": 90000 if power == "battery" else -1}

    def test_ac_fallback_survives_battery_only_derivation(self):
        ac_fallback = {"curve": [[t, 30] for t in analyze.GRID],
                       "partial": False, "loads_used": ["idle"],
                       "points": {"idle": [[25.0, 45.0]]}}
        rows = []
        for duty, temp in ((40, 88), (70, 86), (100, 84)):
            rows.append(self._row("battery", duty, "all", temp))
            rows.append({"trial_id": f"ladder-battery-all-d{duty}-r1",
                         "trial_end": True, "rows": 5, "ts": 1040.0})
        curves = analyze.derive_curves(rows, cap=86, window_s=30,
                                       ac_fallback=ac_fallback)
        self.assertEqual(curves["ac"], ac_fallback)
        bat = dict(curves["battery"]["curve"])
        # battery merge uses the REAL fallback AC curve, not a degenerate one
        self.assertEqual(curves["battery"]["partial"], True)
        self.assertEqual(curves["battery"].get("merged_with_ac"), True)
        for t, duty in bat.items():
            self.assertGreaterEqual(duty, 30)  # >= fallback AC at every temp

    def test_battery_fallback_survives_ac_only_derivation(self):
        bat_fallback = {"curve": [[t, 45] for t in analyze.GRID],
                        "partial": False, "loads_used": ["dual"],
                        "points": {"dual": [[40.0, 76.0]]}, "validated": True}
        rows = []
        for duty, temp in ((40, 70), (70, 68), (100, 66)):
            for i in range(5):
                rows.append(self._row("ac", duty, "all", temp + (i % 2)))
                rows[-1]["ts"] = 1000.0 + i
            rows.append({"trial_id": f"ladder-ac-all-d{duty}-r1",
                         "trial_end": True, "rows": 5, "ts": 1040.0})
        curves = analyze.derive_curves(rows, cap=86, window_s=30,
                                       battery_fallback=bat_fallback)
        self.assertEqual(curves["battery"], bat_fallback)  # untouched, no merge
        self.assertNotIn("merged_with_ac", curves["battery"])
        # fresh AC derived normally alongside
        self.assertTrue(curves["ac"].get("points"))


class BatteryEnduranceTests(unittest.TestCase):
    """Endurance must be glitch-filtered median, not raw mean."""

    def _row(self, phase, mw):
        return {"power": "battery", "load": "all", "phase": phase,
                "discharge_mw": mw}

    def test_median_ignores_glitches_and_other_loads(self):
        rows = [self._row("ladder", mw) for mw in (99000, 100000, 100000)]
        rows.append(self._row("ladder", 989186))   # observed glitch
        rows.append(self._row("ladder", -1))       # read error
        rows.append(self._row("ladder", 0))        # zero excluded
        rows.append(self._row("ladder", 60000))
        rows.append(self._row("profiles", 50000))  # profiles rows count too
        e = analyze.battery_endurance(rows, 99013.0)
        # plausible ladder+profiles: 50000,60000,99000,100000,100000 -> median 99000
        self.assertEqual(e["median_discharge_mw"], 99000.0)
        expected_min = 0.9 * 99013.0 / 99000.0 * 60.0
        self.assertAlmostEqual(e["minutes"], expected_min, places=6)
        self.assertEqual(e["samples"], 5)

    def test_no_plausible_samples_yields_none(self):
        rows = [self._row("ladder", 989186), self._row("ladder", 0)]
        self.assertIsNone(analyze.battery_endurance(rows, 99013.0))


if __name__ == "__main__":
    unittest.main()
