# -*- coding: utf-8 -*-
"""
Functions for interacting with the Windows Task Scheduler to manage app startup.
Now relies entirely on canonical paths passed in from PathManager, ensuring the
correct task is created in every mode.
"""
import os
import sys
import subprocess
import tempfile
from typing import Tuple
from config.settings import (
    TASK_SCHEDULER_NAME, BIOS_WATCHDOG_TASK_NAME,
    STARTUP_ARG_MINIMIZED, REVERT_BIOS_ARG,
)
from core.path_manager import PathManager
from .localization import tr

def _run_schtasks(args: list[str]) -> Tuple[bool, str]:
    """Helper that runs schtasks.exe commands and handles output encoding correctly."""
    if os.name != 'nt': return False, "Task Scheduler is only for Windows."
    command = ['schtasks'] + args
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        result = subprocess.run(command, capture_output=True, check=True, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
        
        encoding = (getattr(sys.stdout, 'encoding', None) or ('mbcs' if os.name == 'nt' else 'utf-8'))
        output = result.stdout.decode(encoding, errors='ignore')
        return True, output

    except (FileNotFoundError, subprocess.CalledProcessError, Exception) as e:
        encoding = (getattr(sys.stdout, 'encoding', None) or ('mbcs' if os.name == 'nt' else 'utf-8'))
        error_output_bytes = getattr(e, 'stderr', b'') or getattr(e, 'stdout', b'')
        error_output = error_output_bytes.decode(encoding, errors='ignore') if error_output_bytes else str(e)
        return False, f"schtasks command failed: {error_output.strip()}"

def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _get_default_task_xml_content() -> bytes:
    """Return the task XML as bytes, UTF-16-LE encoded with a BOM prefix."""
    xml_str = r'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the Gigahelm application on user logon and system wake.</Description>
    <Author>Gigahelm</Author>
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
      <LogonType>InteractiveToken</LogonType>
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
    <RestartOnFailure>
      <Count>3</Count>
      <Interval>PT1M</Interval>
    </RestartOnFailure>
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
    return b'\xff\xfe' + xml_str.encode('utf-16-le')

def _get_default_watchdog_task_xml_content() -> bytes:
    """Return the BIOS-revert watchdog task XML (UTF-16-LE with BOM).

    A 1-minute repetition tick: if the app hard-died while owning fan
    control, this bounds the exposure to at most one minute. Hidden, no
    execution time limit concerns (a run takes well under a second).
    """
    xml_str = r'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Safety net: hands fans back to BIOS if Gigahelm owned fan control but was hard-killed (crash, OOM, power loss).</Description>
    <Author>Gigahelm</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2025-01-01T00:00:00</StartBoundary>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
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
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
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
    return b'\xff\xfe' + xml_str.encode('utf-16-le')

def _resolve_command_and_args(paths: PathManager, arguments: str) -> Tuple[str, str]:
    """Command + arguments for a scheduled task, per script/packaged mode."""
    if paths.is_running_as_script():
        # Script mode: python.exe "main.py" <arguments>
        return sys.executable, f'"{paths.main_script_path}" {arguments}'
    # Packaged mode: the .exe itself
    return paths.executable_path, arguments

def _create_task(paths: PathManager, task_name: str, template_path: str,
                 default_xml_fn, arguments: str):
    """Create or update a scheduled task from an XML template file with
    {{COMMAND}}/{{ARGUMENTS}}/{{WORKING_DIRECTORY}} placeholders.

    The template is generated state, not user config: it is rewritten from
    the embedded default on every call so a stale file can never linger
    after a code change."""
    try:
        with open(template_path, 'wb') as f:
            f.write(default_xml_fn())
    except IOError as e:
        raise Exception(f"Failed to write task template: {e}")

    try:
        with open(template_path, 'rb') as f:
            xml_template = f.read().decode('utf-16')
    except IOError as e:
        raise Exception(f"Failed to read task template file: {e}")

    command, resolved_arguments = _resolve_command_and_args(paths, arguments)

    final_xml_content_str = xml_template.replace("{{COMMAND}}", _xml_escape(command))
    final_xml_content_str = final_xml_content_str.replace("{{ARGUMENTS}}", _xml_escape(resolved_arguments))
    final_xml_content_str = final_xml_content_str.replace("{{WORKING_DIRECTORY}}", _xml_escape(paths.base_dir))

    final_xml_bytes = b'\xff\xfe' + final_xml_content_str.encode('utf-16-le')

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix=".xml", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(final_xml_bytes)

        success, output = _run_schtasks(['/Create', '/TN', task_name, '/XML', temp_path, '/F'])

        if not success:
            raise Exception(tr("task_scheduler_error_msg", command=f"Create {task_name}", error=output))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

def _delete_task(task_name: str):
    """Delete a scheduled task; a missing task is not an error."""
    success, output = _run_schtasks(['/Delete', '/TN', task_name, '/F'])
    if not success and "ERROR: The system cannot find the file specified." not in output:
        raise Exception(tr("task_scheduler_error_msg", command=f"Delete {task_name}", error=output))

def _is_task_registered(task_name: str) -> bool:
    """Check whether a scheduled task is registered in the Task Scheduler."""
    success, _ = _run_schtasks(['/Query', '/TN', task_name])
    return success

def create_startup_task(paths: PathManager):
    """Create or update the app's startup task (logon + wake, --minimized)."""
    _create_task(paths, TASK_SCHEDULER_NAME, paths.task_template,
                 _get_default_task_xml_content, STARTUP_ARG_MINIMIZED)

def delete_startup_task():
    """Delete the app's startup task from the Windows Task Scheduler."""
    _delete_task(TASK_SCHEDULER_NAME)

def is_startup_task_registered() -> bool:
    """Check whether the app's startup task is registered in the Task Scheduler."""
    return _is_task_registered(TASK_SCHEDULER_NAME)

def create_bios_watchdog_task(paths: PathManager):
    """Create or update the hidden BIOS-revert watchdog task (1-minute tick, --revert-bios)."""
    _create_task(paths, BIOS_WATCHDOG_TASK_NAME, paths.watchdog_task_template,
                 _get_default_watchdog_task_xml_content, REVERT_BIOS_ARG)

def delete_bios_watchdog_task():
    """Delete the BIOS-revert watchdog task from the Windows Task Scheduler."""
    _delete_task(BIOS_WATCHDOG_TASK_NAME)

def is_bios_watchdog_task_registered() -> bool:
    """Check whether the BIOS-revert watchdog task is registered in the Task Scheduler."""
    return _is_task_registered(BIOS_WATCHDOG_TASK_NAME)