# -*- coding: utf-8 -*-
"""
A centralized, reliable tooltip manager that fixes Qt's default ToolTip mechanism being unreliable in complex UIs.
"""
from .qt import QObject, QTimer, QToolTip, QEvent, QWidget, QCursor, Slot, QPoint
from typing import Dict, Optional
from tools.localization import tr
from config.settings import TOOLTIP_DELAY_MS

class TooltipManager(QObject):
    """
    Singleton class that proactively manages and shows tooltips via an event filter.
    """
    _instance: Optional['TooltipManager'] = None

    @staticmethod
    def get_instance() -> 'TooltipManager':
        """Returns the TooltipManager singleton instance."""
        if TooltipManager._instance is None:
            TooltipManager._instance = TooltipManager()
        return TooltipManager._instance

    def __init__(self, parent: Optional[QObject] = None):
        """Initializer, private to enforce the singleton pattern."""
        if TooltipManager._instance is not None:
            raise RuntimeError("TooltipManager is a singleton, use get_instance()")
        super().__init__(parent)
        
        self._registered_widgets: Dict[QWidget, str] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TOOLTIP_DELAY_MS)
        self._timer.timeout.connect(self._show_tooltip)

        self._current_widget: Optional[QWidget] = None

    def register(self, widget: QWidget, text_key: str):
        """
        Registers a tooltip for a widget.
        
        Args:
            widget: The widget that should show the tooltip.
            text_key: Key used to look up the text in the translation file.
        """
        if widget not in self._registered_widgets:
            self._registered_widgets[widget] = text_key
            widget.installEventFilter(self)

    def unregister(self, widget: QWidget):
        """Unregisters a widget's tooltip."""
        if widget in self._registered_widgets:
            widget.removeEventFilter(self)
            del self._registered_widgets[widget]

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """
        Event filter that captures mouse enter/leave events to control tooltips.
        """
        if not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)

        # Start or stop the timer based on the event type
        if event.type() == QEvent.Type.Enter:
            self._current_widget = watched
            self._timer.start()
        elif event.type() == QEvent.Type.Leave:
            self._timer.stop()
            if self._current_widget is watched:
                self._current_widget = None
            QToolTip.hideText()
        elif event.type() == QEvent.Type.Hide:
            # If the widget was hidden, also stop the timer and hide the tooltip
            if watched == self._current_widget:
                self._timer.stop()
                self._current_widget = None
                QToolTip.hideText()
        
        return super().eventFilter(watched, event)

    @Slot()
    def _show_tooltip(self):
        """
        Shows the tooltip when the timer fires.
        """
        if self._current_widget and self._current_widget.isVisible() and self._current_widget.isEnabled():
            text_key = self._registered_widgets.get(self._current_widget)
            if text_key:
                translated_text = tr(text_key)
                if translated_text:
                    # Offset the position so the tooltip does not flicker under the cursor, avoiding event conflicts
                    pos = QCursor.pos()
                    offset_pos = QPoint(pos.x() + 16, pos.y() + 16)
                    QToolTip.showText(offset_pos, translated_text, self._current_widget)

# Create the global singleton instance for use across the app
tooltip_manager = TooltipManager.get_instance()