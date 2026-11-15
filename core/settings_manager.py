# -*- coding: utf-8 -*-
"""
Manages global application settings that are unrelated to profiles.
"""
import os
import sys
from typing import Optional

from gui.qt import QObject, Signal
from .state import AppState
from .profile_manager import ProfileManager
from tools.task_scheduler import create_startup_task, delete_startup_task

class SettingsManager(QObject):
    """Manages global application settings."""
    
    def __init__(self, app_state: AppState, profile_manager: ProfileManager, parent: Optional[QObject] = None):
        """
        Initialize SettingsManager.

        Args:
            app_state: Reference to the application's global state object.
            profile_manager: ProfileManager instance used to trigger save operations.
        """
        super().__init__(parent)
        self.state = app_state
        self.profile_manager = profile_manager

    def set_language(self, lang_code: str):
        """Set the application language and save the config."""
        if self.state.get_language() != lang_code:
            self.state.set_language(lang_code)
            self.profile_manager.save_config()

    def set_start_on_boot(self, enabled: bool):
        """Enable or disable the start-on-boot task."""
        if os.name != 'nt': return
        try:
            if enabled:
                create_startup_task(self.state.paths)
            else:
                delete_startup_task()
            
            if self.state.get_start_on_boot() != enabled:
                self.state.set_start_on_boot(enabled)
                self.profile_manager.save_config()

        except Exception as e:
            print(f"Error configuring start on boot: {e}", file=sys.stderr)
            # On failure, restore the UI state
            self.state.set_start_on_boot(not enabled)
            
    def set_window_geometry(self, geometry: str):
        """Store the window geometry and save the config."""
        if self.state.window_geometry != geometry:
            self.state.window_geometry = geometry
            self.profile_manager.save_config()

    def set_active_curve_type(self, curve_type: str):
        """Set the curve type shown as active in the UI."""
        self.state.set_active_curve_type(curve_type)