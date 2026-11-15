# -*- coding: utf-8 -*-
"""
Battery care card - self-contained view component.

Inherits from BaseControlPanel and adds the battery-specific live row: the
applied charge limit plus battery health and cycle count.
"""

from typing import Optional

from .qt import QWidget
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
