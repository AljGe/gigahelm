# -*- coding: utf-8 -*-
"""
Resolve the application icon.

The icon is a loose file (`app_icon.ico`) next to the executable, not an
embedded resource, so a packaging mistake can leave it missing. A null icon
would make the tray entry invisible - the app would look like it vanished -
so this module always returns a usable icon.
"""

import os
import sys
from typing import Optional

from .qt import QApplication, QIcon, QStyle


def load_app_icon(path: Optional[str]) -> QIcon:
    """Load the icon at `path`, falling back to a visible built-in icon."""
    if path and os.path.exists(path):
        icon = QIcon(path)
        if not icon.isNull():
            return icon
        print(f"Warning: application icon at '{path}' could not be decoded.", file=sys.stderr)
    else:
        print(f"Warning: application icon not found at '{path}'. Using a fallback icon.", file=sys.stderr)

    return _fallback_icon()


def _fallback_icon() -> QIcon:
    app = QApplication.instance()
    style = app.style() if app is not None else None
    if style is not None:
        icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        if not icon.isNull():
            return icon
    return QIcon()
