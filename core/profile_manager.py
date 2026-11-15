# -*- coding: utf-8 -*-
"""
Manages all profile-related state and persistence logic.
This class encapsulates loading, saving, creating, renaming, and activating profiles.
"""
import os
import json
import copy
import sys
import tempfile
from typing import Dict, Any, Optional, List

from gui.qt import QObject, Signal
from .state import AppState, ProfileState
from config.settings import DEFAULT_PROFILE_SETTINGS

class ProfileManager(QObject):
    """Central class that manages the application's profiles."""
    
    def __init__(self, app_state: AppState, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.state = app_state
        self.config_path = self.state.paths.control_config

    def load_config(self):
        """Load the config from the JSON file and populate state."""
        config_data = self._read_config_file()
        
        self.state.set_language(config_data.get("language", "en"))
        self.state.set_start_on_boot(config_data.get("start_on_boot", False))
        self.state.window_geometry = config_data.get("window_geometry")

        profiles_data = config_data.get("profiles", {})
        if not profiles_data:
            profiles_data = {"Config 1": copy.deepcopy(DEFAULT_PROFILE_SETTINGS)}
        
        # Load or generate the profile order
        profile_order = config_data.get("profile_order", sorted(profiles_data.keys()))
        # Keep the order list and the profiles dict in sync
        profile_order = [name for name in profile_order if name in profiles_data]
        for name in profiles_data:
            if name not in profile_order:
                profile_order.append(name)

        self.state.load_profiles_from_config(profiles_data, profile_order)
        
        active_profile_name = config_data.get("active_profile_name", "Config 1")
        if active_profile_name not in self.state.get_profile_names():
            active_profile_name = self.state.get_profile_names()[0] if self.state.get_profile_names() else "Config 1"
        self.state.set_active_profile_name(active_profile_name)

    def save_config(self):
        """Save the current state to the JSON config file. Atomic write: write to a temp file, then replace, so a mid-write crash cannot corrupt the config."""
        config_data = {
            "language": self.state.get_language(),
            "start_on_boot": self.state.get_start_on_boot(),
            "active_profile_name": self.state.get_active_profile_name(),
            "window_geometry": self.state.window_geometry,
            "profile_order": self.state.profile_order, # Preserve the order
            "profiles": self.state.get_profiles_for_config()
        }
        temp_path = ""
        try:
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(self.config_path) or ".", prefix="control_config_", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
            os.replace(temp_path, self.config_path)
            temp_path = ""
        except Exception as e:
            if temp_path:
                try: os.remove(temp_path)
                except OSError: pass
            print(f"Error: could not save profiles to '{self.config_path}'. Error: {e}", file=sys.stderr)

    def _read_config_file(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
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