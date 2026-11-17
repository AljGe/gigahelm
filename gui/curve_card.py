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
    QContextMenuEvent, QPoint, QShortcut, QKeySequence, Qt, Signal, Slot,
    QPainter, QColor, QPen, QPointF,
)
from .flow_layout import FlowLayout
from .segmented_control import SegmentedControl
from .lightweight_curve_canvas import LightweightCurveCanvas
from .curve_history import CurveEditHistory
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


class _LegendSwatch(QWidget):
    """Tiny fixed-size swatch for the curve legend: line (CPU/GPU), hollow
    ring (EMA operating point) or dashed line (safety floor). Reads the
    palette at paint time, so a theme change only needs an update()."""

    def __init__(self, kind: str, theme: ThemeManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._kind = kind
        self._theme = theme
        self.setFixedSize(18, 10)

    def paintEvent(self, event) -> None:
        palette = self._theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        if self._kind in ("cpu", "gpu"):
            color = palette.curve_cpu if self._kind == "cpu" else palette.curve_gpu
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        elif self._kind == "ema":
            painter.setPen(QPen(QColor(palette.text), 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(self.width() / 2, y), 3.2, 3.2)
        else:  # safety floor
            painter.setPen(QPen(QColor(palette.danger), 1.6, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        painter.end()


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

        self.undo_curve_button = QPushButton("\u21b6", self)
        self.undo_curve_button.setObjectName("resetCurveButton")
        self.undo_curve_button.setToolTip(tr("undo_curve_tooltip"))
        self.undo_curve_button.setEnabled(False)
        header.addWidget(self.undo_curve_button)

        self.redo_curve_button = QPushButton("\u21b7", self)
        self.redo_curve_button.setObjectName("resetCurveButton")
        self.redo_curve_button.setToolTip(tr("redo_curve_tooltip"))
        self.redo_curve_button.setEnabled(False)
        header.addWidget(self.redo_curve_button)

        self.reset_curve_button = QPushButton(tr("reset_curve_button"), self)
        self.reset_curve_button.setObjectName("resetCurveButton")
        tooltip_manager.register(self.reset_curve_button, "reset_curve_tooltip")
        header.addWidget(self.reset_curve_button)

        layout.addLayout(header)

        chips_row = FlowLayout(spacing=6)  # adopted by layout.addLayout below
        chips_row.setContentsMargins(0, 0, 0, 0)
        self.profile_chips_layout = chips_row
        layout.addLayout(chips_row)

        self.add_profile_button = QPushButton("+", self)
        self.add_profile_button.setObjectName("addProfileButton")
        self.add_profile_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        tooltip_manager.register(self.add_profile_button, "add_profile_tooltip")

        # Legend: what each colour/shape on the canvas means.
        self._legend_keys = ("legend_cpu_curve", "legend_gpu_curve",
                             "legend_ema_point", "legend_safety_floor")
        self._legend_kinds = ("cpu", "gpu", "ema", "floor")
        self._legend_labels: List[QLabel] = []
        self._legend_swatches: List[_LegendSwatch] = []
        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(14)
        for kind, key in zip(self._legend_kinds, self._legend_keys):
            entry = QHBoxLayout()
            entry.setSpacing(4)
            swatch = _LegendSwatch(kind, self.theme, self)
            label = QLabel(tr(key), self)
            label.setObjectName("legendLabel")
            entry.addWidget(swatch)
            entry.addWidget(label)
            legend_row.addLayout(entry)
            self._legend_labels.append(label)
            self._legend_swatches.append(swatch)
        legend_row.addStretch(1)
        layout.addLayout(legend_row)

        self.canvas = LightweightCurveCanvas(self.state, self.profile_manager, self.theme, self)
        layout.addWidget(self.canvas, 1)

    def _connect_signals(self) -> None:
        self.curve_switch.valueChanged.connect(self.settings_manager.set_active_curve_type)
        self.reset_curve_button.clicked.connect(self._on_reset_clicked)
        self.undo_curve_button.clicked.connect(self._undo_curve_edit)
        self.redo_curve_button.clicked.connect(self._redo_curve_edit)
        self.add_profile_button.clicked.connect(self._handle_add_profile_clicked)

        # Canvas-scoped shortcuts; StandardKey.Redo covers Ctrl+Shift+Z and
        # Ctrl+Y. Widget context keeps the native undo of text fields working.
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self.canvas)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._undo_shortcut.activated.connect(self._undo_curve_edit)
        self._redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self.canvas)
        self._redo_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._redo_shortcut.activated.connect(self._redo_curve_edit)

        self.state.active_curve_type_changed.connect(self._on_curve_type_changed)
        self.state.profiles_list_changed.connect(self._rebuild_profile_chips)
        self.state.is_fan_control_panel_enabled_changed.connect(
            self._update_interactive_controls_enablement
        )
        self.state.active_profile_changed.connect(lambda *_: self._clear_curve_history())
        self.canvas.edit_committed.connect(self._on_curve_edit_committed)
        self.canvas.transient_status_signal.connect(self.transient_status.emit)
        self._history = CurveEditHistory()

    # ------------------------------------------------------------ history ---
    def _on_curve_edit_committed(self, curve_type: str, before: list, after: list) -> None:
        self._history.push(curve_type, before)
        self._sync_history_buttons()

    def _undo_curve_edit(self) -> None:
        self._apply_history_snapshot(self._history.undo(self.canvas.get_curve_data))

    def _redo_curve_edit(self) -> None:
        self._apply_history_snapshot(self._history.redo(self.canvas.get_curve_data))

    def _apply_history_snapshot(self, snapshot) -> None:
        if snapshot is None:
            return
        curve_type, points = snapshot
        self.settings_manager.set_active_curve_type(curve_type)
        self.profile_manager.set_curve_data(curve_type, points)
        self.canvas.transient_status_signal.emit(tr("saving_config"), 2000)
        self._sync_history_buttons()

    def _on_reset_clicked(self) -> None:
        curve_type = self.state.get_active_curve_type()
        before = self.canvas.get_curve_data(curve_type)
        self.profile_manager.reset_active_curve()
        after = self.canvas.get_curve_data(curve_type)
        if [list(p) for p in before] != [list(p) for p in after]:
            self._history.push(curve_type, [list(p) for p in before])
        self._sync_history_buttons()

    def _clear_curve_history(self) -> None:
        self._history.clear()
        self._sync_history_buttons()

    def _sync_history_buttons(self) -> None:
        self.undo_curve_button.setEnabled(self._history.can_undo)
        self.redo_curve_button.setEnabled(self._history.can_redo)

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
        # Overwriting erases the target profile's saved settings; ask first.
        confirm = QMessageBox.question(
            self,
            tr("profile_overwrite_confirm_title"),
            tr("profile_overwrite_confirm_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
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
        for label, key in zip(self._legend_labels, self._legend_keys):
            label.setText(tr(key))
        self.canvas.retranslate_ui()

    def apply_theme(self) -> None:
        for swatch in self._legend_swatches:
            swatch.update()
        self.canvas.on_theme_changed()
