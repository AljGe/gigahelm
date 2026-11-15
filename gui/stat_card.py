# -*- coding: utf-8 -*-
"""
StatCard: one live metric tile for the dashboard hero row.

Pure view: it never reads AppState itself, the dashboard pushes values in.
Every setter is idempotent and cheap so it can run on each sensor tick.
"""

from typing import Optional

from .qt import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy, Qt, repolish,
)
from .theme import Palette
from .level_bar import LevelBar
from tools.localization import tr

_TONE_COLORS = ("normal", "warn", "danger", "muted")


class StatCard(QFrame):
    """Caption, big value plus unit, level bar and a footer line."""

    HEIGHT = 112

    def __init__(self, caption_key: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.caption_key = caption_key
        self._tone = "normal"
        self._palette: Optional[Palette] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.caption_label = QLabel(tr(caption_key), self)
        self.caption_label.setObjectName("statCaption")
        layout.addWidget(self.caption_label)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(4)
        self.value_label = QLabel("", self)
        self.value_label.setObjectName("statValue")
        self.unit_label = QLabel("", self)
        self.unit_label.setObjectName("statUnit")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignBottom)
        value_row.addWidget(self.value_label)
        value_row.addWidget(self.unit_label)
        value_row.addStretch(1)
        layout.addLayout(value_row)

        self.level_bar = LevelBar(self)
        layout.addWidget(self.level_bar)

        self.footer_label = QLabel("", self)
        self.footer_label.setObjectName("statFooter")
        layout.addWidget(self.footer_label)
        layout.addStretch(1)

    def set_caption_text(self, text: str) -> None:
        """Set the small caption above the value."""
        self.caption_label.setText(text)

    def set_value(self, text: str) -> None:
        """Set the big number text."""
        if self.value_label.text() != text:
            self.value_label.setText(text)

    def set_unit(self, text: str) -> None:
        """Set the unit shown next to the big number."""
        if self.unit_label.text() != text:
            self.unit_label.setText(text)

    def set_footer(self, text: str) -> None:
        """Set the muted line under the bar."""
        if self.footer_label.text() != text:
            self.footer_label.setText(text)

    def set_tone(self, tone: str) -> None:
        """Switch the value colour between normal, warn, danger and muted."""
        if tone not in _TONE_COLORS:
            tone = "normal"
        if tone == self._tone:
            return
        self._tone = tone
        self.value_label.setProperty("tone", tone)
        repolish(self.value_label)
        self._refresh_level_colors()

    def set_level(self, level: Optional[float]) -> None:
        """Show a fraction on the level bar, or hide the bar for `None`."""
        if level is None:
            self.level_bar.setVisible(False)
            return
        self.level_bar.setVisible(True)
        self.level_bar.set_level(level)

    def apply_theme(self, palette: Palette) -> None:
        """Re-tint the level bar for the active palette."""
        self._palette = palette
        self._refresh_level_colors()

    def _refresh_level_colors(self) -> None:
        if self._palette is None:
            return
        fill = {
            "normal": self._palette.accent,
            "warn": self._palette.warn,
            "danger": self._palette.danger,
            "muted": self._palette.text_muted,
        }[self._tone]
        self.level_bar.set_colors(self._palette.surface_alt, fill)
