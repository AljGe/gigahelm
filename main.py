# -*- coding: utf-8 -*-
"""
应用入口，负责初始化、单例检查、管理员权限检查，并启动Qt事件循环。
使用最可靠的方式一次性确定所有权威路径，并将其注入PathManager，从根本上解决打包后的路径问题。
"""

import sys
import os
import traceback
import atexit
import ctypes
import subprocess
from datetime import datetime

# 打包/任务模式下 stdout 可能为 None 或 cp1252 管道；统一为 UTF-8 且不可编码字符替换，避免打印中文时崩溃
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

import argparse
from typing import Optional, Any, Dict, Tuple

# --- 权威路径确定 ---
# 这是整个应用中唯一应该确定核心路径的地方。
EXECUTABLE_PATH: str = ""
BASE_DIR: str = ""
MAIN_SCRIPT_PATH: str = ""
IS_FROZEN: bool = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

try:
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(2048)
        ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        # EXECUTABLE_PATH 始终是用户双击的那个 .exe 文件，或脚本模式下的 python.exe
        EXECUTABLE_PATH = buffer.value
    else:
        # 为非Windows系统提供回退
        EXECUTABLE_PATH = sys.executable

    executable_name = os.path.basename(EXECUTABLE_PATH).lower()
    is_script_mode = executable_name in ('python.exe', 'pythonw.exe')

    if is_script_mode:
        # 脚本模式下，基础目录是主脚本所在目录
        MAIN_SCRIPT_PATH = os.path.abspath(__file__ if "__file__" in locals() else sys.argv[0])
        BASE_DIR = os.path.dirname(MAIN_SCRIPT_PATH)
    else:
        # 打包模式下，基础目录是 .exe 文件所在的目录
        BASE_DIR = os.path.dirname(EXECUTABLE_PATH)

except Exception:
    BASE_DIR = os.getcwd()
    EXECUTABLE_PATH = sys.executable

try:
    os.chdir(BASE_DIR)
except Exception as e:
    print(f"Fatal error: failed to change working directory to '{BASE_DIR}'. External files will not be found. Error: {e}", file=sys.stderr)

# --- 早退入口：GUI 导入前处理，避免看门狗每次启动都加载 Qt ---
_EARLY_ARGS = set(sys.argv[1:])
if _EARLY_ARGS.intersection({"--revert-bios", "--emergency-fan"}):
    from tools.system_utils import is_admin, run_as_admin
    if os.name == "nt" and not is_admin():
        run_as_admin(EXECUTABLE_PATH, BASE_DIR)
        sys.exit(1)

    if "--emergency-fan" in _EARLY_ARGS:
        try:
            from emergency_fan_setter import set_emergency_fan_speed
            sys.exit(0 if set_emergency_fan_speed() else 1)
        except Exception as error:
            print(f"Emergency fan setter failed: {error!r}", file=sys.stderr)
            sys.exit(1)

    from tools.bios_watchdog import run_bios_revert_watchdog
    sys.exit(run_bios_revert_watchdog(BASE_DIR))

# --- 导入项目模块 ---
from gui.qt import QApplication, QMessageBox, QCoreApplication

from config.settings import (
    APP_NAME, APP_ORGANIZATION_NAME, APP_INTERNAL_NAME, STARTUP_ARG_MINIMIZED,
    COMMAND_CLI_ACTION, CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM,
    MIN_CHARGE_PERCENT, MAX_CHARGE_PERCENT, LAST_MODE_FILENAME,
)
from tools.localization import tr, set_language
from tools.system_utils import is_admin, run_as_admin
from tools.task_scheduler import (
    create_startup_task, startup_task_needs_repair, ensure_watchdog_task,
)
from tools.single_instance import (
    check_single_instance, release_mutex, write_hwnd_to_shared_memory
)
from core.app_services import AppServices
from core.state import AppState
from core.path_manager import PathManager
from core.profile_manager import ProfileManager
from core.settings_manager import SettingsManager
from gui.main_window import MainWindow
from gui.theme import ThemeManager, apply_app_font

_app_services_for_cleanup: Optional[AppServices] = None
_emergency_latched = False


def _parse_cli_arguments(argv: list[str]) -> Tuple[argparse.Namespace, Optional[str]]:
    """解析并验证命令行参数。返回(args, error_message)。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(STARTUP_ARG_MINIMIZED, action="store_true", dest="minimized")
    parser.add_argument("--profile", type=str, default=None)
    parser.add_argument("--show-mode", type=str, choices=["show", "notify", "silent"], default=None)
    parser.add_argument("--charge-policy", type=str, choices=[CHARGE_POLICY_BIOS, CHARGE_POLICY_CUSTOM], default=None)
    parser.add_argument("--charge-threshold", type=int, default=None)

    try:
        args, unknown = parser.parse_known_args(argv)
    except SystemExit:
        return argparse.Namespace(), "命令行参数格式无效。"

    if unknown:
        return args, f"Unknown command-line arguments: {' '.join(unknown)}"

    if args.charge_threshold is not None and args.charge_policy != CHARGE_POLICY_CUSTOM:
        return args, "--charge-threshold 只能和 --charge-policy custom 一起使用。"

    if args.charge_threshold is not None and not (MIN_CHARGE_PERCENT <= args.charge_threshold <= MAX_CHARGE_PERCENT):
        return args, f"--charge-threshold must be between {MIN_CHARGE_PERCENT}-{MAX_CHARGE_PERCENT}."

    if args.profile is not None and not args.profile.strip():
        return args, "--profile 不能为空。"

    if args.show_mode is None and (args.profile is not None or args.charge_policy is not None):
        args.show_mode = "notify"

    return args, None

def _write_state_file(mode: str) -> None:
    try:
        path = os.path.join(BASE_DIR, LAST_MODE_FILENAME)
        with open(path, "w", encoding="ascii") as f:
            f.write(mode)
            f.flush()
            os.fsync(f.fileno())
    except OSError as error:
        print(f"CRASH HANDLER: could not write {LAST_MODE_FILENAME}: {error}", file=sys.stderr)


def _spawn_emergency_fan_setter() -> bool:
    """Run the 80% setter in a separate process; never touch COM in the crashed process."""
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--emergency-fan"]
    else:
        command = [sys.executable, os.path.join(BASE_DIR, "emergency_fan_setter.py")]

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            return False
        return process.returncode == 0
    except Exception as error:
        print(f"CRASH HANDLER: could not start emergency fan setter: {error!r}", file=sys.stderr)
        return False


# --- 崩溃安全机制 ---
def handle_exception(exc_type, exc_value, exc_traceback):
    """全局异常处理器。自动/自定义模式在独立进程中锁存 80% 紧急风扇速度。"""
    global _emergency_latched
    try:
        last_mode_file = os.path.join(BASE_DIR, LAST_MODE_FILENAME)
        if os.path.exists(last_mode_file):
            with open(last_mode_file, 'r', encoding='utf-8', errors='replace') as f:
                last_mode = f.read().strip()
            if last_mode in ('auto', 'custom', 'emergency'):
                if _spawn_emergency_fan_setter():
                    _emergency_latched = True
                    if _app_services_for_cleanup:
                        _app_services_for_cleanup.mark_emergency_latched()
                    _write_state_file('emergency')
                else:
                    print("CRASH HANDLER: emergency fan setter failed.", file=sys.stderr)
    except Exception as safety_e:
        print(f"CRASH HANDLER: error while running safety mechanism: {safety_e}", file=sys.stderr)

    error_msg_detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    # stderr 可能是 cp1252 编码的管道（打印中文会二次崩溃并跳过清理），落盘 UTF-8 崩溃日志
    try:
        with open(os.path.join(BASE_DIR, 'crash.log'), 'a', encoding='utf-8', errors='replace') as crash_file:
            crash_file.write(f"[{datetime.now().isoformat(timespec='seconds')}] Unhandled exception:\n{error_msg_detail}\n")
    except Exception:
        pass
    print(f"Unhandled exception:\n{error_msg_detail}", file=sys.stderr)

    try:
        title = tr("unhandled_exception_title")
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

# --- 清理函数 ---
_cleanup_called = False
def perform_cleanup():
    """在退出时执行应用清理操作。"""
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

# --- 主函数 ---
def main():
    """主应用函数。"""
    global _app_services_for_cleanup

    cli_args, cli_error = _parse_cli_arguments(sys.argv[1:])
    if cli_error:
        print(cli_error, file=sys.stderr)
        sys.exit(2)

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

    # --- 初始化核心服务 ---
    # 1. 创建路径管理器，注入所有权威路径
    path_manager = PathManager(
        base_dir=BASE_DIR,
        executable_path=EXECUTABLE_PATH,
        main_script_path=MAIN_SCRIPT_PATH
    )

    if os.name == 'nt':
        if not is_admin():
            run_as_admin(EXECUTABLE_PATH, BASE_DIR)
            sys.exit(1)
        # The fan-control loop must outrank arbitrary CPU load. Launchers
        # have been observed handing us a BELOW_NORMAL process class, which
        # silently starves the control worker while burners saturate every
        # core (fans parked at 0-25 % at 90 C for minutes).
        from tools.system_utils import set_process_priority_above_normal
        if not set_process_priority_above_normal():
            print("Warning: could not raise process priority class", file=sys.stderr)
        
        is_task_launch = bool(cli_args.minimized)
        command_override = COMMAND_CLI_ACTION if has_cli_action else None
        if not check_single_instance(
            is_task_launch=is_task_launch,
            command_override=command_override,
            payload=cli_payload
        ):
            print("Another instance is running. Command sent; this instance will exit.")
            sys.exit(0)

        # Independent safety net: never leave a hard-killed or hung instance
        # owning the EC in fixed-fan mode.
        try:
            ensure_watchdog_task(path_manager)
        except Exception as watchdog_error:
            print(f"Failed to register watchdog task: {watchdog_error}", file=sys.stderr)

    QCoreApplication.setOrganizationName(APP_ORGANIZATION_NAME)
    QCoreApplication.setApplicationName(APP_INTERNAL_NAME)
    app = QApplication(sys.argv)
    apply_app_font(app)
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(perform_cleanup)

    try:
        app_state = AppState(path_manager=path_manager)
        profile_manager = ProfileManager(app_state=app_state)
        settings_manager = SettingsManager(app_state=app_state, profile_manager=profile_manager)
        theme_manager = ThemeManager(app_state, settings_manager)
        
        app_services = AppServices(state=app_state)
        _app_services_for_cleanup = app_services

        if not app_services.initialize_wmi():
            error_title = tr("wmi_init_error_title")
            error_text = app_state.get_controller_status_message()
            QMessageBox.critical(None, error_title, error_text)
            sys.exit(1)

        profile_manager.load_config()

        if os.name == "nt" and app_state.get_start_on_boot() and startup_task_needs_repair(path_manager):
            try:
                create_startup_task(path_manager)
            except Exception as boot_heal_error:
                print(f"Failed to auto-repair startup task: {boot_heal_error}", file=sys.stderr)

        set_language(app_state.get_language())
        
        start_minimized = bool(cli_args.minimized) or (has_cli_action and cli_args.show_mode != "show")
        main_window = MainWindow(
            app_services=app_services, 
            state=app_state,
            profile_manager=profile_manager,
            settings_manager=settings_manager,
            theme=theme_manager,
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