# -*- coding: utf-8 -*-
"""
Fan control panel QFrame - a self-contained view component.
Inherits from BaseControlPanel and provides the fan-control-specific UI configuration.
"""
from .qt import QWidget
from .base_control_panel import BaseControlPanel
from core.state import AppState
from core.profile_manager import ProfileManager
from config.settings import FAN_MODE_CUSTOM
from typing import Optional

class FanControlPanel(BaseControlPanel):
    """Dedicated view component for fan control."""
    
    def __init__(self, state: AppState, profile_manager: ProfileManager, parent: Optional[QWidget] = None):
        config = {
            "mode_label_key": "fan_mode_label",
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