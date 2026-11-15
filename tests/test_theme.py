# -*- coding: utf-8 -*-
"""Theme resolution and stylesheet generation."""
import unittest

from gui.theme import (
    DARK_PALETTE, LIGHT_PALETTE, PALETTES, build_stylesheet, resolve_palette
)


class ResolvePaletteTests(unittest.TestCase):
    def test_explicit_modes(self):
        self.assertIs(resolve_palette("dark"), DARK_PALETTE)
        self.assertIs(resolve_palette("light"), LIGHT_PALETTE)

    def test_system_resolves_to_a_real_palette(self):
        self.assertIn(resolve_palette("system").name, {"dark", "light"})

    def test_unknown_mode_falls_back_to_dark(self):
        self.assertIs(resolve_palette("chartreuse"), DARK_PALETTE)

    def test_palette_registry_matches_constants(self):
        self.assertEqual(set(PALETTES), {"dark", "light"})
        self.assertIs(PALETTES["dark"], DARK_PALETTE)
        self.assertIs(PALETTES["light"], LIGHT_PALETTE)


class BuildStylesheetTests(unittest.TestCase):
    def test_every_palette_builds_without_leftover_tokens(self):
        for palette in PALETTES.values():
            with self.subTest(palette=palette.name):
                qss = build_stylesheet(palette)
                self.assertIn("#statCard", qss)
                self.assertIn(palette.accent, qss)
                self.assertNotIn("$", qss)

    def test_palettes_differ_in_background_and_text(self):
        dark_qss = build_stylesheet(DARK_PALETTE)
        light_qss = build_stylesheet(LIGHT_PALETTE)
        self.assertNotEqual(dark_qss, light_qss)
        self.assertIn(DARK_PALETTE.bg, dark_qss)
        self.assertIn(LIGHT_PALETTE.bg, light_qss)


if __name__ == "__main__":
    unittest.main()
