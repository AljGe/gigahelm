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
from config.settings import TASK_SCHEDULER_NAME, STARTUP_ARG_MINIMIZED
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
    <Description>Starts the FanBatteryControl application on user logon and system wake.</Description>
    <Author>FanBatteryControl</Author>
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

def create_startup_task(paths: PathManager):
    """
    Create or update the Windows task using the external XML template and
    canonical paths from PathManager.
    """
    xml_path = paths.task_template

    if not os.path.exists(xml_path):
        try:
            default_xml_bytes = _get_default_task_xml_content()
            with open(xml_path, 'wb') as f:
                f.write(default_xml_bytes)
            print(f"Default task template created at: {xml_path}")
        except IOError as e:
            raise Exception(f"Failed to create default task template: {e}")

    try:
        with open(xml_path, 'rb') as f:
            xml_template_bytes = f.read()
        xml_template = xml_template_bytes.decode('utf-16')
    except IOError as e:
        raise Exception(f"Failed to read task template file: {e}")

    # Get all path info from PathManager instead of guessing
    if paths.is_running_as_script():
        # Script mode
        command = sys.executable # python.exe
        arguments = f'"{paths.main_script_path}" {STARTUP_ARG_MINIMIZED}'
    else:
        # Packaged mode
        command = paths.executable_path # main.exe
        arguments = STARTUP_ARG_MINIMIZED
        
    working_dir = paths.base_dir

    final_xml_content_str = xml_template.replace("{{COMMAND}}", _xml_escape(command))
    final_xml_content_str = final_xml_content_str.replace("{{ARGUMENTS}}", _xml_escape(arguments))
    final_xml_content_str = final_xml_content_str.replace("{{WORKING_DIRECTORY}}", _xml_escape(working_dir))
    
    final_xml_bytes = b'\xff\xfe' + final_xml_content_str.encode('utf-16-le')

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