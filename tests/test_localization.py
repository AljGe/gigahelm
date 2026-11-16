# -*- coding: utf-8 -*-
"""Localization tables: parity between languages and completeness of usage."""
import os
import re
import unittest

from tools.localization import (
    DEFAULT_CHINESE_TRANSLATIONS, DEFAULT_ENGLISH_TRANSLATIONS, set_language, tr,
    _translations,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ("gui", "core", "tools")
SCAN_ROOT_FILES = ("main.py",)
TR_CALL = re.compile(r"""\btr\(\s*["']([a-z0-9_]+)["']""")


def _iter_source_files():
    for directory in SCAN_DIRS:
        for base, _dirs, files in os.walk(os.path.join(REPO_ROOT, directory)):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(base, name)
    for name in SCAN_ROOT_FILES:
        path = os.path.join(REPO_ROOT, name)
        if os.path.exists(path):
            yield path


def _used_keys():
    used = set()
    for path in _iter_source_files():
        with open(path, "r", encoding="utf-8") as handle:
            used.update(TR_CALL.findall(handle.read()))
    return used


class TranslationTableTests(unittest.TestCase):
    def test_language_key_sets_match(self):
        self.assertEqual(
            set(DEFAULT_ENGLISH_TRANSLATIONS),
            set(DEFAULT_CHINESE_TRANSLATIONS),
        )

    def test_ux_pass_keys_exist_in_both_languages(self):
        keys = (
            "curve_min_points_reached", "ff_disabled_not_auto",
            "fan_mode_apply_failed", "refresh_ms", "tray_charge_percent",
        )
        for key in keys:
            with self.subTest(key=key):
                self.assertIn(key, DEFAULT_ENGLISH_TRANSLATIONS)
                self.assertIn(key, DEFAULT_CHINESE_TRANSLATIONS)

    def test_registry_exposes_both_languages(self):
        self.assertEqual(set(_translations), {"en", "zh"})

    def test_every_key_used_in_code_exists_in_both_languages(self):
        missing = {}
        for key in sorted(_used_keys()):
            if key not in DEFAULT_ENGLISH_TRANSLATIONS:
                missing.setdefault("en", []).append(key)
            if key not in DEFAULT_CHINESE_TRANSLATIONS:
                missing.setdefault("zh", []).append(key)
        self.assertEqual(missing, {}, f"translation keys used in code are missing: {missing}")

    def test_scan_actually_finds_translation_calls(self):
        used = _used_keys()
        self.assertGreater(len(used), 30)
        self.assertIn("mode_bios", used)

    def test_window_title_is_branded_in_both_languages(self):
        for code in ("en", "zh"):
            with self.subTest(language=code):
                set_language(code)
                self.assertEqual(tr("window_title"), "Gigahelm")
        set_language("en")


if __name__ == "__main__":
    unittest.main()
