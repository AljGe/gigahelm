# -*- coding: utf-8 -*-
"""
Battery care card - self-contained view component.

Inherits from BaseControlPanel and adds the battery-specific live row: the
applied charge limit plus battery health and cycle count.
"""

from typing import Dict, Optional

from .qt import QComboBox, QHBoxLayout, QLabel, QWidget
from .base_control_panel import BaseControlPanel
from core.state import AppState
from core.profile_manager import ProfileManager
from config.settings import CHARGE_POLICY_CUSTOM, MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT
from tools.localization import tr


class BatteryControlPanel(BaseControlPanel):
    """Dedicated view component for battery control."""

    def __init__(self, state: AppState, profile_manager: ProfileManager, parent: Optional[QWidget] = None):
        config = {
            "card_title_key": "card_battery_title",
            "card_desc_key": "card_battery_desc",
            "radio_configs": [("bios", "mode_bios"), ("custom", "mode_custom")],
            "slider_label_key": "charge_threshold_label",
            "slider_range": (MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT),
            "custom_mode_name": CHARGE_POLICY_CUSTOM,
            # Desired state (written to the profile)
            "profile_mode_attr": "battery_charge_policy",
            "profile_value_attr": "battery_charge_threshold",
            # Applied state (read for display)
            "app_state_mode_attr": "applied_charge_policy",
            "app_state_value_attr": "applied_charge_threshold",
            "mode_tooltip_key": "charge_policy_tooltip",
            "slider_tooltip_key": "charge_threshold_tooltip",
            "is_fan_control": False,
        }
        super().__init__(config, state, profile_manager, parent)
        self._build_binding_row()

    def _build_binding_row(self) -> None:
        """Per-power-source profile bindings: switch profiles automatically
        when the machine is plugged in / runs on battery (Off = disabled)."""
        self._binding_combos: Dict[str, QComboBox] = {}
        row = QHBoxLayout()
        for label_key, attr in (
            ("power_source_ac_label", "profile_on_ac"),
            ("power_source_battery_label", "profile_on_battery"),
        ):
            label = QLabel(tr(label_key))
            combo = self._make_binding_combo(attr)
            row.addWidget(label)
            row.addWidget(combo, 1)
            self._binding_combos[attr] = combo
        self.layout().addLayout(row)
        self.state.profiles_list_changed.connect(lambda *_: self._refresh_binding_combos())

    def _make_binding_combo(self, attr: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("power_source_off"), "")
        for name in self.state.get_profile_names():
            combo.addItem(name, name)
        current = getattr(self.state, f"get_{attr}")()
        if current:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.activated.connect(lambda _index, combo=combo, attr=attr: self._on_binding_selected(attr, combo))
        return combo

    def _refresh_binding_combos(self) -> None:
        for attr, combo in self._binding_combos.items():
            selected = combo.currentData() or ""
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("power_source_off"), "")
            for name in self.state.get_profile_names():
                combo.addItem(name, name)
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _on_binding_selected(self, attr: str, combo: QComboBox) -> None:
        getattr(self.state, f"set_{attr}")(combo.currentData() or "")
        self.profile_manager.save_config()

    def _connect_live_signals(self) -> None:
        self.state.applied_charge_policy_changed.connect(self._on_live_changed)
        self.state.applied_charge_threshold_changed.connect(self._on_live_changed)
        self.state.battery_health_changed.connect(self._on_live_changed)
        self.state.battery_cycles_changed.connect(self._on_live_changed)

    def _on_live_changed(self, *_args) -> None:
        self._update_live_display()

    def _update_live_display(self) -> None:
        if self.state.get_applied_charge_policy() == CHARGE_POLICY_CUSTOM:
            self.live_value_label.setText(
                f"{self.state.get_applied_charge_threshold()}{tr('percent_unit')}"
            )
        else:
            self.live_value_label.setText(tr("mode_bios"))

        self.live_line_label.setText(tr(
            "battery_display_format",
            health=self._optional_count(self.state.get_battery_health()),
            cycles=self._optional_count(self.state.get_battery_cycles()),
        ))
