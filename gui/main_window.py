# -*- coding: utf-8 -*-
"""
主应用窗口(QMainWindow)，作为UI的中心控制器，
连接用户操作与服务，并将状态更新分派给视图组件。
此类现在负责通知AppServices UI的可见性，以实现智能轮询。
"""
import sys
import time
import os
import ctypes
from typing import Optional, Any, Dict

from .qt import *
from .dashboard import Dashboard
from .custom_title_bar import CustomTitleBar
from .app_icon import load_app_icon
from .theme import ThemeManager
from core.state import AppState
from tools.localization import tr, set_language
from core.app_services import AppServices
from core.profile_manager import ProfileManager
from core.settings_manager import SettingsManager
from config.settings import (
    COMMAND_CLI_ACTION, COMMAND_QUIT, COMMAND_RELOAD_AND_SHOW, COMMAND_RELOAD_ONLY,
    CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM, THEME_MODE_SYSTEM
)

class _WindowsResumeFilter(QAbstractNativeEventFilter):
    """Collapse resume power broadcasts into one AppServices recovery and
    forward AC attach/detach (PBT_APMPOWERSTATUSCHANGE) to the app."""

    def __init__(self, on_resume, on_power_change=None):
        super().__init__()
        self._on_resume = on_resume
        self._on_power_change = on_power_change

    def nativeEventFilter(self, eventType, message):
        try:
            kind = eventType
            if kind not in (b"windows_generic_MSG", "windows_generic_MSG"):
                try:
                    kind = bytes(eventType)
                except Exception:
                    kind = str(eventType)
            if kind in (b"windows_generic_MSG", "windows_generic_MSG"):
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0218 and int(msg.wParam) in (0x0007, 0x0012):
                    self._on_resume()
                elif (msg.message == 0x0218 and int(msg.wParam) == 0x000A
                      and self._on_power_change is not None):
                    self._on_power_change()
        except Exception:
            pass
        return False, 0


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("Reserved1", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]

class MainWindow(QMainWindow):
    """主应用窗口。"""
    gui_tick = Signal()
    window_initialized_signal = Signal(int)

    def __init__(
        self,
        app_services: AppServices,
        state: AppState,
        profile_manager: ProfileManager,
        settings_manager: SettingsManager,
        theme: ThemeManager,
        start_minimized: bool = False,
        startup_cli_payload: Optional[Dict[str, Any]] = None
    ):
        super().__init__()
        self.app_services = app_services
        self.state = state
        self.profile_manager = profile_manager
        self.settings_manager = settings_manager
        self.theme = theme
        self.start_minimized = start_minimized
        self.startup_cli_payload = startup_cli_payload or {}

        self.tray_icon: Optional[QSystemTrayIcon] = None
        self._last_status_notified: Optional[str] = None
        self._is_quitting: bool = False
        self._is_showing_transient_status: bool = False
        self._tray_close_hint_shown: bool = False
        self.status_clear_timer = QTimer(self)
        self.status_clear_timer.setSingleShot(True)

        self.gui_update_timer = QTimer(self)
        self.command_poll_timer: Optional[QTimer] = None
        self._is_initialized: bool = False

        self._apply_locale()
        self.init_ui()
        self.connect_signals()
        self.init_tray_icon()
        self._on_theme_changed()
        self.resize(1180, 800)
        self._restore_geometry()

        if self.start_minimized:
            if self.tray_icon: self.tray_icon.show()
            # 即使最小化启动，也要正确设置初始UI可见性
            self.app_services.set_ui_visibility(False)
        else:
            self.show()

        self._start_command_poller()
        self._install_resume_filter()
        if self.startup_cli_payload:
            QTimer.singleShot(0, lambda: self.execute_cli_payload(self.startup_cli_payload))
    def _restore_geometry(self):
        geometry_str = self.state.window_geometry
        if geometry_str:
            try:
                self.restoreGeometry(QByteArray.fromHex(geometry_str.encode('ascii')))
            except (ValueError, TypeError): self.resize(1180, 800)

    def _apply_locale(self):
        QLocale.setDefault(QLocale(self.state.get_language()))

    def init_ui(self):
        self.setWindowTitle(tr("window_title"))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        # Deliberately below the dashboard's layout minimum: on small screens
        # Windows clamps the window anyway, and the scroll area below keeps
        # every control reachable instead of clipping text.
        self.setMinimumSize(720, 480)
        main_container = QWidget()
        main_container.setObjectName("mainContainer")
        self.setCentralWidget(main_container)
        overall_layout = QVBoxLayout(main_container)
        overall_layout.setContentsMargins(0, 0, 0, 0); overall_layout.setSpacing(0)
        self.title_bar = CustomTitleBar(self.state, self.settings_manager, self.theme, self)
        overall_layout.addWidget(self.title_bar)
        content_scroll = QScrollArea()
        content_scroll.setObjectName("contentWidget")
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setWidgetResizable(True)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_scroll.viewport().setAutoFillBackground(False)
        self.dashboard = Dashboard(
            self.state, self.profile_manager, self.settings_manager, self.theme, self
        )
        # The dashboard paints its own $bg via the contentWidget QSS rule.
        self.dashboard.setObjectName("contentWidget")
        content_scroll.setWidget(self.dashboard)
        overall_layout.addWidget(content_scroll, 1)

    def connect_signals(self):
        self.title_bar.minimize_button.clicked.connect(self.showMinimized)
        self.title_bar.maximize_button.clicked.connect(self._toggle_maximize)
        self.title_bar.close_button.clicked.connect(self.close)

        self.gui_tick.connect(self.app_services.on_gui_tick)

        self.state.controller_status_message_changed.connect(self._update_status_bar)
        self.state.controller_status_message_changed.connect(self._on_controller_status_changed)
        self.state.language_changed.connect(self._on_language_changed_by_state)
        QApplication.instance().paletteChanged.connect(self._on_application_palette_changed)

        self.theme.changed.connect(self._on_theme_changed)
        self.dashboard.transient_status.connect(self.set_transient_status)
        self.state.gui_update_interval_ms_changed.connect(self.gui_update_timer.setInterval)
        self.status_clear_timer.timeout.connect(self._clear_transient_status)

    def init_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self.tray_icon = QSystemTrayIcon(self)
        icon = load_app_icon(self.state.paths.app_icon)
        self.setWindowIcon(icon)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip(tr("window_title"))
        tray_menu = QMenu(self)
        show_hide_action = QAction(tr("tray_menu_show_hide"), self)
        show_hide_action.triggered.connect(self.toggle_window_visibility)
        self._profiles_menu = QMenu(tr("tray_menu_profiles"), self)
        self._charge_menu = QMenu(tr("tray_menu_charge_limit"), self)
        quit_action = QAction(tr("tray_menu_quit"), self)
        quit_action.triggered.connect(self._request_quit)
        tray_menu.addAction(show_hide_action)
        tray_menu.addMenu(self._profiles_menu)
        tray_menu.addMenu(self._charge_menu)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.state.profiles_list_changed.connect(lambda *_: self._rebuild_tray_profiles_menu())
        self.state.applied_charge_policy_changed.connect(lambda *_: self._mark_tray_charge_actions())
        self.state.applied_charge_threshold_changed.connect(lambda *_: self._mark_tray_charge_actions())
        self._build_tray_charge_menu()
        self._rebuild_tray_profiles_menu()
        if not self.start_minimized:
            self.tray_icon.show()

    def _rebuild_tray_profiles_menu(self):
        """One checkable action per profile; activation reuses the CLI payload
        path (validation + save + balloon notification for free)."""
        if not hasattr(self, "_profiles_menu"):
            return
        self._profiles_menu.clear()
        active = self.state.get_active_profile_name()
        for name in self.state.get_profile_names():
            action = QAction(name, self._profiles_menu)
            action.setCheckable(True)
            action.setChecked(name == active)
            action.triggered.connect(
                lambda _checked=False, name=name: self.execute_cli_payload(
                    {"profile": name, "show_mode": "notify"}))
            self._profiles_menu.addAction(action)

    _TRAY_CHARGE_THRESHOLDS = (60, 70, 80, 90, 100)

    def _build_tray_charge_menu(self):
        self._charge_actions = []
        entries = [(tr("mode_bios"), CHARGE_POLICY_BIOS, None)]
        for threshold in self._TRAY_CHARGE_THRESHOLDS:
            entries.append((tr("tray_charge_percent", n=threshold), CHARGE_POLICY_CUSTOM, threshold))
        for label, policy, threshold in entries:
            action = QAction(label, self._charge_menu)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, policy=policy, threshold=threshold: self._apply_tray_charge(policy, threshold))
            self._charge_menu.addAction(action)
            self._charge_actions.append((action, policy, threshold))

    def _apply_tray_charge(self, policy: str, threshold: Optional[int]):
        payload = {"charge_policy": policy, "show_mode": "notify"}
        if threshold is not None:
            payload["charge_threshold"] = threshold
        self.execute_cli_payload(payload)

    def _mark_tray_charge_actions(self):
        if not hasattr(self, "_charge_actions"):
            return
        applied_policy = self.state.get_applied_charge_policy()
        applied_threshold = self.state.get_applied_charge_threshold()
        if (applied_policy == CHARGE_POLICY_CUSTOM
                and 0 < applied_threshold
                and applied_threshold not in self._TRAY_CHARGE_THRESHOLDS):
            self._ensure_tray_charge_threshold_action(applied_threshold)
        for action, policy, threshold in self._charge_actions:
            action.setChecked(
                policy == applied_policy
                and (threshold is None or threshold == applied_threshold))

    def _ensure_tray_charge_threshold_action(self, threshold: int):
        """Off-list applied limit (e.g. 55): insert a matching entry so the
        submenu always shows which threshold is live."""
        if any(th == threshold for _action, _policy, th in self._charge_actions):
            return
        action = QAction(tr("tray_charge_percent", n=threshold), self._charge_menu)
        action.setCheckable(True)
        action.triggered.connect(
            lambda _checked=False, threshold=threshold: self._apply_tray_charge(
                CHARGE_POLICY_CUSTOM, threshold))
        insert_index = 1 + sum(
            1 for _a, policy, th in self._charge_actions
            if policy == CHARGE_POLICY_CUSTOM and th is not None and th < threshold)
        before_action = (self._charge_actions[insert_index][0]
                         if insert_index < len(self._charge_actions) else None)
        if before_action is not None:
            self._charge_menu.insertAction(before_action, action)
        else:
            self._charge_menu.addAction(action)
        self._charge_actions.insert(insert_index, (action, CHARGE_POLICY_CUSTOM, threshold))

    def _on_theme_changed(self):
        """A palette switch: restyle the window, then refresh palette-driven widgets."""
        self.setStyleSheet(self.theme.stylesheet())
        self.title_bar.apply_theme()
        self.dashboard.apply_theme()

    @Slot(str)
    def _update_status_bar(self, message: str):
        if self._is_showing_transient_status:
            return
        text = message or ""
        if text == tr("wmi_error"):
            level = "error"
        elif text == tr("paused"):
            level = "warn"
        else:
            level = "info"
        self._set_status_text(text, level)

    _STATUS_MAX_WIDTH = 360  # keeps the bar breathable down to minimum width

    def _set_status_text(self, text: str, level: str) -> None:
        metrics = self.title_bar.status_label.fontMetrics()
        if metrics.horizontalAdvance(text) > self._STATUS_MAX_WIDTH:
            self.title_bar.status_label.setText(
                metrics.elidedText(text, Qt.TextElideMode.ElideRight, self._STATUS_MAX_WIDTH))
            self.title_bar.status_label.setToolTip(text)
        else:
            self.title_bar.status_label.setText(text)
            self.title_bar.status_label.setToolTip("")
        if self.title_bar.status_label.property("level") != level:
            self.title_bar.status_label.setProperty("level", level)
            repolish(self.title_bar.status_label)

    def set_transient_status(self, message: str, duration_ms: int = 2000, level: str = "info"):
        self.status_clear_timer.stop()
        self._set_status_text(message, level)
        self._is_showing_transient_status = True
        self.status_clear_timer.start(duration_ms)

    def _clear_transient_status(self):
        self._is_showing_transient_status = False
        self._update_status_bar(self.state.get_controller_status_message())

    @Slot(str)
    def _on_language_changed_by_state(self, lang_code: str):
        set_language(lang_code)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(tr("window_title"))
        self.title_bar.retranslate_ui()
        self.dashboard.retranslate_ui()
        if self.tray_icon and self.tray_icon.contextMenu():
            actions = self.tray_icon.contextMenu().actions()
            actions[0].setText(tr("tray_menu_show_hide"))
            actions[-1].setText(tr("tray_menu_quit"))
        self._update_status_bar(self.state.get_controller_status_message())

    def _start_gui_timer(self):
        self.gui_update_timer.setInterval(self.state.get_gui_update_interval_ms())
        try:
            self.gui_update_timer.timeout.disconnect(self.gui_tick.emit)
        except (TypeError, RuntimeError):
            pass
        self.gui_update_timer.timeout.connect(self.gui_tick.emit)
        self.gui_update_timer.start()

    def _install_resume_filter(self):
        app = QApplication.instance()
        if app is None or os.name != "nt":
            return
        self._last_ac_line: Optional[bool] = None
        self._resume_filter = _WindowsResumeFilter(self.app_services.note_resume, self._on_power_status_change)
        app.installNativeEventFilter(self._resume_filter)

    def _on_power_status_change(self):
        """PBT_APMPOWERSTATUSCHANGE: forward each actual AC attach/detach to
        AppServices (profile binding switch + charge state re-verify)."""
        if os.name != "nt":
            return
        try:
            status = _SYSTEM_POWER_STATUS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return
            if status.ACLineStatus == 0xFF:
                return  # unknown - keep the last known state
            on_ac = status.ACLineStatus == 1
            if on_ac == self._last_ac_line:
                return  # duplicate broadcast, no transition
            self._last_ac_line = on_ac
            self.app_services.on_power_source_changed(on_ac)
        except Exception:
            pass

    def _start_command_poller(self):
        from tools.single_instance import (
            read_command_from_shared_memory, read_payload_from_shared_memory,
            write_command_to_shared_memory, clear_payload_in_shared_memory, COMMAND_NONE
        )
        self.command_poll_timer = QTimer(self)
        self.command_poll_timer.setInterval(500)
        def _on_poll_tick():
            command = read_command_from_shared_memory()
            if command is not None and command != COMMAND_NONE:
                payload = read_payload_from_shared_memory()
                write_command_to_shared_memory(COMMAND_NONE)
                clear_payload_in_shared_memory()
                self._handle_command(command, payload)
        self.command_poll_timer.timeout.connect(_on_poll_tick)
        self.command_poll_timer.start()

    def _handle_command(self, command: int, payload: Optional[Dict[str, Any]] = None):
        payload = payload or {}
        if command == COMMAND_QUIT: self._request_quit()
        elif command == COMMAND_RELOAD_AND_SHOW:
            self.profile_manager.reload_and_apply_active_profile()
            self.toggle_window_visibility(force_show=True)
        elif command == COMMAND_RELOAD_ONLY:
            self.profile_manager.reload_and_apply_active_profile()
        elif command == COMMAND_CLI_ACTION:
            self.execute_cli_payload(payload)

    def _show_cli_notification(self, title_key: str, message: str, critical: bool = False):
        if not self.tray_icon:
            # No tray (rare): faults must still surface; confirmations are droppable.
            if critical and os.name == "nt":
                try:
                    ctypes.windll.user32.MessageBoxW(0, message, tr(title_key), 0x10)
                except Exception:
                    pass
            return
        # Fault and thermal alerts (critical=True) ignore the preference.
        if not critical and not self.state.get_notifications_enabled():
            return
        icon = QSystemTrayIcon.MessageIcon.Critical if critical else QSystemTrayIcon.MessageIcon.Information
        self.tray_icon.showMessage(tr(title_key), message, icon, 3000)

    @Slot(str)
    def _on_controller_status_changed(self, msg: str):
        neutral = {"", tr("initializing"), tr("paused"), tr("saving_config"), tr("shutting_down")}
        if msg in neutral:
            self._last_status_notified = None
        if not self.tray_icon or self.isVisible() or msg in neutral:
            return
        if msg == self._last_status_notified:
            return
        self._last_status_notified = msg
        if msg == tr("wmi_error"):
            self._show_cli_notification("wmi_error", msg, critical=True)
        else:
            self._show_cli_notification("controller_notice_title", msg, critical=False)

    def execute_cli_payload(self, payload: Dict[str, Any]):
        profile_name = payload.get("profile")
        show_mode = payload.get("show_mode", "notify")
        charge_policy = payload.get("charge_policy")
        charge_threshold = payload.get("charge_threshold")

        profile_changed = False
        if isinstance(profile_name, str) and profile_name.strip():
            profile_name = profile_name.strip()
            if profile_name not in self.state.get_profile_names():
                error_msg = tr("cli_profile_not_found", profile=profile_name)
                self._show_cli_notification("cli_action_failed_title", error_msg, critical=True)
                return
            self.profile_manager.activate_profile(profile_name)
            profile_changed = True

        active_profile = self.state.get_active_profile()
        if charge_policy in (CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM) and active_profile:
            active_profile.set_battery_charge_policy(charge_policy)
            if charge_policy == CHARGE_POLICY_CUSTOM and isinstance(charge_threshold, int):
                active_profile.set_battery_charge_threshold(charge_threshold)
            self.profile_manager.save_config()

        if show_mode == "show":
            self.toggle_window_visibility(force_show=True)
            return

        if show_mode == "notify":
            if profile_changed and charge_policy:
                msg = tr("cli_profile_and_policy_applied", profile=profile_name, policy=charge_policy)
            elif profile_changed:
                msg = tr("cli_profile_applied", profile=profile_name)
            elif charge_policy:
                msg = tr("cli_policy_applied", policy=charge_policy)
            else:
                msg = tr("cli_no_action")
            self._show_cli_notification("cli_action_done_title", msg, critical=False)

    def showEvent(self, event: QShowEvent):
        if not self._is_initialized:
            self._is_initialized = True
            QTimer.singleShot(0, lambda: self.window_initialized_signal.emit(int(self.winId())))

        # 调用AppServices中的方法来处理UI可见性变化
        self.app_services.set_ui_visibility(True)
        self._start_gui_timer()
        if self.command_poll_timer:
            self.command_poll_timer.setInterval(500)
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent):
        self.gui_update_timer.stop()
        # Tray-only: shared-memory CLI commands are rare; a 2 s poll keeps
        # the timer wakeups out of the idle profile. Tray-menu actions are
        # in-process and unaffected.
        if self.command_poll_timer:
            self.command_poll_timer.setInterval(2000)
        # 调用AppServices中的方法来处理UI可见性变化
        self.app_services.set_ui_visibility(False)
        super().hideEvent(event)

    def _toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.update_window_state(self.isMaximized())
        elif event.type() == QEvent.Type.ThemeChange:
            if self.theme.mode() == THEME_MODE_SYSTEM:
                self.theme.refresh()
        super().changeEvent(event)

    def _on_application_palette_changed(self):
        """Backup signal for system theme changes on builds that skip ThemeChange."""
        if self.theme.mode() == THEME_MODE_SYSTEM:
            self.theme.refresh()

    if sys.platform == 'win32':
        def nativeEvent(self, event_type: Any, message: int) -> tuple[bool, int]:
            from ctypes.wintypes import MSG
            msg = MSG.from_address(message.__int__())

            if msg.message == 0x0083: # WM_NCCALCSIZE
                return True, 0

            if msg.message == 0x00A3: # WM_NCLBUTTONDBLCLK
                pos = QPoint(msg.lParam & 0xFFFF, (msg.lParam >> 16) & 0xFFFF)
                # 检查双击是否在标题栏的非按钮区域
                if self.title_bar.geometry().contains(self.mapFromGlobal(pos)):
                    child = self.title_bar.childAt(self.title_bar.mapFromGlobal(pos))
                    if not isinstance(child, (QPushButton, QComboBox)):
                        self._toggle_maximize()
                        return True, 0

            if msg.message == 0x0084: # WM_NCHITTEST
                # 修复：使用 Qt 的 QCursor.pos() 自动处理高 DPI 缩放
                # 不要用 msg.lParam，它在开启缩放的屏幕上返回物理像素，会导致底部控件被误判为窗口边缘
                global_pos = QCursor.pos()
                pos = self.mapFromGlobal(global_pos)

                # 仅在窗口化状态下检查边框用于缩放
                if not self.isMaximized():
                    border = 8
                    on_left = pos.x() < border
                    on_right = pos.x() > self.width() - border
                    on_top = pos.y() < border
                    on_bottom = pos.y() > self.height() - border

                    if on_top and on_left: return True, 13
                    if on_top and on_right: return True, 14
                    if on_bottom and on_left: return True, 16
                    if on_bottom and on_right: return True, 17
                    if on_left: return True, 10
                    if on_right: return True, 11
                    if on_top: return True, 12
                    if on_bottom: return True, 15

                # 检查标题栏区域
                if self.title_bar and pos.y() < self.title_bar.height():
                    local_pos = self.title_bar.mapFromGlobal(global_pos)
                    child = self.title_bar.childAt(local_pos)

                    # 递归检查，确保下拉框等复杂控件不会被误判为拖拽区
                    is_interactive = False
                    current_child = child
                    while current_child:
                        if isinstance(current_child, (QPushButton, QComboBox)):
                            is_interactive = True
                            break
                        current_child = current_child.parentWidget()

                    if is_interactive:
                        result = super().nativeEvent(event_type, message)
                        return (bool(result[0]), int(result[1])) if isinstance(result, (tuple, list)) and len(result) == 2 else (False, 0)
                    return True, 2 # HTCAPTION (返回 2 让 Windows 接管标题栏拖拽)

            result = super().nativeEvent(event_type, message)
            return (bool(result[0]), int(result[1])) if isinstance(result, (tuple, list)) and len(result) == 2 else (False, 0)

    @Slot(QSystemTrayIcon.ActivationReason)
    def on_tray_icon_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_window_visibility()

    def toggle_window_visibility(self, force_show: bool = False):
        if force_show or not self.isVisible() or self.isMinimized():
            # show() (not showNormal()): a window hidden while maximized must
            # come back maximized, and a minimized one must just un-minimize.
            if self.isMinimized():
                self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
            self.activateWindow(); self.raise_()
        else: self.hide()

    def _request_quit(self):
        if self._is_quitting: return
        self._is_quitting = True
        self._shutdown_services()
        app = QApplication.instance()
        if app: app.quit()

    def _shutdown_services(self):
        try:
            geometry_str = bytes(self.saveGeometry().toHex().data()).decode('ascii')
            self.settings_manager.set_window_geometry(geometry_str)
        except Exception as e:
            print(f"Warning: failed to save window geometry on close: {e}", file=sys.stderr)
        # Debounced saves never fire after quit: flush the config inline.
        try:
            self.profile_manager.save_config_sync()
        except Exception as e:
            print(f"Warning: failed to save config on close: {e}", file=sys.stderr)

    def closeEvent(self, event: QCloseEvent):
        if self._is_quitting: event.accept()
        elif self.tray_icon and self.tray_icon.isVisible():
            # First close is invisible to a new user: say the app keeps
            # running (once per process, not persisted).
            if not self._tray_close_hint_shown:
                self._tray_close_hint_shown = True
                self.tray_icon.showMessage(
                    tr("tray_still_running_title"), tr("tray_still_running_msg"),
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            self.hide(); event.ignore()
        else:
            self._request_quit(); event.accept()
