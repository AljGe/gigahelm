# -*- coding: utf-8 -*-
"""
Centralized Qt imports.

This module is the single entry point for all Qt-related imports,
making it easy to migrate between Qt bindings (e.g. PySide6)
and improving cohesion by centralizing the framework dependency.
"""

# PySide6 is the binding in use
from PySide6.QtCore import (
    QObject,
    QTimer,
    QCoreApplication,
    QAbstractNativeEventFilter,
    Qt,
    QLocale,
    QEvent,
    QByteArray,
    Signal,
    Slot,
    QSize,
    QPoint,
    QRectF,
    QPointF,
    QRunnable,
    QThreadPool,
    Property,
    QBuffer,
    QIODevice,
    QRect
)
from PySide6.QtGui import (
    QIcon,
    QAction,
    QImage,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QLinearGradient,
    QPainterPath,
    QPolygonF,
    QCloseEvent,
    QShowEvent,
    QHideEvent,
    QMouseEvent,
    QContextMenuEvent,
    QResizeEvent,
    QPixmap,
    QCursor,
    QIntValidator,
    QPaintEvent, # Type support for paintEvent signatures
    QRegion
)
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QSystemTrayIcon,
    QMenu,
    QStyle,
    QGridLayout,
    QLabel,
    QRadioButton,
    QButtonGroup,
    QSlider,
    QSpacerItem,
    QSizePolicy,
    QPushButton,
    QCheckBox,
    QInputDialog,
    QLineEdit,
    QComboBox,
    QToolTip,
    QStackedLayout,
    QDialog,
    QDialogButtonBox
)

# SVG icon import; may require 'pip install PySide6-Addons'
try:
    from PySide6.QtSvg import QSvgRenderer
except ImportError:
    print("Warning: PySide6.QtSvg module not found. SVG icons will be unavailable. Run 'pip install PySide6-Addons'.")
    QSvgRenderer = None


def repolish(widget: "QWidget") -> None:
    """Force a widget to re-read its stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()