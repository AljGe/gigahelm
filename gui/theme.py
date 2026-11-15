# -*- coding: utf-8 -*-
"""
Single source of truth for every colour and style in the application.

A `Palette` holds the whole colour vocabulary; `QSS_TEMPLATE` is the widget
stylesheet expressed in terms of that vocabulary. `ThemeManager` caches the
resolved palette and stylesheet and emits `changed` whenever either moves.
"""

import os
from dataclasses import dataclass, asdict
from string import Template
from typing import Dict, Optional

from .qt import QObject, Signal, QApplication, QFont, QFontDatabase
from config.settings import (
    THEME_MODE_SYSTEM, THEME_MODE_DARK, THEME_MODE_LIGHT, DEFAULT_THEME_MODE
)


@dataclass(frozen=True)
class Palette:
    """Complete colour vocabulary of the interface."""
    name: str
    bg: str
    surface: str
    surface_alt: str
    hover: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    warn: str
    danger: str
    success: str
    grid: str
    curve_cpu: str
    curve_gpu: str
    dot_cpu: str
    dot_gpu: str


DARK_PALETTE = Palette(
    "dark",
    bg="#0E1116", surface="#161B22", surface_alt="#1D242E", hover="#232C38",
    border="#262F3B", border_strong="#364152", text="#E6EDF6", text_muted="#8E9AAB",
    accent="#2DD4BF", accent_text="#06211D", warn="#F0A83C", danger="#F05B5B",
    success="#5CD68A", grid="#2A3441", curve_cpu="#4CC2FF", curve_gpu="#A6E05C",
    dot_cpu="#FF7B72", dot_gpu="#7EE787",
)

LIGHT_PALETTE = Palette(
    "light",
    bg="#F2F5F9", surface="#FFFFFF", surface_alt="#E9EEF5", hover="#DFE6EF",
    border="#D8DFE9", border_strong="#B9C4D2", text="#12171F", text_muted="#5A6678",
    accent="#0E9F8E", accent_text="#FFFFFF", warn="#B45309", danger="#C62828",
    success="#1B7F3B", grid="#C9D2DE", curve_cpu="#0B72B5", curve_gpu="#4A7D12",
    dot_cpu="#D14343", dot_gpu="#2E8B57",
)

PALETTES: Dict[str, Palette] = {"dark": DARK_PALETTE, "light": LIGHT_PALETTE}


def system_prefers_light() -> bool:
    """Whether Windows is configured to use the light application theme."""
    if os.name != "nt":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(value)
    except OSError:
        return False


def resolve_palette(mode: str) -> Palette:
    """Map a stored theme mode onto a concrete palette."""
    if mode == THEME_MODE_LIGHT:
        return LIGHT_PALETTE
    if mode == THEME_MODE_DARK:
        return DARK_PALETTE
    if mode == THEME_MODE_SYSTEM:
        return LIGHT_PALETTE if system_prefers_light() else DARK_PALETTE
    return DARK_PALETTE


QSS_TEMPLATE = """
QWidget { color: $text; font-size: 10pt; }
QMainWindow, QWidget#mainContainer, QWidget#contentWidget { background: $bg; }

QWidget#customTitleBar { background: $surface; border-bottom: 1px solid $border; }
QLabel#titleBarLabel { font-size: 11pt; font-weight: 700; color: $text; }
QLabel#statusLabel { font-size: 9pt; color: $text_muted; }
QLabel#statusLabel[level="warn"] { color: $warn; }
QLabel#statusLabel[level="error"] { color: $danger; }

QPushButton#windowControlButton, QPushButton#windowControlButton_close,
QPushButton#themeButton { background: transparent; border: none; border-radius: 8px; padding: 0; }
QPushButton#windowControlButton:hover, QPushButton#windowControlButton_close:hover,
QPushButton#themeButton:hover { background: $hover; }
QPushButton#windowControlButton:pressed, QPushButton#windowControlButton_close:pressed,
QPushButton#themeButton:pressed { background: $surface_alt; }

QFrame#statCard, QFrame#card, QFrame#curveCard { background: $surface; border: 1px solid $border; border-radius: 12px; }
QLabel#statCaption { font-size: 9pt; font-weight: 600; color: $text_muted; }
QLabel#statValue { font-size: 24pt; font-weight: 600; color: $text; }
QLabel#statValue[tone="warn"] { color: $warn; }
QLabel#statValue[tone="danger"] { color: $danger; }
QLabel#statValue[tone="muted"] { color: $text_muted; }
QLabel#statUnit { font-size: 11pt; font-weight: 500; color: $text_muted; }
QLabel#statFooter { font-size: 9pt; color: $text_muted; }

QLabel#cardTitle { font-size: 12pt; font-weight: 600; color: $text; }
QLabel#cardDesc { font-size: 9pt; color: $text_muted; }
QLabel#liveValue { font-size: 18pt; font-weight: 600; color: $text; }
QLabel#liveLine { font-size: 9pt; color: $text_muted; }

QFrame#segmented { background: $surface_alt; border: 1px solid $border; border-radius: 9px; }
QFrame#segmented QPushButton { background: transparent; border: none; border-radius: 7px;
    padding: 5px 14px; font-size: 9pt; font-weight: 600; color: $text_muted; }
QFrame#segmented QPushButton:hover { color: $text; }
QFrame#segmented QPushButton:checked { background: $accent; color: $accent_text; }
QFrame#segmented QPushButton:disabled { color: $border_strong; }

QPushButton { background: $surface_alt; color: $text; border: 1px solid $border;
    border-radius: 8px; padding: 6px 12px; }
QPushButton:hover { background: $hover; border-color: $border_strong; }
QPushButton:pressed { background: $surface_alt; }
QPushButton:checked { background: $accent; color: $accent_text; border-color: $accent; }
QPushButton:disabled { color: $text_muted; }
QPushButton#profileChip { border-radius: 12px; padding: 4px 12px; font-size: 9pt; color: $text_muted; }
QPushButton#profileChip:checked { background: $accent; color: $accent_text; border-color: $accent; font-weight: 600; }
QPushButton#addProfileButton { border-radius: 12px; padding: 2px 10px; font-size: 11pt; color: $text_muted; }
QPushButton#resetCurveButton { background: transparent; color: $text_muted; }
QPushButton#resetCurveButton:hover { color: $warn; border-color: $warn; }
QPushButton#dangerButton { color: $danger; }
QPushButton#dangerButton:hover { background: $danger; color: $accent_text; border-color: $danger; }

QSlider::groove:horizontal { height: 4px; background: $surface_alt; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
    background: $surface; border: 2px solid $accent; }
QSlider::handle:horizontal:hover { background: $accent; }
QSlider::sub-page:horizontal:disabled { background: $border_strong; }
QSlider::handle:horizontal:disabled { border-color: $border_strong; background: $surface_alt; }

QLabel#valuePill { background: $surface_alt; border: 1px solid $border; border-radius: 6px;
    padding: 2px 8px; font-size: 10pt; font-weight: 600; color: $text; }
EditableLabel > QLineEdit { background: $surface_alt; border: 1px solid $accent; border-radius: 6px;
    padding: 2px 6px; color: $text; }

QComboBox { background: $surface_alt; border: 1px solid $border; border-radius: 8px;
    padding: 4px 22px 4px 10px; font-size: 9pt; color: $text; }
QComboBox:hover { border-color: $border_strong; }
QComboBox::drop-down { width: 18px; border: none; }
QComboBox QAbstractItemView { background: $surface; border: 1px solid $border; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: $accent; selection-color: $accent_text; }

QCheckBox { spacing: 8px; font-size: 9pt; color: $text_muted; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid $border_strong; background: $surface_alt; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

QToolTip { background: $surface; color: $text; border: 1px solid $border_strong;
    border-radius: 6px; padding: 6px 8px; font-size: 9pt; }

QMenu { background: $surface; border: 1px solid $border; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 18px 6px 12px; border-radius: 6px; font-size: 9pt; color: $text; }
QMenu::item:selected { background: $accent; color: $accent_text; }
QMenu::item:disabled { color: $text_muted; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }

QDialog, QMessageBox { background: $bg; }
QLineEdit { background: $surface_alt; border: 1px solid $border; border-radius: 8px;
    padding: 6px 8px; color: $text; selection-background-color: $accent; selection-color: $accent_text; }
QLineEdit:focus { border-color: $accent; }
"""


def build_stylesheet(palette: Palette) -> str:
    """Render the QSS template for one palette (raises on any unfilled token)."""
    return Template(QSS_TEMPLATE).substitute(asdict(palette))


def apply_app_font(app: QApplication) -> None:
    """Pick the nicest available UI font and install it application-wide."""
    installed = set(QFontDatabase.families())
    candidates = ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", app.font().family())
    family = next((name for name in candidates if name in installed), app.font().family())
    app.setFont(QFont(family, 10))


_MODE_CYCLE = (THEME_MODE_SYSTEM, THEME_MODE_DARK, THEME_MODE_LIGHT)


class ThemeManager(QObject):
    """Resolves and caches the active palette and stylesheet."""

    changed = Signal()

    def __init__(self, state, settings_manager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._state = state
        self._settings_manager = settings_manager
        self._palette_cache: Dict[str, Palette] = {}
        self._stylesheet_cache: Dict[str, str] = {}
        self._state.theme_mode_changed.connect(self.refresh)

    def mode(self) -> str:
        """The stored mode: 'system', 'dark' or 'light'."""
        return self._state.get_theme_mode()

    def palette(self) -> Palette:
        """The palette the stored mode resolves to right now."""
        mode = self.mode()
        cached = self._palette_cache.get(mode)
        if cached is None:
            cached = resolve_palette(mode)
            self._palette_cache[mode] = cached
        return cached

    def stylesheet(self) -> str:
        """The QSS for the current palette."""
        palette = self.palette()
        cached = self._stylesheet_cache.get(palette.name)
        if cached is None:
            cached = build_stylesheet(palette)
            self._stylesheet_cache[palette.name] = cached
        return cached

    def set_mode(self, mode: str) -> None:
        """Persist a new mode; state emits the change and refresh() runs."""
        if mode == self.mode():
            return
        self._palette_cache.pop(THEME_MODE_SYSTEM, None)
        self._settings_manager.set_theme_mode(mode)

    def cycle(self) -> None:
        """Advance system -> dark -> light -> system."""
        current = self.mode()
        try:
            index = _MODE_CYCLE.index(current)
        except ValueError:
            index = 0
        self.set_mode(_MODE_CYCLE[(index + 1) % len(_MODE_CYCLE)])

    def refresh(self) -> None:
        """Re-resolve the system palette and announce the new theme."""
        self._palette_cache.pop(THEME_MODE_SYSTEM, None)
        self.changed.emit()


__all__ = [
    "Palette", "DARK_PALETTE", "LIGHT_PALETTE", "PALETTES", "QSS_TEMPLATE",
    "system_prefers_light", "resolve_palette", "build_stylesheet",
    "apply_app_font", "ThemeManager", "DEFAULT_THEME_MODE",
]
