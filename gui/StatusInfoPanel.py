# -*- coding: utf-8 -*-
"""
Status info panel QFrame - a self-contained view component.
The panel updates its content reactively by connecting directly to AppState signals.
"""
from .qt import *
from core.state import AppState, ProfileState
from tools.localization import tr
from config.settings import (
    TEMP_READ_ERROR_VALUE, RPM_READ_ERROR_VALUE,
    FAN_MODE_AUTO, FAN_MODE_CUSTOM, CHARGE_POLICY_CUSTOM
)

class StatusInfoPanel(QFrame):
    """Self-contained view component that displays system status."""
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("infoFrame")
        self._init_ui()
        self._connect_signals()

        self.setEnabled(True)
        self._update_all_displays()

    def _init_ui(self):
        """Create and lay out this panel's UI widgets."""
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 5, 10, 10); layout.setSpacing(10)
        
        self.cpu_temp_label = QLabel(tr("cpu_temp_label"))
        self.cpu_temp_value = QLabel()
        self.gpu_temp_label = QLabel(tr("gpu_temp_label"))
        self.gpu_temp_value = QLabel()
        # UI labels keep their business meaning, but internally bind to fan1/fan2
        self.cpu_fan_rpm_label = QLabel(tr("cpu_fan_rpm_label"))
        self.cpu_fan_rpm_value = QLabel()
        self.gpu_fan_rpm_label = QLabel(tr("gpu_fan_rpm_label"))
        self.gpu_fan_rpm_value = QLabel()
        self.applied_target_label = QLabel(tr("applied_target_label"))
        self.applied_target_value = QLabel()
        self.battery_info_label = QLabel(tr("battery_info_label"))
        self.battery_info_value = QLabel()

        layout.addWidget(self.cpu_temp_label, 0, 0); layout.addWidget(self.cpu_temp_value, 0, 1)
        layout.addWidget(self.gpu_temp_label, 0, 2); layout.addWidget(self.gpu_temp_value, 0, 3)
        layout.addWidget(self.cpu_fan_rpm_label, 1, 0); layout.addWidget(self.cpu_fan_rpm_value, 1, 1)
        layout.addWidget(self.gpu_fan_rpm_label, 1, 2); layout.addWidget(self.gpu_fan_rpm_value, 1, 3)
        layout.addWidget(self.applied_target_label, 2, 0); layout.addWidget(self.applied_target_value, 2, 1)
        layout.addWidget(self.battery_info_label, 2, 2); layout.addWidget(self.battery_info_value, 2, 3)
        
        layout.setColumnStretch(1, 1); layout.setColumnStretch(3, 1)

    def _connect_signals(self):
        """Connect the UI update slots to specific AppState signals."""
        self.state.cpu_temp_changed.connect(self._update_cpu_temp)
        self.state.gpu_temp_changed.connect(self._update_gpu_temp)
        self.state.gpu_asleep_changed.connect(self._on_gpu_asleep_changed)
        
        # Wire UI widgets to the new, hardware-aligned state properties
        self.state.fan1_rpm_changed.connect(self._update_cpu_rpm) # Fan 1 -> CPU Fan UI
        self.state.fan2_rpm_changed.connect(self._update_gpu_rpm) # Fan 2 -> GPU Fan UI

        self.state.active_profile_changed.connect(self._update_fan_and_battery_display)
        self.state.applied_fan_mode_changed.connect(self._update_fan_and_battery_display)
        self.state.applied_fan_speed_percent_changed.connect(self._update_fan_and_battery_display)
        self.state.auto_fan_target_speed_percent_changed.connect(self._update_fan_and_battery_display)
        self.state.applied_charge_policy_changed.connect(self._update_fan_and_battery_display)
        self.state.applied_charge_threshold_changed.connect(self._update_fan_and_battery_display)
        self.state.ec_fixed_fan_speed_raw_changed.connect(self._update_fan_and_battery_display)
        self.state.battery_health_changed.connect(self._update_fan_and_battery_display)
        self.state.battery_cycles_changed.connect(self._update_fan_and_battery_display)

    def _update_all_displays(self):
        """Update all displayed values in one pass; used for init and language switching."""
        self._update_cpu_temp(self.state.get_cpu_temp())
        self._update_gpu_temp(self.state.get_gpu_temp())
        # Get initial values from the new state properties
        self._update_cpu_rpm(self.state.get_fan1_rpm())
        self._update_gpu_rpm(self.state.get_fan2_rpm())
        self._update_fan_and_battery_display()

    @Slot(float)
    def _update_cpu_temp(self, temp: float):
        self.cpu_temp_value.setText(f"{temp:.1f}{tr('celsius_unit')}" if temp != TEMP_READ_ERROR_VALUE else tr("temp_error"))

    @Slot(float)
    def _update_gpu_temp(self, temp: float):
        if self.state.get_gpu_asleep():
            self.gpu_temp_value.setText(tr("gpu_asleep_display"))
        else:
            self.gpu_temp_value.setText(f"{temp:.1f}{tr('celsius_unit')}" if temp != TEMP_READ_ERROR_VALUE else tr("temp_error"))

    @Slot(bool)
    def _on_gpu_asleep_changed(self, asleep: bool):
        self._update_gpu_temp(self.state.get_gpu_temp())

    @Slot(int)
    def _update_cpu_rpm(self, rpm: int):
        self.cpu_fan_rpm_value.setText(f"{rpm}{tr('rpm_unit')}" if rpm != RPM_READ_ERROR_VALUE else tr("rpm_error"))

    @Slot(int)
    def _update_gpu_rpm(self, rpm: int):
        self.gpu_fan_rpm_value.setText(f"{rpm}{tr('rpm_unit')}" if rpm != RPM_READ_ERROR_VALUE else tr("rpm_error"))


    def _optional_count(self, value: int) -> str:
        return tr("value_not_available") if value < 0 else str(value)

    @Slot()
    def _update_fan_and_battery_display(self):
        """Update the combined fan and battery display text."""
        fan_mode = self.state.get_applied_fan_mode()
        raw = self._optional_count(self.state.get_ec_fixed_fan_speed_raw())
        if fan_mode == FAN_MODE_AUTO:
            fan_display = tr(
                "fan_display_auto_format",
                applied=self.state.get_applied_fan_speed_percent(),
                target=self.state.get_auto_fan_target_speed_percent(),
                raw=raw,
            )
        elif fan_mode == FAN_MODE_CUSTOM:
            fan_display = tr(
                "fan_display_custom_format",
                applied=self.state.get_applied_fan_speed_percent(),
                raw=raw,
            )
        else:
            fan_display = tr('mode_bios')
        self.applied_target_value.setText(fan_display)

        policy_str = tr("mode_custom") if self.state.get_applied_charge_policy() == CHARGE_POLICY_CUSTOM else tr("mode_bios")
        limit_str = f"{self.state.get_applied_charge_threshold()}{tr('percent_unit')}"
        self.battery_info_value.setText(tr(
            "battery_display_format",
            policy=policy_str,
            limit=limit_str,
            health=self._optional_count(self.state.get_battery_health()),
            cycles=self._optional_count(self.state.get_battery_cycles()),
        ))

    def retranslate_ui(self):
        """Retranslate all user-visible text in this panel."""
        self.cpu_temp_label.setText(tr("cpu_temp_label"))
        self.gpu_temp_label.setText(tr("gpu_temp_label"))
        self.cpu_fan_rpm_label.setText(tr("cpu_fan_rpm_label"))
        self.gpu_fan_rpm_label.setText(tr("gpu_fan_rpm_label"))
        self.applied_target_label.setText(tr("applied_target_label"))
        self.battery_info_label.setText(tr("battery_info_label"))
        self._update_all_displays()