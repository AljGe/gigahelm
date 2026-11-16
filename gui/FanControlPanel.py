# -*- coding: utf-8 -*-
"""
Fan control card - self-contained view component.

Inherits from BaseControlPanel and adds the fan-specific live row: the speed
the EC is currently applying plus the detail line (curve target / EC raw).
"""

from typing import Optional

from .qt import QWidget, QCheckBox, QHBoxLayout, Slot
from .base_control_panel import BaseControlPanel
from .tooltip_manager import tooltip_manager
from core.state import AppState
from core.profile_manager import ProfileManager
from config.settings import FAN_MODE_AUTO, FAN_MODE_CUSTOM, FAN_MODE_UNKNOWN
from tools.localization import tr


class FanControlPanel(BaseControlPanel):
    """Dedicated view component for fan control."""

    def __init__(self, state: AppState, profile_manager: ProfileManager, parent: Optional[QWidget] = None):
        config = {
            "card_title_key": "card_fan_title",
            "card_desc_key": "card_fan_desc",
            "radio_configs": [("bios", "mode_bios"), ("auto", "mode_auto"), ("custom", "mode_custom")],
            "slider_label_key": "custom_speed_label",
            "slider_range": (0, 100),
            "custom_mode_name": FAN_MODE_CUSTOM,
            # Desired state (written to the profile)
            "profile_mode_attr": "fan_mode",
            "profile_value_attr": "custom_fan_speed",
            # Applied state (read for display)
            "app_state_mode_attr": "applied_fan_mode",
            "app_state_value_attr": "applied_fan_speed_percent",
            "mode_tooltip_key": "fan_mode_tooltip",
            "slider_tooltip_key": "custom_fan_speed_tooltip",
            "is_fan_control": True,
        }
        super().__init__(config, state, profile_manager, parent)

    def _init_ui(self) -> None:
        super()._init_ui()
        ff_row = QHBoxLayout()
        ff_row.setContentsMargins(0, 0, 0, 0)
        self.ff_checkbox = QCheckBox(tr("ff_checkbox_label"), self)
        tooltip_manager.register(self.ff_checkbox, "ff_checkbox_tooltip")
        ff_row.addWidget(self.ff_checkbox)
        ff_row.addStretch(1)
        self.layout().addLayout(ff_row)

    def _connect_live_signals(self) -> None:
        self.state.applied_fan_mode_changed.connect(self._on_live_changed)
        self.state.applied_fan_speed_percent_changed.connect(self._on_live_changed)
        self.state.auto_fan_target_speed_percent_changed.connect(self._on_live_changed)
        self.state.ec_fixed_fan_speed_raw_changed.connect(self._on_live_changed)
        self.ff_checkbox.toggled.connect(self._on_ff_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_ff_checkbox())
        self._refresh_ff_checkbox()

    @Slot(bool)
    def _on_ff_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("gpu_power_feedforward", checked)

    def _refresh_ff_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("gpu_power_feedforward", True)) if profile else True
        self.ff_checkbox.blockSignals(True)
        self.ff_checkbox.setChecked(checked)
        self.ff_checkbox.blockSignals(False)

    def _apply_enablement(self) -> None:
        super()._apply_enablement()
        # The cycle only applies the offset in auto mode.
        self.ff_checkbox.setEnabled(self._mode == FAN_MODE_AUTO)

    def _on_live_changed(self, *_args) -> None:
        self._update_live_display()

    def _update_live_display(self) -> None:
        if not self.state.get_is_fan_control_panel_enabled():
            self.live_value_label.setText(tr("fan_unavailable_text"))
            self.live_line_label.setText("")
            return

        mode = self.state.get_applied_fan_mode()
        raw = self._optional_count(self.state.get_ec_fixed_fan_speed_raw())
        applied = self.state.get_applied_fan_speed_percent()

        if mode == FAN_MODE_UNKNOWN:
            self.live_value_label.setText(tr("fan_unavailable_text"))
            self.live_line_label.setText("")
            return

        if mode == FAN_MODE_AUTO:
            self.live_value_label.setText(f"{applied}{tr('percent_unit')}")
            self.live_line_label.setText(tr(
                "fan_display_auto_format",
                target=self.state.get_auto_fan_target_speed_percent(),
                raw=raw,
            ))
        elif mode == FAN_MODE_CUSTOM:
            self.live_value_label.setText(f"{applied}{tr('percent_unit')}")
            self.live_line_label.setText(tr("fan_display_custom_format", raw=raw))
        else:
            self.live_value_label.setText(tr("mode_bios"))
            self.live_line_label.setText(tr("fan_display_bios_format"))

    def retranslate_ui(self) -> None:
        super().retranslate_ui()
        self.ff_checkbox.setText(tr("ff_checkbox_label"))
