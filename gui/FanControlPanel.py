# -*- coding: utf-8 -*-
"""
Fan control card - self-contained view component.

Inherits from BaseControlPanel and adds the fan-specific live row: the speed
the EC is currently applying plus the detail line (curve target / EC raw).
"""

from typing import Optional

from .qt import QWidget, QCheckBox, QHBoxLayout, Slot, Qt
from .base_control_panel import BaseControlPanel
from .tooltip_manager import tooltip_manager
from core.state import AppState
from core.profile_manager import ProfileManager
from core.fan_duty import effective_percent
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
        split_row = QHBoxLayout()
        split_row.setContentsMargins(0, 0, 0, 0)
        self.split_checkbox = QCheckBox(tr("split_fan_checkbox_label"), self)
        tooltip_manager.register(self.split_checkbox, "split_fan_checkbox_tooltip")
        split_row.addWidget(self.split_checkbox)
        split_row.addStretch(1)
        self.layout().addLayout(split_row)
        self.detune_checkbox = QCheckBox(tr("detune_checkbox_label"), self)
        tooltip_manager.register(self.detune_checkbox, "detune_checkbox_tooltip")
        detune_row = QHBoxLayout()
        detune_row.setContentsMargins(0, 0, 0, 0)
        detune_row.addWidget(self.detune_checkbox)
        detune_row.addStretch(1)
        self.layout().addLayout(detune_row)
        self.assist_checkbox = QCheckBox(tr("assist_checkbox_label"), self)
        tooltip_manager.register(self.assist_checkbox, "assist_checkbox_tooltip")
        assist_row = QHBoxLayout()
        assist_row.setContentsMargins(0, 0, 0, 0)
        assist_row.addWidget(self.assist_checkbox)
        assist_row.addStretch(1)
        self.layout().addLayout(assist_row)
        self.twin_wake_checkbox = QCheckBox(tr("twin_wake_checkbox_label"), self)
        tooltip_manager.register(self.twin_wake_checkbox, "twin_wake_checkbox_tooltip")
        twin_wake_row = QHBoxLayout()
        twin_wake_row.setContentsMargins(0, 0, 0, 0)
        twin_wake_row.addWidget(self.twin_wake_checkbox)
        twin_wake_row.addStretch(1)
        self.layout().addLayout(twin_wake_row)

    def _connect_live_signals(self) -> None:
        self.state.applied_fan_mode_changed.connect(self._on_live_changed)
        self.state.applied_fan_speed_percent_changed.connect(self._on_live_changed)
        self.state.auto_fan_target_speed_percent_changed.connect(self._on_live_changed)
        self.state.ec_fixed_fan_speed_raw_changed.connect(self._on_live_changed)
        self.ff_checkbox.toggled.connect(self._on_ff_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_ff_checkbox())
        self._refresh_ff_checkbox()
        self.split_checkbox.toggled.connect(self._on_split_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_split_checkbox())
        self._refresh_split_checkbox()
        self.detune_checkbox.toggled.connect(self._on_detune_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_detune_checkbox())
        self._refresh_detune_checkbox()
        self.assist_checkbox.toggled.connect(self._on_assist_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_assist_checkbox())
        self._refresh_assist_checkbox()
        self.twin_wake_checkbox.toggled.connect(self._on_twin_wake_toggled)
        self.state.active_profile_changed.connect(lambda *_: self._refresh_twin_wake_checkbox())
        self._refresh_twin_wake_checkbox()

    @Slot(bool)
    def _on_ff_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("gpu_power_feedforward", checked)

    def _refresh_ff_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("gpu_power_feedforward", True)) if profile else True
        self.ff_checkbox.blockSignals(True)
        self.ff_checkbox.setChecked(checked)
        self.ff_checkbox.blockSignals(False)

    @Slot(bool)
    def _on_split_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("split_fan_control", checked)
        # Detune/assist act only under split control; refresh their gating.
        self._apply_enablement()

    def _refresh_split_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("split_fan_control", True)) if profile else True
        self.split_checkbox.blockSignals(True)
        self.split_checkbox.setChecked(checked)
        self.split_checkbox.blockSignals(False)

    @Slot(bool)
    def _on_detune_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("anti_beat_detune", checked)

    def _refresh_detune_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("anti_beat_detune", True)) if profile else True
        self.detune_checkbox.blockSignals(True)
        self.detune_checkbox.setChecked(checked)
        self.detune_checkbox.blockSignals(False)

    @Slot(bool)
    def _on_assist_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("fan_load_assist", checked)

    def _refresh_assist_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("fan_load_assist", True)) if profile else True
        self.assist_checkbox.blockSignals(True)
        self.assist_checkbox.setChecked(checked)
        self.assist_checkbox.blockSignals(False)

    @Slot(bool)
    def _on_twin_wake_toggled(self, checked: bool) -> None:
        self.profile_manager.set_profile_value("fan_twin_wake", checked)

    def _refresh_twin_wake_checkbox(self) -> None:
        profile = self.state.get_active_profile()
        checked = bool(profile.get_value("fan_twin_wake", True)) if profile else True
        self.twin_wake_checkbox.blockSignals(True)
        self.twin_wake_checkbox.setChecked(checked)
        self.twin_wake_checkbox.blockSignals(False)

    def _apply_enablement(self) -> None:
        super()._apply_enablement()
        # The cycle only applies the offset in auto mode.
        enabled = self._mode == FAN_MODE_AUTO
        self.ff_checkbox.setEnabled(enabled)
        if enabled:
            self.ff_checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, False)
            self.ff_checkbox.setToolTip("")
            tooltip_manager.register(self.ff_checkbox, "ff_checkbox_tooltip")
        else:
            # Disabled widgets only show tooltips with WA_AlwaysShowToolTips.
            tooltip_manager.unregister(self.ff_checkbox)
            self.ff_checkbox.setToolTip(tr("ff_disabled_not_auto"))
            self.ff_checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        # Split control is auto-mode-only: custom mode drives both channels
        # with one slider, BIOS owns the fans.
        self.split_checkbox.setEnabled(enabled)
        if enabled:
            self.split_checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, False)
            self.split_checkbox.setToolTip("")
            tooltip_manager.register(self.split_checkbox, "split_fan_checkbox_tooltip")
        else:
            tooltip_manager.unregister(self.split_checkbox)
            self.split_checkbox.setToolTip(tr("split_disabled_not_auto"))
            self.split_checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        # Detune/assist only act in split auto mode: custom mode drives both
        # channels with one slider, mirrored mode couples them.
        shaping_enabled = enabled and self.split_checkbox.isChecked()
        for checkbox in (self.detune_checkbox, self.assist_checkbox,
                         self.twin_wake_checkbox):
            checkbox.setEnabled(shaping_enabled)
            if shaping_enabled:
                checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, False)
                checkbox.setToolTip("")
            else:
                tooltip_manager.unregister(checkbox)
                checkbox.setToolTip(tr("shaping_disabled_not_split"))
                checkbox.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)

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
            self.live_value_label.setText(
                f"{effective_percent(applied)}{tr('percent_unit')}")
            self.live_line_label.setText(tr(
                "fan_display_auto_format",
                target=effective_percent(self.state.get_auto_fan_target_speed_percent()),
                raw=raw,
            ))
        elif mode == FAN_MODE_CUSTOM:
            # percent_to_raw clamps 1-24 % to the EC's 25 % minimum-spin
            # duty; display the duty the fans actually get.
            self.live_value_label.setText(
                f"{effective_percent(applied)}{tr('percent_unit')}")
            self.live_line_label.setText(tr("fan_display_custom_format", raw=raw))
        else:
            self.live_value_label.setText(tr("mode_bios"))
            self.live_line_label.setText(tr("fan_display_bios_format"))

    def retranslate_ui(self) -> None:
        super().retranslate_ui()
        self.ff_checkbox.setText(tr("ff_checkbox_label"))
        self.split_checkbox.setText(tr("split_fan_checkbox_label"))
        self.twin_wake_checkbox.setText(tr("twin_wake_checkbox_label"))
