# -*- coding: utf-8 -*-
"""FlowLayout: wraps child widgets onto new rows instead of inflating the
parent's minimum width. Standard Qt example layout, trimmed to the widget
API CurveCard needs (addWidget / takeAt / heightForWidth).
"""

from typing import List, Optional

from .qt import QLayout, QRect, QSize, QPoint, Qt, QWidget


class FlowLayout(QLayout):
    """Left-aligned wrapping row; the minimum width is the widest child."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 6):
        super().__init__(parent)
        self.setSpacing(spacing)
        self._items: List[QLayoutItem] = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def minimumHeightForWidth(self, width: int) -> int:
        return self.heightForWidth(width)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        right = rect.right() - margins.right()
        bottom = rect.bottom() - margins.bottom()
        spacing = self.spacing()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > right and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
            _ = bottom  # rows may overflow the rect when testing tiny widths
        return y + line_height - rect.y() + margins.bottom()
