# -*- coding: utf-8 -*-
"""
OS-level utility functions, mainly for Windows-specific operations.
The unreliable path helpers were removed; the logic is now more centralized and robust.
"""

import sys
import os
import ctypes
import subprocess

from .localization import tr

def is_admin() -> bool:
    """Check whether the script is running with administrator rights on Windows."""
    if os.name == 'nt':
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except AttributeError:
            print("Warning: could not determine admin status via ctypes.", file=sys.stderr)
            return False
    else:
        return True

def run_as_admin(executable_path: str, base_dir: str):
    """
    Try to relaunch the application with administrator rights on Windows.
    """
    if os.name != 'nt':
        return

    if is_admin():
        return

    error_title = tr("elevation_error_title")
    error_msg_base = tr("elevation_error_msg")

    try:
        is_script_mode = os.path.basename(executable_path).lower() in ('python.exe', 'pythonw.exe')

        if is_script_mode:
            # In script mode we need to resolve the main script's path
            main_script_path = os.path.abspath(sys.argv[0])
            params = f'"{main_script_path}" {subprocess.list2cmdline(sys.argv[1:])}'
        else:
            params = subprocess.list2cmdline(sys.argv[1:])

        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable_path, params, base_dir, 1)

        if result <= 32:
            if result == 1223:
                sys.exit(1)
            else:
                detailed_error = f"ShellExecuteW failed with code: {result}."
                final_error_msg = f"{error_msg_base}\n\n{detailed_error}"
                ctypes.windll.user32.MessageBoxW(0, final_error_msg, error_title, 0x10 | 0x1000)
                sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        detailed_error = f"Unexpected error during elevation: {e}"
        final_error_msg = f"{error_msg_base}\n\n{detailed_error}"
        ctypes.windll.user32.MessageBoxW(0, final_error_msg, error_title, 0x10 | 0x1000)
        sys.exit(1)

# Removed get_application_executable_path and get_application_script_path_for_task:
# those two functions were the root cause of the path problems; all paths now come from PathManager.