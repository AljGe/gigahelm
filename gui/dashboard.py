# -*- coding: utf-8 -*-
"""
Dashboard: the entire single-page interface below the title bar.

Composition only - four live stat cards over a control column plus the curve
card, with a footer holding the version and the start-with-Windows switch.
All values are pushed in from `AppState`; no business logic lives here.
"""

from typing import Optional

from .qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QComboBox, QSizePolicy, Signal, Slot,
)
from .stat_card import StatCard
from .FanControlPanel import FanControlPanel
from .BatteryControlPanel import BatteryControlPanel
from .curve_card import CurveCard
from .theme import ThemeManager
from .tooltip_manager import tooltip_manager
from core.state import AppState
from core.profile_manager import ProfileManager
from core.settings_manager import SettingsManager
from tools.localization import tr
from config.settings import (
    APP_NAME, APP_VERSION, MAX_TEMP_C, TEMP_READ_ERROR_VALUE, RPM_READ_ERROR_VALUE,
    FAN_MODE_BIOS, FAN_MODE_UNKNOWN,
)

_TEMP_WARN_C = 70.0
_TEMP_DANGER_C = 85.0
_CONTROL_COLUMN_WIDTH = 340


class Dashboard(QWidget):
    """The whole page: hero cards, control column, curve card, footer."""

    transient_status = Signal(str, int)

    def __init__(
        self,
        state: AppState,
        profile_manager: ProfileManager,
        settings_manager: SettingsManager,
        theme: ThemeManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.state = state
        self.profile_manager = profile_manager
        self.settings_manager = settings_manager
        self.theme = theme

        self._init_ui()
        self._connect_signals()
        self._refresh_all()

    # ------------------------------------------------------------------ UI ---
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        hero = QHBoxLayout()
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(16)
        self.cpu_card = StatCard("stat_cpu_caption", self)
        self.gpu_card = StatCard("stat_gpu_caption", self)
        self.fan_card = StatCard("stat_fan_caption", self)
        self.battery_card = StatCard("stat_battery_caption", self)
        tooltip_manager.register(self.battery_card, "battery_health_tooltip")
        for card in (self.cpu_card, self.gpu_card, self.fan_card, self.battery_card):
            hero.addWidget(card, 1)
        layout.addLayout(hero)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(16)

        controls = QWidget(self)
        controls.setFixedWidth(_CONTROL_COLUMN_WIDTH)
        controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(16)
        self.fan_control = FanControlPanel(self.state, self.profile_manager, controls)
        self.battery_control = BatteryControlPanel(self.state, self.profile_manager, controls)
        controls_layout.addWidget(self.fan_control)
        controls_layout.addWidget(self.battery_control)
        controls_layout.addStretch(1)
        main_row.addWidget(controls)

        self.curve_card = CurveCard(
            self.state, self.profile_manager, self.settings_manager, self.theme, self
        )
        main_row.addWidget(self.curve_card, 1)
        layout.addLayout(main_row, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.brand_label = QLabel(f"{APP_NAME} {APP_VERSION}", self)
        self.brand_label.setObjectName("statFooter")
        footer.addWidget(self.brand_label)
        footer.addStretch(1)
        self.start_on_boot_checkbox = QCheckBox(tr("start_on_boot_label"), self)
        tooltip_manager.register(self.start_on_boot_checkbox, "start_on_boot_tooltip")
        footer.addWidget(self.start_on_boot_checkbox)

        self.notifications_checkbox = QCheckBox(tr("notifications_label"), self)
        tooltip_manager.register(self.notifications_checkbox, "notifications_tooltip")
        footer.addWidget(self.notifications_checkbox)

        self.ui_refresh_label = QLabel(tr("ui_refresh_label"), self)
        self.ui_refresh_label.setObjectName("statFooter")
        footer.addWidget(self.ui_refresh_label)
        self.ui_refresh_combo = QComboBox(self)
        for ms in (250, 500, 1000, 2000):
            self.ui_refresh_combo.addItem(tr("refresh_ms", ms=ms), ms)
        tooltip_manager.register(self.ui_refresh_combo, "ui_refresh_tooltip")
        footer.addWidget(self.ui_refresh_combo)
        layout.addLayout(footer)

    def _connect_signals(self) -> None:
        self.state.cpu_temp_changed.connect(self._on_cpu_temp_changed)
        self.state.gpu_temp_changed.connect(self._on_gpu_temp_changed)
        self.state.gpu_asleep_changed.connect(self._on_gpu_asleep_changed)
        self.state.gpu_util_changed.connect(lambda *_: self._update_gpu_card_footer())
        self.state.fan1_rpm_changed.connect(self._on_fan1_rpm_changed)
        self.state.fan2_rpm_changed.connect(self._on_fan2_rpm_changed)

        self.state.applied_fan_speed_percent_changed.connect(self._update_fan_card)
        self.state.applied_fan_mode_changed.connect(self._update_fan_card)
        self.state.auto_fan_target_speed_percent_changed.connect(self._update_fan_footer)
        self.state.ec_fixed_fan_speed_raw_changed.connect(self._update_fan_footer)

        self.state.applied_charge_threshold_changed.connect(self._update_battery_value)
        self.state.applied_charge_policy_changed.connect(self._update_battery_value)
        self.state.battery_health_changed.connect(self._update_battery_footer)
        self.state.battery_cycles_changed.connect(self._update_battery_footer)

        self.state.start_on_boot_changed.connect(self._on_start_on_boot_changed)
        self.start_on_boot_checkbox.toggled.connect(self.settings_manager.set_start_on_boot)

        self.state.notifications_enabled_changed.connect(self._on_notifications_enabled_changed)
        self.notifications_checkbox.toggled.connect(self.settings_manager.set_notifications_enabled)

        self.ui_refresh_combo.activated.connect(self._on_ui_refresh_selected)

        self.curve_card.transient_status.connect(self.transient_status.emit)

    # --------------------------------------------------------------- cards ---
    def _refresh_all(self) -> None:
        self._apply_theme_to_cards()
        self._on_cpu_temp_changed(self.state.get_cpu_temp())
        self._on_gpu_temp_changed(self.state.get_gpu_temp())
        self._on_fan1_rpm_changed(self.state.get_fan1_rpm())
        self._on_fan2_rpm_changed(self.state.get_fan2_rpm())
        self._update_fan_card()
        self._update_battery_value()
        self._update_battery_footer()
        self._on_start_on_boot_changed(self.state.get_start_on_boot())
        self._on_notifications_enabled_changed(self.state.get_notifications_enabled())
        self._refresh_ui_refresh_combo()

    def _apply_theme_to_cards(self) -> None:
        palette = self.theme.palette()
        for card in (self.cpu_card, self.gpu_card, self.fan_card, self.battery_card):
            card.apply_theme(palette)

    @Slot(float)
    def _on_cpu_temp_changed(self, temp: float) -> None:
        self._update_temp_card(self.cpu_card, temp)

    @Slot(float)
    def _on_gpu_temp_changed(self, temp: float) -> None:
        self._update_temp_card(self.gpu_card, temp, asleep=self.state.get_gpu_asleep())

    @Slot(bool)
    def _on_gpu_asleep_changed(self, asleep: bool) -> None:
        self._update_temp_card(self.gpu_card, self.state.get_gpu_temp(), asleep=asleep)
        if not asleep:
            self._update_gpu_card_footer()

    def _update_temp_card(self, card: StatCard, temp: float, asleep: bool = False) -> None:
        if asleep:
            card.set_value("")
            card.set_unit("")
            card.set_tone("muted")
            card.set_level(0.0)
            card.set_footer(tr("stat_gpu_asleep"))
            return

        if temp == TEMP_READ_ERROR_VALUE:
            card.set_value(tr("temp_error"))
            card.set_unit("")
            card.set_tone("muted")
            card.set_level(None)
            # Don't pair ERR with the stale last-good RPM line.
            card.set_footer(tr("value_not_available"))
            return

        card.set_value(f"{temp:.1f}")
        card.set_unit(tr("celsius_unit"))
        card.set_level(max(0.0, min(1.0, temp / MAX_TEMP_C)))
        if temp >= _TEMP_DANGER_C:
            card.set_tone("danger")
        elif temp >= _TEMP_WARN_C:
            card.set_tone("warn")
        else:
            card.set_tone("normal")

    @Slot(int)
    def _on_fan1_rpm_changed(self, rpm: int) -> None:
        self._update_fan_rpm(self.cpu_card, rpm)

    @Slot(int)
    def _on_fan2_rpm_changed(self, rpm: int) -> None:
        if self.state.get_gpu_asleep():
            return
        self._update_gpu_card_footer()

    def _update_gpu_card_footer(self) -> None:
        """GPU fan RPM plus NVML utilization when known (sleep guard may
        pause polling -> -1 shows as RPM-only)."""
        if self.state.get_gpu_asleep():
            return  # _update_temp_card owns the asleep footer
        rpm = self.state.get_fan2_rpm()
        if rpm == RPM_READ_ERROR_VALUE:
            self.gpu_card.set_footer(tr("rpm_error"))
            return
        footer = f"{rpm}{tr('rpm_unit')}"
        util = self.state.get_gpu_util()
        if util >= 0:
            footer += f" · {util}{tr('percent_unit')}"
        self.gpu_card.set_footer(footer)

    def _update_fan_rpm(self, card: StatCard, rpm: int) -> None:
        if rpm == RPM_READ_ERROR_VALUE:
            card.set_footer(tr("rpm_error"))
        else:
            card.set_footer(f"{rpm}{tr('rpm_unit')}")

    @Slot()
    def _update_fan_card(self, *_args) -> None:
        mode = self.state.get_applied_fan_mode()
        if mode == FAN_MODE_UNKNOWN:
            self.fan_card.set_value(tr("value_not_available"))
            self.fan_card.set_unit("")
            self.fan_card.set_level(None)
        elif mode == FAN_MODE_BIOS:
            self.fan_card.set_value(tr("mode_bios"))
            self.fan_card.set_unit("")
            self.fan_card.set_level(None)
        else:
            applied = self.state.get_applied_fan_speed_percent()
            self.fan_card.set_value(f"{applied}")
            self.fan_card.set_unit(tr("percent_unit"))
            self.fan_card.set_level(max(0.0, min(1.0, applied / 100)))
        self._update_fan_footer()

    @Slot()
    def _update_fan_footer(self, *_args) -> None:
        raw_value = self.state.get_ec_fixed_fan_speed_raw()
        raw = tr("value_not_available") if raw_value < 0 else str(raw_value)
        self.fan_card.set_footer(tr(
            "stat_fan_footer_format",
            target=self.state.get_auto_fan_target_speed_percent(),
            raw=raw,
        ))

    @Slot()
    def _update_battery_value(self, *_args) -> None:
        threshold = self.state.get_applied_charge_threshold()
        if threshold < 0:
            self.battery_card.set_value(tr("value_not_available"))
            self.battery_card.set_unit("")
            self.battery_card.set_level(None)
            return
        self.battery_card.set_value(f"{threshold}")
        self.battery_card.set_unit(tr("percent_unit"))
        self.battery_card.set_level(max(0.0, min(1.0, threshold / 100)))

    @Slot()
    def _update_battery_footer(self, *_args) -> None:
        health = self.state.get_battery_health()
        cycles = self.state.get_battery_cycles()
        self.battery_card.set_footer(tr(
            "stat_battery_footer_format",
            health=tr("value_not_available") if health < 0 else str(health),
            cycles=tr("value_not_available") if cycles < 0 else str(cycles),
        ))

    @Slot(bool)
    def _on_start_on_boot_changed(self, enabled: bool) -> None:
        self.start_on_boot_checkbox.blockSignals(True)
        try:
            self.start_on_boot_checkbox.setChecked(enabled)
        finally:
            self.start_on_boot_checkbox.blockSignals(False)

    @Slot(bool)
    def _on_notifications_enabled_changed(self, enabled: bool) -> None:
        self.notifications_checkbox.blockSignals(True)
        try:
            self.notifications_checkbox.setChecked(enabled)
        finally:
            self.notifications_checkbox.blockSignals(False)

    def _refresh_ui_refresh_combo(self) -> None:
        interval = self.state.get_gui_update_interval_ms()
        index = self.ui_refresh_combo.findData(interval)
        self.ui_refresh_combo.blockSignals(True)
        self.ui_refresh_combo.setCurrentIndex(index if index >= 0 else self.ui_refresh_combo.findData(1000))
        self.ui_refresh_combo.blockSignals(False)

    @Slot(int)
    def _on_ui_refresh_selected(self, index: int) -> None:
        ms = self.ui_refresh_combo.itemData(index)
        if ms:
            self.settings_manager.set_gui_update_interval_ms(int(ms))

    # --------------------------------------------------------------- i18n ----
    def retranslate_ui(self) -> None:
        self.cpu_card.set_caption_text(tr("stat_cpu_caption"))
        self.gpu_card.set_caption_text(tr("stat_gpu_caption"))
        self.fan_card.set_caption_text(tr("stat_fan_caption"))
        self.battery_card.set_caption_text(tr("stat_battery_caption"))
        self.start_on_boot_checkbox.setText(tr("start_on_boot_label"))
        self.notifications_checkbox.setText(tr("notifications_label"))
        self.ui_refresh_label.setText(tr("ui_refresh_label"))
        self.brand_label.setText(f"{APP_NAME} {APP_VERSION}")
        self.fan_control.retranslate_ui()
        self.battery_control.retranslate_ui()
        self.curve_card.retranslate_ui()
        self._refresh_all()

    def apply_theme(self) -> None:
        self._apply_theme_to_cards()
        self.curve_card.apply_theme()
