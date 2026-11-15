# -*- coding: utf-8 -*-
"""
Windows Task Scheduler integration for the start-on-boot task.

The task XML lives here and only here: `build_startup_task_xml` produces the
exact bytes that are handed to `schtasks /Create /XML`, and the deploy script
calls the same function instead of keeping its own copy.
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Tuple

from config.settings import (
    APP_NAME, TASK_SCHEDULER_NAME, LEGACY_TASK_SCHEDULER_NAME, STARTUP_ARG_MINIMIZED,
    WATCHDOG_TASK_NAME, LEGACY_WATCHDOG_TASK_NAMES, WATCHDOG_INTERVAL_MINUTES,
)
from core.path_manager import PathManager
from .localization import tr

TASK_XML_TEMPLATE = r'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the {{APP_NAME}} application on user logon and system wake.</Description>
    <Author>{{APP_NAME}}</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT15S</Delay>
    </LogonTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=107)]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
    </EventTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
{{USER_ID_LINE}}      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{{COMMAND}}</Command>
      <Arguments>{{ARGUMENTS}}</Arguments>
      <WorkingDirectory>{{WORKING_DIRECTORY}}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''

# Repeating watchdog: fires every minute, ignores overlapping runs, and stays
# invisible.  It intentionally has no RestartOnFailure - a crash-induced
# restart would re-apply Auto on top of the 80% emergency speed.
WATCHDOG_TASK_XML_TEMPLATE = r'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Reverts {{APP_NAME}} fan control to BIOS when the app is gone or its heartbeat is stale.</Description>
    <Author>{{APP_NAME}}</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{{START_BOUNDARY}}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT{{INTERVAL_MINUTES}}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
{{USER_ID_LINE}}      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{{COMMAND}}</Command>
      <Arguments>{{ARGUMENTS}}</Arguments>
      <WorkingDirectory>{{WORKING_DIRECTORY}}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''


def _decode_schtasks_output(data: bytes) -> str:
    """schtasks can emit UTF-16 or the console code page; pick what parses."""
    if not data:
        return ""
    for encoding in ("utf-16", "utf-8", "mbcs"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "<Task" in text or "TaskName" in text or "ERROR" in text.upper():
            return text
    return data.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")


def _run_schtasks(args: list) -> Tuple[bool, str]:
    """Helper that runs schtasks.exe commands and handles output encoding correctly."""
    if os.name != 'nt': return False, "Task Scheduler is only for Windows."
    command = ['schtasks'] + args
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=15,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True, _decode_schtasks_output(result.stdout)

    except (FileNotFoundError, subprocess.CalledProcessError, Exception) as e:
        error_output_bytes = getattr(e, 'stderr', b'') or getattr(e, 'stdout', b'')
        error_output = _decode_schtasks_output(error_output_bytes) if error_output_bytes else str(e)
        return False, f"schtasks command failed: {error_output.strip()}"


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_startup_task_xml(
    command: str,
    arguments: str,
    working_directory: str,
    user_id: Optional[str] = None,
) -> bytes:
    """Render the startup task XML as UTF-16-LE bytes with a BOM."""
    user_id_line = f"      <UserId>{_xml_escape(user_id)}</UserId>\n" if user_id else ""
    xml_str = TASK_XML_TEMPLATE
    xml_str = xml_str.replace("{{APP_NAME}}", _xml_escape(APP_NAME))
    xml_str = xml_str.replace("{{USER_ID_LINE}}", user_id_line)
    xml_str = xml_str.replace("{{COMMAND}}", _xml_escape(command))
    xml_str = xml_str.replace("{{ARGUMENTS}}", _xml_escape(arguments))
    xml_str = xml_str.replace("{{WORKING_DIRECTORY}}", _xml_escape(working_directory))
    return b'\xff\xfe' + xml_str.encode('utf-16-le')


def build_watchdog_task_xml(
    command: str,
    arguments: str,
    working_directory: str,
    user_id: Optional[str] = None,
) -> bytes:
    """Render the repeating BIOS-revert watchdog task as UTF-16-LE with a BOM."""
    start_boundary = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    user_id_line = f"      <UserId>{_xml_escape(user_id)}</UserId>\n" if user_id else ""
    xml_str = WATCHDOG_TASK_XML_TEMPLATE
    xml_str = xml_str.replace("{{APP_NAME}}", _xml_escape(APP_NAME))
    xml_str = xml_str.replace("{{USER_ID_LINE}}", user_id_line)
    xml_str = xml_str.replace("{{START_BOUNDARY}}", start_boundary)
    xml_str = xml_str.replace("{{INTERVAL_MINUTES}}", str(WATCHDOG_INTERVAL_MINUTES))
    xml_str = xml_str.replace("{{COMMAND}}", _xml_escape(command))
    xml_str = xml_str.replace("{{ARGUMENTS}}", _xml_escape(arguments))
    xml_str = xml_str.replace("{{WORKING_DIRECTORY}}", _xml_escape(working_directory))
    return b'\xff\xfe' + xml_str.encode('utf-16-le')


def _script_interpreter() -> str:
    """Prefer pythonw so scheduled tasks never flash a console window."""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else sys.executable


def _watchdog_command(paths: PathManager) -> Tuple[str, str]:
    if paths.is_running_as_script():
        return _script_interpreter(), f'"{paths.main_script_path}" --revert-bios'
    return paths.executable_path, "--revert-bios"


def _startup_command(paths: PathManager) -> Tuple[str, str]:
    if paths.is_running_as_script():
        return _script_interpreter(), f'"{paths.main_script_path}" {STARTUP_ARG_MINIMIZED}'
    return paths.executable_path, STARTUP_ARG_MINIMIZED


def delete_legacy_watchdog_tasks() -> None:
    """Remove pre-rebrand watchdogs that read a different install directory."""
    if os.name != 'nt':
        return
    for name in LEGACY_WATCHDOG_TASK_NAMES:
        _run_schtasks(['/Delete', '/TN', name, '/F'])


def _task_xml_matches(paths: PathManager, task_name: str, expected_args: str) -> bool:
    success, output = _run_schtasks(['/Query', '/TN', task_name, '/XML'])
    if not success:
        return False
    if task_name == TASK_SCHEDULER_NAME and "RestartOnFailure" in output:
        return False  # old task could restart Auto over the 80% emergency speed
    command, _arguments = _watchdog_command(paths) if task_name == WATCHDOG_TASK_NAME else _startup_command(paths)
    normalized_output = os.path.normcase(output)
    normalized_command = os.path.normcase(command)
    return normalized_command in normalized_output and expected_args in output


def startup_task_needs_repair(paths: PathManager) -> bool:
    """Check the task action, not just the task name (a moved exe leaves a stale task)."""
    return not _task_xml_matches(paths, TASK_SCHEDULER_NAME, STARTUP_ARG_MINIMIZED)


def watchdog_task_needs_repair(paths: PathManager) -> bool:
    return not _task_xml_matches(paths, WATCHDOG_TASK_NAME, "--revert-bios")


def create_watchdog_task(paths: PathManager) -> None:
    """Register the current-install watchdog; called on every primary launch."""
    if os.name != 'nt' or paths.is_running_as_script():
        # A development run must not hijack the installed production watchdog
        # to a temporary source path.
        return
    command, arguments = _watchdog_command(paths)
    final_xml_bytes = build_watchdog_task_xml(command, arguments, paths.base_dir)

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix=".xml", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(final_xml_bytes)
        success, output = _run_schtasks(['/Create', '/TN', WATCHDOG_TASK_NAME, '/XML', temp_path, '/F'])
        if not success:
            raise Exception(tr("task_scheduler_error_msg", command=f"Create {WATCHDOG_TASK_NAME}", error=output))
        # Only remove the legacy task after the replacement exists, so a
        # failed registration never leaves the machine without a watchdog.
        delete_legacy_watchdog_tasks()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def ensure_watchdog_task(paths: PathManager) -> None:
    if os.name != 'nt' or paths.is_running_as_script():
        return
    if watchdog_task_needs_repair(paths):
        create_watchdog_task(paths)
    else:
        delete_legacy_watchdog_tasks()


def delete_legacy_startup_task() -> None:
    """Remove the pre-rebrand task if it exists; failure here is never fatal."""
    if os.name != 'nt':
        return
    _run_schtasks(['/Delete', '/TN', LEGACY_TASK_SCHEDULER_NAME, '/F'])


def create_startup_task(paths: PathManager):
    """Create or update the Windows task for start-on-boot."""
    delete_legacy_startup_task()

    command, arguments = _startup_command(paths)
    final_xml_bytes = build_startup_task_xml(command, arguments, paths.base_dir)

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix=".xml", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(final_xml_bytes)

        success, output = _run_schtasks(['/Create', '/TN', TASK_SCHEDULER_NAME, '/XML', temp_path, '/F'])

        if not success:
            raise Exception(tr("task_scheduler_error_msg", command=f"Create {TASK_SCHEDULER_NAME}", error=output))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def delete_startup_task():
    """Delete the app's startup task from the Windows Task Scheduler."""
    success, output = _run_schtasks(['/Delete', '/TN', TASK_SCHEDULER_NAME, '/F'])
    if not success and "ERROR: The system cannot find the file specified." not in output:
        raise Exception(tr("task_scheduler_error_msg", command=f"Delete {TASK_SCHEDULER_NAME}", error=output))


def is_startup_task_registered() -> bool:
    """Check whether the app's startup task is registered in the Task Scheduler."""
    success, _ = _run_schtasks(['/Query', '/TN', TASK_SCHEDULER_NAME])
    return success
