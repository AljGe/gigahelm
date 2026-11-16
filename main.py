# -*- coding: utf-8 -*-
"""
Application entry point: initialization, single-instance check, admin-rights check, and starting the Qt event loop.
Determines all authoritative paths once via the most reliable method and injects them into PathManager, eliminating packaged-mode path problems at the root.
"""

import sys
import os
import traceback
import atexit
import ctypes
from datetime import datetime

# In packaged/scheduled-task modes stdout may be None or a cp1252 pipe; force UTF-8 with replacement for unencodable chars so printing never crashes
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

import argparse
from typing import Optional, Any, Dict, Tuple

# --- Authoritative path resolution ---
# This is the only place in the entire app where core paths are determined.
EXECUTABLE_PATH: str = ""
BASE_DIR: str = ""
MAIN_SCRIPT_PATH: str = ""
IS_FROZEN: bool = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

try:
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(2048)
        ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        # EXECUTABLE_PATH is always the .exe the user launched, or python.exe in script mode
        EXECUTABLE_PATH = buffer.value
    else:
        # Fallback for non-Windows systems
        EXECUTABLE_PATH = sys.executable

    executable_name = os.path.basename(EXECUTABLE_PATH).lower()
    is_script_mode = executable_name in ('python.exe', 'pythonw.exe')

    if is_script_mode:
        # In script mode, the base directory is the main script's directory
        MAIN_SCRIPT_PATH = os.path.abspath(__file__ if "__file__" in locals() else sys.argv[0])
        BASE_DIR = os.path.dirname(MAIN_SCRIPT_PATH)
    else:
        # In packaged mode, the base directory is the .exe's directory
        BASE_DIR = os.path.dirname(EXECUTABLE_PATH)

except Exception:
    BASE_DIR = os.getcwd()
    EXECUTABLE_PATH = sys.executable

try:
    os.chdir(BASE_DIR)
except Exception as e:
    print(f"Fatal error: failed to change working directory to '{BASE_DIR}'. External files will not be found. Error: {e}", file=sys.stderr)

# --- Project module imports ---
from gui.qt import QApplication, QMessageBox, QCoreApplication

from config.settings import (
    APP_NAME, APP_ORGANIZATION_NAME, APP_INTERNAL_NAME, STARTUP_ARG_MINIMIZED,
    REVERT_BIOS_ARG, COMMAND_CLI_ACTION, CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM,
    MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT
)
from tools.localization import load_translations, tr, set_language, _translations_loaded
from tools.system_utils import is_admin, run_as_admin
from tools.task_scheduler import (
    create_startup_task, is_startup_task_registered,
    create_bios_watchdog_task, is_bios_watchdog_task_registered,
)
from tools.bios_watchdog import run_bios_revert_watchdog
from tools.single_instance import (
    check_single_instance, release_mutex, write_hwnd_to_shared_memory
)
from core.app_services import AppServices
from core.state import AppState
from core.path_manager import PathManager
from core.profile_manager import ProfileManager
from core.settings_manager import SettingsManager
from gui.main_window import MainWindow
from emergency_fan_setter import set_emergency_fan_speed

_app_services_for_cleanup: Optional[AppServices] = None
_emergency_latched = False


def _parse_cli_arguments(argv: list[str]) -> Tuple[argparse.Namespace, Optional[str]]:
    """Parse and validate command-line arguments. Returns (args, error_message)."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(STARTUP_ARG_MINIMIZED, action="store_true", dest="minimized")
    parser.add_argument("--profile", type=str, default=None)
    parser.add_argument("--show-mode", type=str, choices=["show", "notify", "silent"], default=None)
    parser.add_argument("--charge-policy", type=str, choices=[CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM], default=None)
    parser.add_argument("--charge-threshold", type=int, default=None)
    parser.add_argument(REVERT_BIOS_ARG, action="store_true", dest="revert_bios")
    parser.add_argument("--diagnose", action="store_true", dest="diagnose")

    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit:
        return argparse.Namespace(), "Invalid command-line arguments."

    if unknown:
        return args, f"Unknown command-line arguments: {' '.join(unknown)}"

    if args.charge_threshold is not None and args.charge_policy != CHARGE_POLICY_CUSTOM:
        return args, "--charge-threshold can only be used together with --charge-policy custom."

    if args.charge_threshold is not None and not (MIN_CHARGE_PERCENT <= args.charge_threshold <= MAX_CHARGE_PERCENT):
        return args, f"--charge-threshold must be between {MIN_CHARGE_PERCENT}-{MAX_CHARGE_PERCENT}."

    if args.profile is not None and not args.profile.strip():
        return args, "--profile must not be empty."

    if args.show_mode is None and (args.profile is not None or args.charge_policy is not None):
        args.show_mode = "notify"

    return args, None

# --- Crash-safety mechanism ---
def handle_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler. In auto or custom mode, latches the emergency fan speed in-process."""
    global _emergency_latched
    try:
        last_mode_file = os.path.join(BASE_DIR, 'last_mode.state')
        if os.path.exists(last_mode_file):
            with open(last_mode_file, 'r') as f:
                last_mode = f.read().strip()
            if last_mode in ('auto', 'custom'):
                set_emergency_fan_speed()
                _emergency_latched = True
    except Exception as safety_e:
        print(f"CRASH HANDLER: error while running safety mechanism: {safety_e}", file=sys.stderr)

    error_msg_detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    # stderr may be a cp1252-encoded pipe (printing Chinese text would crash a second time and skip cleanup); write the crash log to disk as UTF-8
    try:
        with open(os.path.join(BASE_DIR, 'crash.log'), 'a', encoding='utf-8', errors='replace') as crash_file:
            crash_file.write(f"[{datetime.now().isoformat(timespec='seconds')}] Unhandled exception:\n{error_msg_detail}\n")
    except Exception:
        pass
    print(f"Unhandled exception:\n{error_msg_detail}", file=sys.stderr)

    try:
        title = tr("unhandled_exception_title") if '_translations_loaded' in globals() and _translations_loaded else "Unhandled Exception"
        msg = f"A critical error occurred:\n\n{exc_value}\n\nPlease report this issue."
        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(None, title, msg)
    except Exception as e:
        print(f"Error showing exception message box: {e}", file=sys.stderr)
        if os.name == 'nt':
            try:
                ctypes.windll.user32.MessageBoxW(0, f"Unhandled exception:\n{exc_value}", "Critical Error", 0x10)
            except Exception: pass

    print("Attempting cleanup after unhandled exception...")
    perform_cleanup()
    print("Forcing exit after exception.")
    os._exit(1)

# --- Cleanup ---
_cleanup_called = False
def perform_cleanup():
    """Run application cleanup on exit."""
    global _app_services_for_cleanup, _cleanup_called
    if _cleanup_called:
        return
    _cleanup_called = True
    print("Performing cleanup...")

    if _app_services_for_cleanup:
        print("Shutting down AppServices...")
        try:
            _app_services_for_cleanup.shutdown()
        except Exception as e:
            print(f"Error during AppServices shutdown: {e}", file=sys.stderr)
        _app_services_for_cleanup = None
        print("AppServices shutdown complete.")
    else:
        print("No AppServices instance found to shut down.")

    print("Releasing mutex and shared memory...")
    release_mutex()
    print("Mutex and shared memory released.")

    print("Cleanup complete.")

# --- Main function ---
def main():
    """Main application function."""
    global _app_services_for_cleanup

    cli_args, cli_error = _parse_cli_arguments(sys.argv[1:])
    if cli_error:
        print(cli_error, file=sys.stderr)
        sys.exit(2)

    if cli_args.revert_bios:
        sys.excepthook = handle_exception
        if os.name == 'nt' and not is_admin():
            run_as_admin(EXECUTABLE_PATH, BASE_DIR)
            sys.exit(1)
        sys.exit(run_bios_revert_watchdog(BASE_DIR))

    if cli_args.diagnose:
        sys.excepthook = handle_exception
        if os.name == 'nt' and not is_admin():
            run_as_admin(EXECUTABLE_PATH, BASE_DIR)
            sys.exit(1)
        from tools.diagnose import run_diagnose
        report_path = run_diagnose(BASE_DIR, is_admin())
        try:
            ctypes.windll.user32.MessageBoxW(0, f"Diagnostic report written to:\n{report_path}", APP_NAME, 0x40)
        except Exception:
            print(report_path)
        sys.exit(0)

    sys.excepthook = handle_exception
    atexit.register(perform_cleanup)

    has_cli_action = bool(cli_args.profile or cli_args.charge_policy)
    cli_payload: Optional[Dict[str, Any]] = None
    if has_cli_action:
        cli_payload = {
            "profile": cli_args.profile,
            "show_mode": cli_args.show_mode,
            "charge_policy": cli_args.charge_policy,
            "charge_threshold": cli_args.charge_threshold
        }

    # --- Initialize core services ---
    # 1. Create the path manager, injecting all authoritative paths
    path_manager = PathManager(
        base_dir=BASE_DIR,
        executable_path=EXECUTABLE_PATH,
        main_script_path=MAIN_SCRIPT_PATH
    )

    load_translations(path_manager.languages)

    if os.name == 'nt':
        if not is_admin():
            run_as_admin(EXECUTABLE_PATH, BASE_DIR)
            sys.exit(1)
        
        is_task_launch = bool(cli_args.minimized)
        command_override = COMMAND_CLI_ACTION if has_cli_action else None
        if not check_single_instance(
            is_task_launch=is_task_launch,
            command_override=command_override,
            payload=cli_payload
        ):
            print("Another instance is running. Command sent; this instance will exit.")
            sys.exit(0)

    QCoreApplication.setOrganizationName(APP_ORGANIZATION_NAME)
    QCoreApplication.setApplicationName(APP_INTERNAL_NAME)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(perform_cleanup)

    try:
        app_state = AppState(path_manager=path_manager)
        profile_manager = ProfileManager(app_state=app_state)
        settings_manager = SettingsManager(app_state=app_state, profile_manager=profile_manager)
        
        app_services = AppServices(state=app_state)
        _app_services_for_cleanup = app_services

        if not app_services.initialize_wmi():
            error_title = tr("wmi_init_error_title")
            error_text = app_state.get_controller_status_message()
            QMessageBox.critical(None, error_title, error_text)
            sys.exit(1)

        profile_manager.load_config()

        if os.name == "nt" and app_state.get_start_on_boot() and not is_startup_task_registered():
            try:
                create_startup_task(path_manager)
            except Exception as boot_heal_error:
                print(f"Failed to auto-repair startup task: {boot_heal_error}", file=sys.stderr)

        # BIOS-revert watchdog: the hard-kill safety net. Register it whenever
        # it is missing (independent of Start on Boot) - the app is elevated here.
        if os.name == "nt" and not is_bios_watchdog_task_registered():
            try:
                create_bios_watchdog_task(path_manager)
            except Exception as watchdog_heal_error:
                print(f"Failed to register BIOS-revert watchdog task: {watchdog_heal_error}", file=sys.stderr)

        set_language(app_state.get_language())
        
        start_minimized = bool(cli_args.minimized) or (has_cli_action and cli_args.show_mode != "show")
        main_window = MainWindow(
            app_services=app_services, 
            state=app_state,
            profile_manager=profile_manager,
            settings_manager=settings_manager,
            start_minimized=start_minimized,
            startup_cli_payload=cli_payload
        )

        if os.name == 'nt':
            main_window.window_initialized_signal.connect(write_hwnd_to_shared_memory)

    except Exception as init_error:
        handle_exception(type(init_error), init_error, init_error.__traceback__)
        sys.exit(1)

    print(f"Starting {APP_NAME} event loop...")
    exit_code = app.exec()
    print(f"Application event loop finished, exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()