# -*- coding: utf-8 -*-
"""
Battery control panel QFrame - a self-contained view component.
Inherits from BaseControlPanel and provides the battery-control-specific UI configuration.
"""
from .qt import QWidget
from .base_control_panel import BaseControlPanel
from core.state import AppState
from core.profile_manager import ProfileManager
from config.settings import CHARGE_POLICY_CUSTOM, MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT
from typing import Optional

class BatteryControlPanel(BaseControlPanel):
    """Dedicated view component for battery control."""

    def __init__(self, state: AppState, profile_manager: ProfileManager, parent: Optional[QWidget] = None):
        config = {
            "mode_label_key": "charge_policy_label",
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