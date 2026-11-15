# -*- coding: utf-8 -*-
"""
SegmentedControl: a small exclusive group of pill-shaped toggle buttons.

Replaces the old QRadioButton rows: it exposes one string value, hides the
button mechanics, and keeps the QSS `QFrame#segmented QPushButton` rules working.
"""

from typing import Dict, List, Optional

from .qt import QFrame, QHBoxLayout, QPushButton, QButtonGroup, Signal, Slot, QWidget
from .tooltip_manager import tooltip_manager


class SegmentedControl(QFrame):
    """Exclusive row of segments; `valueChanged` carries the segment value."""

    valueChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self._buttons: Dict[str, QPushButton] = {}
        self._order: List[str] = []
        self._tooltip_key: Optional[str] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.buttonClicked.connect(self._on_button_clicked)

    def add_segment(self, value: str, text: str) -> QPushButton:
        """Append a segment and return its button."""
        button = QPushButton(text, self)
        button.setObjectName("segment")
        button.setProperty("segment", True)
        button.setCheckable(True)
        self._buttons[value] = button
        self._order.append(value)
        self._group.addButton(button)
        if self._tooltip_key:
            tooltip_manager.register(button, self._tooltip_key)
        self.layout().addWidget(button)
        if len(self._order) == 1:
            button.setChecked(True)
        return button

    def set_value(self, value: str) -> None:
        """Select a segment without emitting `valueChanged`; unknown values clear the row."""
        button = self._buttons.get(value)
        if button is None:
            self._group.blockSignals(True)
            self._group.setExclusive(False)
            try:
                for candidate in self._buttons.values():
                    candidate.setChecked(False)
            finally:
                self._group.setExclusive(True)
                self._group.blockSignals(False)
            return
        self._group.blockSignals(True)
        try:
            button.setChecked(True)
        finally:
            self._group.blockSignals(False)

    def value(self) -> Optional[str]:
        """The currently checked segment value."""
        for value in self._order:
            button = self._buttons[value]
            if button.isChecked():
                return value
        return None

    def set_segment_text(self, value: str, text: str) -> None:
        """Update one segment's label."""
        button = self._buttons.get(value)
        if button is not None:
            button.setText(text)

    def set_tooltip_key(self, text_key: str) -> None:
        """Apply one translated tooltip to every current and future segment."""
        self._tooltip_key = text_key
        for button in self._buttons.values():
            tooltip_manager.unregister(button)
            tooltip_manager.register(button, text_key)

    def setEnabled(self, enabled: bool) -> None:
        """Disable the frame and every segment inside it."""
        super().setEnabled(enabled)
        for button in self._buttons.values():
            button.setEnabled(enabled)

    @Slot(QPushButton)
    def _on_button_clicked(self, button: QPushButton) -> None:
        for value, candidate in self._buttons.items():
            if candidate is button:
                self.valueChanged.emit(value)
                return
