# -*- coding: utf-8 -*-
"""
Manages all profile-related state and persistence logic.
This class encapsulates loading, saving, creating, renaming, and activating profiles.
"""
import os
import json
import copy
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List

from gui.qt import QObject, QTimer, Signal
from .state import AppState, ProfileState
from config.settings import DEFAULT_PROFILE_SETTINGS, DEFAULT_PROFILES, DEFAULT_THEME_MODE
from tools.localization import tr

class ProfileManager(QObject):
    """Central class that manages the application's profiles."""

    #: Emitted from the writer thread when changes landed during a write.
    _resave_needed = Signal()

    def __init__(self, app_state: AppState, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.state = app_state
        self.config_path = self.state.paths.control_config
        # Debounced background persistence: save_config() only arms a timer;
        # the JSON write runs on a daemon thread so a slow disk never blocks
        # the GUI. save_config_sync() flushes inline (shutdown path).
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_now)
        self._write_thread: Optional[threading.Thread] = None
        self._resave_pending = False
        self._resave_needed.connect(self._save_now)

    def load_config(self):
        """Load the config from the JSON file and populate state."""
        config_data = self._read_config_file()

        # One-time migration: the UI refresh interval moved from per-profile
        # storage to a global app setting. Top-level key wins; the active
        # profile's legacy value is read before from_dict drops it.
        interval = config_data.get("gui_update_interval_ms")
        if interval not in (250, 500, 1000, 2000):
            active_raw = config_data.get("profiles", {}).get(config_data.get("active_profile_name"), {})
            interval = active_raw.get("gui_update_interval_ms") if isinstance(active_raw, dict) else None
        if interval not in (250, 500, 1000, 2000):
            interval = 1000
        self.state.set_gui_update_interval_ms(int(interval))

        self.state.set_language(config_data.get("language", "en"))
        self.state.set_theme_mode(config_data.get("theme_mode", DEFAULT_THEME_MODE))
        self.state.set_start_on_boot(config_data.get("start_on_boot", False))
        self.state.set_notifications_enabled(config_data.get("notifications_enabled", True))
        self.state.set_profile_on_ac(config_data.get("profile_on_ac", ""))
        self.state.set_profile_on_battery(config_data.get("profile_on_battery", ""))
        self.state.window_geometry = config_data.get("window_geometry")

        profiles_data = config_data.get("profiles", {})
        if not profiles_data:
            profiles_data = copy.deepcopy(DEFAULT_PROFILES)
        
        # Load or generate the profile order
        profile_order = config_data.get("profile_order", sorted(profiles_data.keys()))
        # Keep the order list and the profiles dict in sync
        profile_order = [name for name in profile_order if name in profiles_data]
        for name in profiles_data:
            if name not in profile_order:
                profile_order.append(name)

        self.state.load_profiles_from_config(profiles_data, profile_order)
        
        active_profile_name = config_data.get("active_profile_name", next(iter(DEFAULT_PROFILES)))
        if active_profile_name not in self.state.get_profile_names():
            active_profile_name = self.state.get_profile_names()[0] if self.state.get_profile_names() else next(iter(DEFAULT_PROFILES))
        self.state.set_active_profile_name(active_profile_name)

    def _collect_config_data(self) -> Dict[str, Any]:
        """Snapshot of everything that goes into the config JSON."""
        return {
            "language": self.state.get_language(),
            "theme_mode": self.state.get_theme_mode(),
            "start_on_boot": self.state.get_start_on_boot(),
            "notifications_enabled": self.state.get_notifications_enabled(),
            "profile_on_ac": self.state.get_profile_on_ac(),
            "profile_on_battery": self.state.get_profile_on_battery(),
            "active_profile_name": self.state.get_active_profile_name(),
            "window_geometry": self.state.window_geometry,
            "gui_update_interval_ms": self.state.get_gui_update_interval_ms(),
            "profile_order": self.state.profile_order, # Preserve the order
            "profiles": self.state.get_profiles_for_config()
        }

    def save_config(self):
        """Debounced async save: bursts of state changes coalesce into one
        background write 400 ms after the last call. Atomic write: temp file
        + replace, so a mid-write crash cannot corrupt the config."""
        self._save_timer.start()

    def _save_now(self):
        self._save_timer.stop()
        if self._write_thread is not None and self._write_thread.is_alive():
            self._resave_pending = True  # writer re-arms itself when done
            return
        config_data = self._collect_config_data()
        self._write_thread = threading.Thread(
            target=self._write_config_file, args=(config_data,),
            daemon=True, name="ConfigWriter")
        self._write_thread.start()

    def _write_config_file(self, config_data: Dict[str, Any]) -> None:
        """Atomic JSON write; safe on a non-GUI thread (the status-message
        signal is delivered queued)."""
        temp_path = ""
        try:
            if os.path.exists(self.config_path):
                try:
                    shutil.copy2(self.config_path, self.config_path + ".bak")
                except OSError as backup_error:
                    print(f"Warning: could not back up config: {backup_error}", file=sys.stderr)

            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(self.config_path) or ".", prefix="control_config_", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.config_path)
            temp_path = ""
        except Exception as e:
            if temp_path:
                try: os.remove(temp_path)
                except OSError: pass
            print(f"Error: could not save profiles to '{self.config_path}'. Error: {e}", file=sys.stderr)
            self.state.set_controller_status_message(tr("config_save_error", error=str(e)))
        finally:
            if self._write_thread is threading.current_thread():
                self._write_thread = None
            if self._resave_pending:
                self._resave_pending = False
                self._resave_needed.emit()

    def save_config_sync(self) -> None:
        """Blocking save for shutdown: join any in-flight writer, then write
        a fresh snapshot inline."""
        self._save_timer.stop()
        if self._write_thread is not None and self._write_thread.is_alive():
            self._write_thread.join(timeout=2.0)
        self._write_config_file(self._collect_config_data())

    def _read_config_file(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as error:
            backup_path = f"{self.config_path}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            try:
                shutil.copy2(self.config_path, backup_path)
            except OSError:
                backup_path = self.config_path
            print(
                f"Error: config '{self.config_path}' is corrupt ({error}); keeping a copy at '{backup_path}'.",
                file=sys.stderr,
            )
            self.state.set_controller_status_message(
                tr("config_corrupt_recovered", path=backup_path)
            )
            return {}
        except OSError as error:
            print(f"Error: could not read config '{self.config_path}': {error}", file=sys.stderr)
            return {}

    def get_active_profile(self) -> Optional[ProfileState]:
        return self.state.get_active_profile()

    def reload_and_apply_active_profile(self):
        print("Reloading config and force-applying settings...")
        self.load_config()
        active_profile = self.get_active_profile()
        if active_profile:
            self.state.active_profile_changed.emit(active_profile)

    def activate_profile(self, profile_name: str):
        if profile_name in self.state.get_profile_names() and self.state.get_active_profile_name() != profile_name:
            self.state.set_active_profile_name(profile_name)
            self.save_config()

    def create_new_profile(self, new_name: str):
        active_profile = self.get_active_profile()
        if not active_profile or self.state.get_profile(new_name):
            return
        
        new_profile_state = ProfileState(self.state)
        new_profile_state.from_dict(active_profile.to_dict())
        self.state.add_profile(new_name, new_profile_state)
        self.activate_profile(new_name)

    def rename_profile(self, old_name: str, new_name: str):
        self.state.rename_profile(old_name, new_name)
        self.save_config()
            
    # Interface for deleting profiles
    def delete_profile(self, name: str):
        self.state.delete_profile(name)
        self.save_config()

    def update_active_profile_data(self, key: str, value: Any):
        active_profile = self.get_active_profile()
        if active_profile and hasattr(active_profile, f"set_{key}"):
            setter = getattr(active_profile, f"set_{key}")
            setter(value)
            self.save_config()

    def set_profile_value(self, key: str, value: Any):
        """Write a profile key that has no typed property (e.g.
        gpu_power_feedforward) and persist the config."""
        active_profile = self.get_active_profile()
        if active_profile:
            active_profile.set_value(key, value)
            self.save_config()

    def set_curve_data(self, curve_type: str, data: list):
        key = "cpu_fan_table" if curve_type == 'cpu' else "gpu_fan_table"
        self.update_active_profile_data(key, data)

    def reset_active_curve(self):
        active_profile = self.get_active_profile()
        if not active_profile: return
        
        active_curve_type = self.state.get_active_curve_type()
        default_key = f"{active_curve_type}_fan_table"
        default_table = copy.deepcopy(DEFAULT_PROFILE_SETTINGS.get(default_key, []))
        self.set_curve_data(active_curve_type, default_table)