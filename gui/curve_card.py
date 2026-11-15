# -*- coding: utf-8 -*-
"""
CurveCard: the big fan-curve editor card.

Owns the CPU/GPU switch, the per-profile chips (left click activates, right
click opens the actions menu, `+` creates) and the canvas itself. Profile
management is discoverable: nothing depends on hidden click gestures.
"""

from typing import Callable, List, Optional

from .qt import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu,
    QInputDialog, QLineEdit, QMessageBox, QDialog, QSizePolicy,
    QContextMenuEvent, QPoint, Signal, Slot,
)
from .segmented_control import SegmentedControl
from .lightweight_curve_canvas import LightweightCurveCanvas
from .RenameProfileDialog import RenameProfileDialog
from .tooltip_manager import tooltip_manager
from .theme import ThemeManager
from core.state import AppState
from core.profile_manager import ProfileManager
from core.settings_manager import SettingsManager
from tools.localization import tr


class _ProfileChip(QPushButton):
    """Profile button that reports right clicks to its card."""

    def __init__(self, name: str, on_context_menu: Callable[["_ProfileChip", QPoint], None],
                 parent: Optional[QWidget] = None):
        super().__init__(name, parent)
        self.profile_name = name
        self._on_context_menu = on_context_menu
        self.setObjectName("profileChip")
        self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self._on_context_menu(self, event.globalPos())
        event.accept()


class CurveCard(QFrame):
    """Card holding the profile chips, the CPU/GPU switch and the canvas."""

    transient_status = Signal(str, int)

    def __init__(
        self,
        state: AppState,
        profile_manager: ProfileManager,
        settings_manager: SettingsManager,
        theme: ThemeManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.state = state
        self.profile_manager = profile_manager
        self.settings_manager = settings_manager
        self.theme = theme
        self._chips: List[_ProfileChip] = []

        self.setObjectName("curveCard")
        self._init_ui()
        self._connect_signals()

        self._on_curve_type_changed(self.state.get_active_curve_type())
        self._rebuild_profile_chips(
            self.state.get_profile_names(), self.state.get_active_profile_name()
        )
        self._update_interactive_controls_enablement(self.state.get_is_fan_control_panel_enabled())

    # ------------------------------------------------------------------ UI ---
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.title_label = QLabel(tr("curve_card_title"), self)
        self.title_label.setObjectName("cardTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.curve_switch = SegmentedControl(self)
        self.curve_switch.add_segment("cpu", tr("cpu_curve_button"))
        self.curve_switch.add_segment("gpu", tr("gpu_curve_button"))
        tooltip_manager.register(self.curve_switch, "curve_shared_duty_tooltip")
        header.addWidget(self.curve_switch)

        self.reset_curve_button = QPushButton(tr("reset_curve_button"), self)
        self.reset_curve_button.setObjectName("resetCurveButton")
        tooltip_manager.register(self.reset_curve_button, "reset_curve_tooltip")
        header.addWidget(self.reset_curve_button)

        layout.addLayout(header)

        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(6)
        self.profile_chips_layout = chips_row
        layout.addLayout(chips_row)

        self.add_profile_button = QPushButton("+", self)
        self.add_profile_button.setObjectName("addProfileButton")
        self.add_profile_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        tooltip_manager.register(self.add_profile_button, "add_profile_tooltip")

        self.canvas = LightweightCurveCanvas(self.state, self.profile_manager, self.theme, self)
        layout.addWidget(self.canvas, 1)

    def _connect_signals(self) -> None:
        self.curve_switch.valueChanged.connect(self.settings_manager.set_active_curve_type)
        self.reset_curve_button.clicked.connect(self.profile_manager.reset_active_curve)
        self.add_profile_button.clicked.connect(self._handle_add_profile_clicked)

        self.state.active_curve_type_changed.connect(self._on_curve_type_changed)
        self.state.profiles_list_changed.connect(self._rebuild_profile_chips)
        self.state.is_fan_control_panel_enabled_changed.connect(
            self._update_interactive_controls_enablement
        )
        self.canvas.transient_status_signal.connect(self.transient_status.emit)

    # -------------------------------------------------------------- chips ----
    @Slot(list, str)
    def _rebuild_profile_chips(self, profile_names: List[str], active_profile_name: Optional[str]) -> None:
        """Rebuild the chip row from scratch; no index arithmetic."""
        while self.profile_chips_layout.count():
            item = self.profile_chips_layout.takeAt(0)
            widget = item.widget()
            if widget is None or widget is self.add_profile_button:
                continue
            tooltip_manager.unregister(widget)
            widget.deleteLater()
        self._chips.clear()

        for name in profile_names:
            chip = _ProfileChip(name, self._show_profile_menu, self)
            chip.setChecked(name == active_profile_name)
            chip.clicked.connect(lambda _checked=False, profile=name: self.profile_manager.activate_profile(profile))
            tooltip_manager.register(chip, "profile_chip_tooltip")
            self._chips.append(chip)
            self.profile_chips_layout.addWidget(chip)

        self.profile_chips_layout.addWidget(self.add_profile_button)
        self.profile_chips_layout.addStretch(1)

    def _show_profile_menu(self, chip: _ProfileChip, global_pos: QPoint) -> None:
        name = chip.profile_name
        active_name = self.state.get_active_profile_name()

        menu = QMenu(self)
        activate_action = menu.addAction(tr("profile_menu_activate"))
        activate_action.setEnabled(name != active_name)
        save_action = menu.addAction(tr("profile_menu_save"))
        menu.addSeparator()
        manage_action = menu.addAction(tr("profile_menu_manage"))

        chosen = menu.exec(global_pos)
        if chosen is activate_action:
            self.profile_manager.activate_profile(name)
        elif chosen is save_action:
            self._save_profile_into(name)
        elif chosen is manage_action:
            self._manage_profile(name)

    def _save_profile_into(self, name: str) -> None:
        """Copy the active profile's settings into the clicked profile."""
        active_profile = self.profile_manager.get_active_profile()
        target_profile = self.state.get_profile(name)
        if not active_profile or not target_profile:
            return
        target_profile.from_dict(active_profile.to_dict())
        self.profile_manager.save_config()
        self.transient_status.emit(tr("profile_saved_message", profile_name=name), 2000)

    def _manage_profile(self, old_name: str) -> None:
        dialog = RenameProfileDialog(old_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.result_action == "rename":
            new_name = dialog.new_name()
            if new_name and new_name != old_name:
                if self.state.get_profile(new_name):
                    QMessageBox.warning(
                        self,
                        tr("add_profile_error_title"),
                        tr("add_profile_duplicate_name_error", name=new_name),
                    )
                else:
                    self.profile_manager.rename_profile(old_name, new_name)
        elif dialog.result_action == "delete":
            self.profile_manager.delete_profile(old_name)

    @Slot()
    def _handle_add_profile_clicked(self) -> None:
        new_name, ok = QInputDialog.getText(
            self, tr("add_profile_title"), tr("add_profile_label"), QLineEdit.EchoMode.Normal, ""
        )
        if not ok or not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, tr("add_profile_error_title"), tr("add_profile_empty_name_error"))
            return
        if self.state.get_profile(new_name):
            QMessageBox.warning(
                self, tr("add_profile_error_title"),
                tr("add_profile_duplicate_name_error", name=new_name),
            )
            return
        self.profile_manager.create_new_profile(new_name)

    # ------------------------------------------------------------ display ----
    @Slot(str)
    def _on_curve_type_changed(self, curve_type: str) -> None:
        self.curve_switch.set_value(curve_type)

    @Slot(bool)
    def _update_interactive_controls_enablement(self, is_enabled: bool) -> None:
        # Curve editing edits the profile, not the EC; it must work in BIOS mode too.
        self.reset_curve_button.setEnabled(True)
        self.curve_switch.setEnabled(True)
        self.canvas.setEnabled(True)

    # --------------------------------------------------------------- i18n ----
    def retranslate_ui(self) -> None:
        self.title_label.setText(tr("curve_card_title"))
        self.curve_switch.set_segment_text("cpu", tr("cpu_curve_button"))
        self.curve_switch.set_segment_text("gpu", tr("gpu_curve_button"))
        self.reset_curve_button.setText(tr("reset_curve_button"))
        self.canvas.retranslate_ui()

    def apply_theme(self) -> None:
        self.canvas.on_theme_changed()
