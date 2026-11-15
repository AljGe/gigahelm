# -*- coding: utf-8 -*-
"""
LevelBar: a tiny non-interactive bar that visualises a 0..1 fraction.

Deliberately painted rather than styled: a QSS-based bar cannot animate a
gradient width cheaply, and this widget is redrawn on every sensor tick.
"""

from typing import Optional

from .qt import QWidget, QSizePolicy, QPainter, QColor, QRectF, QPaintEvent, Qt


class LevelBar(QWidget):
    """Rounded track plus a rounded fill proportional to `level`."""

    RADIUS = 3
    HEIGHT = 6

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._track_color = QColor("#2A2D30")
        self._fill_color = QColor("#2DD4BF")
        self._level: Optional[float] = None

    def set_colors(self, track: str, fill: str) -> None:
        """Set the track and fill colours (CSS hex strings)."""
        self._track_color = QColor(track)
        self._fill_color = QColor(fill)
        self.update()

    def set_level(self, level: Optional[float]) -> None:
        """Set the fraction to draw; `None` or <= 0 draws nothing."""
        if level is None:
            self._level = None
        else:
            self._level = max(0.0, min(1.0, float(level)))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._level:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        rect = QRectF(self.rect())
        painter.setBrush(self._track_color)
        painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

        fill_rect = QRectF(rect.left(), rect.top(), rect.width() * self._level, rect.height())
        painter.setBrush(self._fill_color)
        painter.drawRoundedRect(fill_rect, self.RADIUS, self.RADIUS)
        painter.end()
