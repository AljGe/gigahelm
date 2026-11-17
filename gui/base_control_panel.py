# -*- coding: utf-8 -*-
"""
BaseControlPanel: the shared vertical card behind fan and battery control.

The card shows, from top to bottom, a title, a one-line description, a
segmented mode switch, the value the hardware is *currently* using, and the
custom-speed slider. User intent flows to `ProfileManager`; display state is
driven exclusively by `AppState` so the UI can never disagree with the EC.
"""

from typing import Any, Dict, Optional

from .qt import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QTimer, Qt, Slot,
    QSizePolicy,
)
from .tooltip_manager import tooltip_manager
from .segmented_control import SegmentedControl
from .EditableLabel import EditableLabel
from tools.localization import tr
from core.state import AppState
from core.profile_manager import ProfileManager


class BaseControlPanel(QFrame):
    """Vertical control card; subclasses supply the config dict and live text."""

    def __init__(
        self,
        config: Dict[str, Any],
        state: AppState,
        profile_manager: ProfileManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.profile_manager = profile_manager
        self._mode: str = ""

        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self.slider_debounce_timer = QTimer(self)
        self.slider_debounce_timer.setSingleShot(True)
        self.slider_debounce_timer.setInterval(300)
        self._pending_slider_value: Optional[int] = None

        self._init_ui()
        self._connect_signals()
        self._refresh_from_app_state()

    # ------------------------------------------------------------------ UI ---
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.card_title_label = QLabel(tr(self.config["card_title_key"]), self)
        self.card_title_label.setObjectName("cardTitle")
        layout.addWidget(self.card_title_label)

        self.card_desc_label = QLabel(tr(self.config["card_desc_key"]), self)
        self.card_desc_label.setObjectName("cardDesc")
        self.card_desc_label.setWordWrap(True)
        layout.addWidget(self.card_desc_label)

        self.segmented = SegmentedControl(self)
        for mode_name, text_key in self.config["radio_configs"]:
            self.segmented.add_segment(mode_name, tr(text_key))
        self.segmented.set_tooltip_key(self.config["mode_tooltip_key"])
        layout.addWidget(self.segmented)

        self.live_value_label = QLabel("", self)
        self.live_value_label.setObjectName("liveValue")
        layout.addWidget(self.live_value_label)

        self.live_line_label = QLabel("", self)
        self.live_line_label.setObjectName("liveLine")
        self.live_line_label.setWordWrap(True)
        layout.addWidget(self.live_line_label)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(8)

        self.slider_label = QLabel(tr(self.config["slider_label_key"]), self)
        self.slider_label.setObjectName("statFooter")
        slider_row.addWidget(self.slider_label)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(*self.config["slider_range"])
        slider_row.addWidget(self.slider, 1)

        self.value_label = EditableLabel(unit=tr("percent_unit"), parent=self)
        self.value_label.setObjectName("valuePill")
        self.value_label.label.setObjectName("valuePill")
        self.value_label.setMinimumWidth(48)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider_row.addWidget(self.value_label)

        layout.addLayout(slider_row)

        tooltip_manager.register(self.slider, self.config["slider_tooltip_key"])
        tooltip_manager.register(self.value_label, "editable_label_tooltip")

    def _connect_signals(self) -> None:
        # UI -> ProfileManager (user intent)
        self.segmented.valueChanged.connect(self._on_mode_selected)
        self.slider.valueChanged.connect(self._on_slider_value_changed)
        self.slider_debounce_timer.timeout.connect(self._commit_slider_value)
        self.value_label.editingFinished.connect(self._commit_label_value)

        # AppState (hardware truth) -> UI
        getattr(self.state, self.config["app_state_mode_attr"] + "_changed").connect(
            self._update_mode_display
        )
        getattr(self.state, self.config["app_state_value_attr"] + "_changed").connect(
            self._update_value_display
        )
        self.state.active_profile_changed.connect(self._on_active_profile_changed)

        if self.config.get("is_fan_control", False):
            self.state.is_fan_control_panel_enabled_changed.connect(
                self._on_fan_control_enabled_changed
            )

        self._connect_live_signals()

    def _connect_live_signals(self) -> None:
        """Hook for subclasses: subscribe to the signals their live row shows."""

    # ------------------------------------------------------------- intent ---
    @Slot()
    def _on_active_profile_changed(self, *_args) -> None:
        # A pending slider commit must not land in the profile that just became
        # active (AC/battery binding switches mid-drag write into the wrong profile).
        self._pending_slider_value = None
        self.slider_debounce_timer.stop()

    @Slot(str)
    def _on_mode_selected(self, mode_name: str) -> None:
        self.profile_manager.update_active_profile_data(self.config["profile_mode_attr"], mode_name)

    @Slot(int)
    def _on_slider_value_changed(self, value: int) -> None:
        self.value_label.blockSignals(True)
        self.value_label.setValue(value)
        self.value_label.blockSignals(False)
        # Capture the user's intent: an applied-value pushback may rewrite the
        # slider position before the debounce fires (EC readback of the old
        # value), and the late commit must not persist that rollback.
        self._pending_slider_value = value
        self.slider_debounce_timer.start()

    @Slot()
    def _commit_slider_value(self) -> None:
        self.slider_debounce_timer.stop()
        value = self._pending_slider_value
        self._pending_slider_value = None
        self._commit_value(value if value is not None else self.slider.value())

    @Slot(int)
    def _commit_label_value(self, value: int) -> None:
        self.slider_debounce_timer.stop()
        self._pending_slider_value = None
        min_val, max_val = self.config["slider_range"]
        self._commit_value(max(min_val, min(max_val, value)))

    def _commit_value(self, final_value: int) -> None:
        self.profile_manager.update_active_profile_data(self.config["profile_value_attr"], final_value)

    # ------------------------------------------------------------ display ---
    def _refresh_from_app_state(self) -> None:
        """Push every currently known hardware value into the card."""
        mode_getter = getattr(self.state, "get_" + self.config["app_state_mode_attr"])
        value_getter = getattr(self.state, "get_" + self.config["app_state_value_attr"])
        self._apply_mode(mode_getter())
        self._apply_value(value_getter())
        self._update_live_display()

    @Slot(str)
    def _update_mode_display(self, mode: str) -> None:
        self._apply_mode(mode)
        self._update_live_display()

    @Slot(int)
    def _update_value_display(self, value: int) -> None:
        if value < 0:
            return  # -1 means "no readback yet"; keep the last known value
        self._apply_value(value)
        self._update_live_display()

    @Slot(bool)
    def _on_fan_control_enabled_changed(self, enabled: bool) -> None:
        self._apply_enablement()
        self._update_live_display()

    def _apply_mode(self, mode: str) -> None:
        self._mode = mode
        self.segmented.set_value(mode)
        self._apply_enablement()

    def _apply_enablement(self) -> None:
        """The mode selector must stay usable in BIOS mode so the user can leave it."""
        in_custom_mode = self._mode == self.config["custom_mode_name"]
        slider_enabled = in_custom_mode

        # Never disable the segmented control: BIOS is a mode, not a dead end.
        self.segmented.setEnabled(True)
        self.slider_label.setEnabled(slider_enabled)
        self.slider.setEnabled(slider_enabled)
        self.value_label.setEnabled(slider_enabled)

    def _apply_value(self, value: int) -> None:
        if value < 0:
            return
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_label.blockSignals(True)
        self.value_label.setValue(value)
        self.value_label.blockSignals(False)

    def _update_live_display(self) -> None:
        """Refresh the big value and detail line; overridden by the two cards."""
        if self.config.get("is_fan_control", False) and not self.state.get_is_fan_control_panel_enabled():
            self.live_value_label.setText(tr("fan_unavailable_text"))
            self.live_line_label.setText("")
            return
        value_getter = getattr(self.state, "get_" + self.config["app_state_value_attr"])
        value = value_getter()
        text = tr("value_not_available") if value < 0 else f"{value}{tr('percent_unit')}"
        self.live_value_label.setText(text)
        self.live_line_label.setText("")

    @staticmethod
    def _optional_count(value: int) -> str:
        """Format an optional integer that uses -1 for 'unknown'."""
        return tr("value_not_available") if value < 0 else str(value)

    # ------------------------------------------------------------- i18n ----
    def retranslate_ui(self) -> None:
        self.card_title_label.setText(tr(self.config["card_title_key"]))
        self.card_desc_label.setText(tr(self.config["card_desc_key"]))
        for mode_name, text_key in self.config["radio_configs"]:
            self.segmented.set_segment_text(mode_name, tr(text_key))
        self.slider_label.setText(tr(self.config["slider_label_key"]))
        self.value_label.unit = tr("percent_unit")
        self._refresh_from_app_state()
