# -*- coding: utf-8 -*-
"""
Manages global application settings that are unrelated to profiles.
"""
import os
import sys
import threading
from typing import Optional

from gui.qt import QObject, Signal
from .state import AppState
from .profile_manager import ProfileManager
from tools.task_scheduler import create_startup_task, delete_startup_task

class SettingsManager(QObject):
    """Manages global application settings."""

    #: (is_current, enabled, ok, error) - outcome of one start-on-boot task
    #: mutation, delivered on the GUI thread. is_current rejects stale
    #: outcomes when the switch was flipped again while a task ran.
    start_on_boot_task_finished = Signal(bool, bool, bool, str)

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
        # One schtasks mutation at a time; the seq rejects stale outcomes.
        self._boot_task_lock = threading.Lock()
        self._boot_task_seq = 0
        self.start_on_boot_task_finished.connect(self._on_start_on_boot_task_finished)

    def set_language(self, lang_code: str):
        """Set the application language and save the config."""
        if self.state.get_language() != lang_code:
            self.state.set_language(lang_code)
            self.profile_manager.save_config()

    def set_theme_mode(self, mode: str):
        """Set the appearance theme mode and save the config."""
        if self.state.get_theme_mode() != mode:
            self.state.set_theme_mode(mode)
            self.profile_manager.save_config()

    def set_notifications_enabled(self, enabled: bool):
        """Persist the action-balloon preference."""
        if self.state.get_notifications_enabled() != enabled:
            self.state.set_notifications_enabled(enabled)
            self.profile_manager.save_config()

    def set_start_on_boot(self, enabled: bool):
        """Enable or disable the start-on-boot task.

        Each schtasks spawn costs 0.1-1 s and there can be three per toggle,
        so the work runs on a worker thread; the state (and checkbox) is
        only updated when the outcome of the newest request arrives.
        """
        if os.name != 'nt':
            return
        self._boot_task_seq += 1
        seq = self._boot_task_seq

        def run():
            try:
                with self._boot_task_lock:
                    if enabled:
                        create_startup_task(self.state.paths)
                    else:
                        delete_startup_task()
                ok, error = True, ""
            except Exception as e:
                ok, error = False, str(e)
            self.start_on_boot_task_finished.emit(seq == self._boot_task_seq, enabled, ok, error)

        threading.Thread(target=run, name="StartOnBootTask", daemon=True).start()

    def _on_start_on_boot_task_finished(self, is_current: bool, enabled: bool, ok: bool, error: str):
        if not is_current:
            return  # a newer switch position superseded this request
        if not ok:
            print(f"Error configuring start on boot: {error}", file=sys.stderr)
            # Restore the UI to the previous switch position.
            self.state.set_start_on_boot(not enabled)
            return
        if self.state.get_start_on_boot() != enabled:
            self.state.set_start_on_boot(enabled)
            self.profile_manager.save_config()
            
    def set_window_geometry(self, geometry: str):
        """Store the window geometry and save the config."""
        if self.state.window_geometry != geometry:
            self.state.window_geometry = geometry
            self.profile_manager.save_config()

    def set_active_curve_type(self, curve_type: str):
        """Set the curve type shown as active in the UI."""
        self.state.set_active_curve_type(curve_type)

    def set_gui_update_interval_ms(self, interval_ms: int):
        """UI refresh cadence is a global app setting, not a profile value."""
        if interval_ms not in (250, 500, 1000, 2000):
            return
        if self.state.get_gui_update_interval_ms() != interval_ms:
            self.state.set_gui_update_interval_ms(interval_ms)
            self.profile_manager.save_config()