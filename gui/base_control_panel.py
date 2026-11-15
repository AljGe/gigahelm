# gui/base_control_panel.py (replaces the entire file)
# -*- coding: utf-8 -*-
"""
Provides a reusable base UI panel (BaseControlPanel) for uniformly creating and managing control areas with mode selection and a slider.
The base class is now driven entirely by AppState (the real hardware state) for its display, keeping the UI in sync with the hardware.
"""
from .qt import *
from .tooltip_manager import tooltip_manager
from tools.localization import tr
from core.state import AppState, ProfileState
from core.profile_manager import ProfileManager
from .EditableLabel import EditableLabel
from typing import Dict, Any, List, Tuple, Optional, Union

class BaseControlPanel(QFrame):
    """
    A configurable base QFrame containing a set of mode radio buttons and an associated slider.
    It calls ProfileManager to set the desired state, but reactively updates
    itself via AppState signals to reflect the real hardware state.
    """
    
    def __init__(self, config: Dict[str, Any], state: AppState, profile_manager: ProfileManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.profile_manager = profile_manager
        
        self.mode_button_group: QButtonGroup
        self.mode_radios: Dict[str, QRadioButton] = {}
        self.slider: QSlider
        self.value_label: EditableLabel
        self.mode_label: QLabel
        self.slider_label: QLabel
        
        self.slider_debounce_timer = QTimer(self)
        self.slider_debounce_timer.setSingleShot(True)
        self.slider_debounce_timer.setInterval(300) # 300ms delay
        
        self._init_ui()
        self._connect_signals()
        
        # On init, read the real state from AppState and update the UI
        self._update_all_displays_from_app_state()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5); layout.setSpacing(10)

        self.mode_label = QLabel(tr(self.config["mode_label_key"]), self)
        layout.addWidget(self.mode_label)

        self.mode_button_group = QButtonGroup(self)
        for mode_name, text_key in self.config["radio_configs"]:
            radio = QRadioButton(tr(text_key), self)
            self.mode_radios[mode_name] = radio
            self.mode_button_group.addButton(radio)
            layout.addWidget(radio)
        
        layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.slider_label = QLabel(tr(self.config["slider_label_key"]), self)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(*self.config["slider_range"])

        self.value_label = EditableLabel(unit=tr('percent_unit'), parent=self)
        self.value_label.setMinimumWidth(45)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        layout.addWidget(self.slider_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)

        tooltip_manager.register(self.mode_label, self.config["mode_tooltip_key"])
        tooltip_manager.register(self.slider, self.config["slider_tooltip_key"])
        tooltip_manager.register(self.value_label, "editable_label_tooltip")

    def _connect_signals(self):
        # UI -> ProfileManager (user intent)
        for mode_name, radio in self.mode_radios.items():
            radio.toggled.connect(lambda checked, name=mode_name: 
                self.profile_manager.update_active_profile_data(self.config["profile_mode_attr"], name) if checked else None)

        self.slider.valueChanged.connect(self._on_slider_value_changed)
        self.slider_debounce_timer.timeout.connect(self._commit_slider_value)
        self.value_label.editingFinished.connect(self._commit_label_value)

        # AppState (real hardware state) -> UI (display)
        # Dynamically connect to the AppState signal
        mode_signal = getattr(self.state, self.config["app_state_mode_attr"] + "_changed")
        mode_signal.connect(self._update_mode_display)
        
        value_signal = getattr(self.state, self.config["app_state_value_attr"] + "_changed")
        value_signal.connect(self._update_value_display)
        
        # The fan panel additionally listens to the global enable/disable state
        if self.config.get("is_fan_control", False):
            self.state.is_fan_control_panel_enabled_changed.connect(
                lambda: self._update_mode_display(self.state.get_applied_fan_mode())
            )

    @Slot(int)
    def _on_slider_value_changed(self, value: int):
        self.value_label.blockSignals(True)
        self.value_label.setValue(value)
        self.value_label.blockSignals(False)
        self.slider_debounce_timer.start()

    @Slot()
    def _commit_slider_value(self):
        self.slider_debounce_timer.stop()
        self._commit_value(self.slider.value())

    @Slot(int)
    def _commit_label_value(self, value: int):
        self.slider_debounce_timer.stop()
        min_val, max_val = self.config["slider_range"]
        clamped_value = max(min_val, min(max_val, value))
        self._commit_value(clamped_value)

    def _commit_value(self, final_value: int):
        self.profile_manager.update_active_profile_data(self.config["profile_value_attr"], final_value)

    def _update_all_displays_from_app_state(self):
        """Reads all current real values from AppState and updates the UI."""
        # Dynamically call the AppState getter methods
        mode_getter = getattr(self.state, "get_" + self.config["app_state_mode_attr"])
        value_getter = getattr(self.state, "get_" + self.config["app_state_value_attr"])
        
        self._update_mode_display(mode_getter())
        self._update_value_display(value_getter())

    @Slot(str)
    def _update_mode_display(self, mode: str):
        self.mode_button_group.blockSignals(True)
        if mode in self.mode_radios:
            self.mode_radios[mode].setChecked(True)
        self.mode_button_group.blockSignals(False)
        
        is_custom_mode = (mode == self.config["custom_mode_name"])
        
        is_slider_enabled = False
        if self.config.get("is_fan_control", False):
            is_slider_enabled = self.state.get_is_fan_control_panel_enabled() and is_custom_mode
        else:
            is_slider_enabled = is_custom_mode

        self.slider_label.setEnabled(is_slider_enabled)
        self.slider.setEnabled(is_slider_enabled)
        self.value_label.setEnabled(is_slider_enabled)

    @Slot(int)
    def _update_value_display(self, value: int):
        if value < 0: return # Ignore invalid values (e.g. -1)
        
        # Update the slider
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        
        # Update the label
        self.value_label.blockSignals(True)
        self.value_label.setValue(value)
        self.value_label.blockSignals(False)

    def retranslate_ui(self):
        self.mode_label.setText(tr(self.config["mode_label_key"]))
        for mode_name, text_key in self.config["radio_configs"]:
            if mode_name in self.mode_radios:
                self.mode_radios[mode_name].setText(tr(text_key))
        
        self.slider_label.setText(tr(self.config["slider_label_key"]))
        self.value_label.unit = tr('percent_unit')
        # Also refresh from AppState when retranslating
        self._update_all_displays_from_app_state()