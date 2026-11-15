# -*- coding: utf-8 -*-
"""
Custom widget that normally shows a QLabel and turns into an editable QLineEdit on double-click.
"""
# Explicit imports instead of a wildcard import
from .qt import (
    QWidget, Signal, QLabel, QLineEdit, QStackedLayout, QIntValidator,
    Qt, QMouseEvent
)
from typing import Optional, cast

class EditableLabel(QWidget):
    """Label widget that becomes editable on double-click."""
    editingFinished = Signal(int)

    def __init__(self, unit: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.unit = unit
        
        self.label = QLabel(self)
        self.line_edit = QLineEdit(self)
        self.line_edit.hide()
        
        self.line_edit.setValidator(QIntValidator(0, 100, self))
        
        layout: QStackedLayout = QStackedLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.line_edit)
        
        self.line_edit.editingFinished.connect(self._finish_editing)
        self.setFocusProxy(self.line_edit)

    def text(self) -> str:
        """Get the label text (without the unit)."""
        return self.label.text().replace(self.unit, "").strip()

    def value(self) -> int:
        """Get the integer value shown by the label."""
        try:
            return int(self.text())
        except ValueError:
            return 0

    def setText(self, text: str):
        """Set the label text."""
        self.label.setText(f"{text}{self.unit}")

    def setValue(self, value: int):
        """Set the label value."""
        self.setText(str(value))
        # Keep the hidden line edit's content in sync too
        if self.line_edit.text() != str(value):
            self.line_edit.setText(str(value))

    def setAlignment(self, alignment: Qt.AlignmentFlag):
        """Set the alignment of both label and line edit."""
        self.label.setAlignment(alignment)
        self.line_edit.setAlignment(alignment)

    def setMinimumWidth(self, minw: int):
        """Set the minimum width."""
        self.label.setMinimumWidth(minw)
        super().setMinimumWidth(minw)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Handle double-click: switch to edit mode."""
        if self.isEnabled():
            self.line_edit.setText(self.text())
            cast(QStackedLayout, self.layout()).setCurrentWidget(self.line_edit)
            self.line_edit.selectAll()
            self.line_edit.setFocus()
        super().mouseDoubleClickEvent(event)

    def _finish_editing(self):
        """Finish editing: commit the value and switch back to label mode."""
        new_value = self.line_edit.text()
        if new_value:
            self.setValue(int(new_value))
            self.editingFinished.emit(int(new_value))
        cast(QStackedLayout, self.layout()).setCurrentWidget(self.label)

    def setEnabled(self, enabled: bool):
        """Override setEnabled to control the inner widget as well."""
        self.label.setEnabled(enabled)
        super().setEnabled(enabled)