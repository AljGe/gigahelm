# -*- coding: utf-8 -*-
"""
The frameless window's title bar: brand, status text, language picker,
theme switch and the three window controls.
"""

from typing import Optional

from .qt import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox, QSize, Qt, QIcon, QPixmap,
    QPainter, QSvgRenderer, QByteArray, QMainWindow, Slot,
)
from .app_icon import load_app_icon
from .theme import ThemeManager
from core.state import AppState
from core.settings_manager import SettingsManager
from tools.localization import tr, get_available_languages, get_current_language
from config.settings import THEME_MODE_SYSTEM, THEME_MODE_DARK, THEME_MODE_LIGHT

ICON_SIZE = 10
WINDOW_BUTTON_SIZE = 32
THEME_BUTTON_SIZE = 28
LOGO_SIZE = 22

SVG_MINIMIZE = """
<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
  <path stroke="{color}" stroke-width="2" d="M2 6h8"/>
</svg>
"""
SVG_MAXIMIZE = """
<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
  <path fill-opacity="0" stroke="{color}" stroke-width="2" d="M2 2h8v8H2z"/>
</svg>
"""
SVG_RESTORE = """
<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
  <path fill-opacity="0" stroke="{color}" stroke-width="1.6" d="M4.2 4.2h5.6v5.6H4.2z"/>
  <path fill-opacity="0" stroke="{color}" stroke-width="1.6" d="M2.2 7.8V2.2h5.6"/>
</svg>
"""
SVG_CLOSE = """
<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
  <path stroke="{color}" stroke-width="2.5" d="M2.5 2.5l7 7M2.5 9.5l7-7"/>
</svg>
"""
SVG_THEME_SYSTEM = """
<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
  <circle cx="7" cy="7" r="5.3" fill-opacity="0" stroke="{color}" stroke-width="1.6"/>
  <path fill="{color}" d="M7 1.7a5.3 5.3 0 0 1 0 10.6z"/>
</svg>
"""
SVG_THEME_DARK = """
<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
  <path fill-opacity="0" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"
        d="M11.6 8.7A5.2 5.2 0 0 1 5.3 2.4a5.6 5.6 0 1 0 6.3 6.3z"/>
</svg>
"""
SVG_THEME_LIGHT = """
<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
  <circle cx="7" cy="7" r="3.1" fill-opacity="0" stroke="{color}" stroke-width="1.6"/>
  <path stroke="{color}" stroke-width="1.6" stroke-linecap="round"
        d="M7 1v1.6M7 11.4V13M1 7h1.6M11.4 7H13M2.8 2.8l1.1 1.1M10.1 10.1l1.1 1.1M11.2 2.8l-1.1 1.1M3.9 10.1l-1.1 1.1"/>
</svg>
"""

_THEME_ICONS = {
    THEME_MODE_SYSTEM: SVG_THEME_SYSTEM,
    THEME_MODE_DARK: SVG_THEME_DARK,
    THEME_MODE_LIGHT: SVG_THEME_LIGHT,
}


class CustomTitleBar(QWidget):
    """Custom title bar with brand, status, language and theme controls."""

    def __init__(
        self,
        state: AppState,
        settings_manager: SettingsManager,
        theme: ThemeManager,
        parent: Optional[QMainWindow] = None,
    ):
        super().__init__(parent)
        self.state = state
        self.settings_manager = settings_manager
        self.theme = theme

        self.setObjectName("customTitleBar")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(12)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(QSize(LOGO_SIZE, LOGO_SIZE))
        self.icon_label.setScaledContents(True)
        self.icon_label.setPixmap(load_app_icon(self.state.paths.app_icon).pixmap(QSize(LOGO_SIZE, LOGO_SIZE)))

        self.title_label = QLabel(tr("window_title"), self)
        self.title_label.setObjectName("titleBarLabel")

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setProperty("level", "info")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.language_combo = QComboBox(self)
        self._populate_language_combo()
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

        self.theme_button = QPushButton(self)
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(QSize(THEME_BUTTON_SIZE, THEME_BUTTON_SIZE))
        self.theme_button.setIconSize(QSize(14, 14))
        self.theme_button.clicked.connect(self._on_theme_button_clicked)

        self.minimize_button = QPushButton(self)
        self.maximize_button = QPushButton(self)
        self.close_button = QPushButton(self)
        self.minimize_button.setObjectName("windowControlButton")
        self.maximize_button.setObjectName("windowControlButton")
        self.close_button.setObjectName("windowControlButton_close")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setFixedSize(QSize(WINDOW_BUTTON_SIZE, WINDOW_BUTTON_SIZE))
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))

        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(self.language_combo)
        layout.addWidget(self.theme_button)
        layout.addSpacing(4)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.update_window_state(False)
        self.apply_theme()

    # ------------------------------------------------------------- icons ----
    def _create_svg_icon(self, svg_data: str, color: str, size: int = ICON_SIZE) -> QIcon:
        if not QSvgRenderer:
            return QIcon()
        renderer = QSvgRenderer(QByteArray(svg_data.format(color=color).encode("utf-8")))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def apply_theme(self) -> None:
        """Re-render every icon and tooltip for the active palette."""
        palette = self.theme.palette()
        text_color = palette.text
        self.minimize_button.setIcon(self._create_svg_icon(SVG_MINIMIZE, text_color))
        self.close_button.setIcon(self._create_svg_icon(SVG_CLOSE, text_color))
        self.update_window_state(self._is_maximized())

        mode = self.theme.mode()
        self.theme_button.setIcon(
            self._create_svg_icon(_THEME_ICONS.get(mode, SVG_THEME_SYSTEM), palette.text_muted, 14)
        )
        self.theme_button.setToolTip(tr(
            "theme_button_tooltip",
            mode=tr(f"theme_mode_{mode}"),
        ))

    def update_window_state(self, is_maximized: bool) -> None:
        color = self.theme.palette().text
        if is_maximized:
            self.maximize_button.setIcon(self._create_svg_icon(SVG_RESTORE, color))
            self.maximize_button.setToolTip(tr("restore_button_tooltip"))
        else:
            self.maximize_button.setIcon(self._create_svg_icon(SVG_MAXIMIZE, color))
            self.maximize_button.setToolTip(tr("maximize_button_tooltip"))

    def _main_window(self):
        """Nearest QMainWindow above this bar (the layout reparents it to the container)."""
        window = self.window()
        return window if isinstance(window, QMainWindow) else None

    def _is_maximized(self) -> bool:
        window = self._main_window()
        return bool(window.isMaximized()) if window else False

    # ---------------------------------------------------------- controls ----
    @Slot()
    def _on_theme_button_clicked(self) -> None:
        self.theme.cycle()
        window = self._main_window()
        if window is not None:
            window.set_transient_status(
                tr("theme_changed_status", mode=tr(f"theme_mode_{self.theme.mode()}")), 1500
            )

    def _populate_language_combo(self) -> None:
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        current_lang_code = get_current_language()
        available_langs = get_available_languages()

        current_idx = 0
        codes_in_order = sorted(available_langs.keys())

        for i, code in enumerate(codes_in_order):
            self.language_combo.addItem(available_langs[code], code)
            if code == current_lang_code:
                current_idx = i

        if codes_in_order:
            self.language_combo.setCurrentIndex(current_idx)
        self.language_combo.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        new_lang_code = self.language_combo.itemData(index)
        if new_lang_code and new_lang_code != get_current_language():
            self.settings_manager.set_language(new_lang_code)

    def retranslate_ui(self) -> None:
        self.title_label.setText(tr("window_title"))
        self._populate_language_combo()
        self.update_window_state(self._is_maximized())
        self.apply_theme()


__all__ = ["CustomTitleBar", "ICON_SIZE", "WINDOW_BUTTON_SIZE", "THEME_BUTTON_SIZE", "LOGO_SIZE"]
