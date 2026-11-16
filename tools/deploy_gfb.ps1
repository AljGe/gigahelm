# Requires elevation. Hands fans back to BIOS, replaces C:\Tools\Gigahelm, re-registers the startup + BIOS-revert-watchdog tasks.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$taskName = "Gigahelm Startup Task"
$install = "C:\Tools\Gigahelm"
$exeName = "Gigahelm.exe"
$sourceRoot = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $sourceRoot "dist\Gigahelm"
$python = Join-Path $sourceRoot ".venv\Scripts\python.exe"
$log = Join-Path $env:TEMP "gigahelm-deploy.log"

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Host $line
}

function Escape-XmlText([string]$Value) {
    if ($null -eq $Value) { return "" }
    return ($Value -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;')
}

Set-Content -Path $log -Value "deploy-start" -Encoding utf8

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Log "not-elevated"
    exit 2
}

if (-not (Test-Path (Join-Path $dist $exeName))) {
    Write-Log "build-missing"
    exit 3
}
if (-not (Test-Path $python)) {
    Write-Log "venv-python-missing"
    exit 3
}

Write-Log "bios-handback"
& $python -c @"
import wmi
conn = wmi.WMI(namespace=r'root\WMI')
setter = conn.query('SELECT * FROM GB_WMIACPI_Set')[0]
setter.SetAutoFanStatus(Data=1.0)
setter.SetFixedFanStatus(Data=0.0)
print('handback-ok')
"@
if ($LASTEXITCODE -ne 0) { Write-Log "handback-failed"; exit 4 }

$userId = ""
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $userId = [string]$existing.Principal.UserId
    Write-Log "task-user=$userId"
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
} else {
    Write-Log "task-missing"
}

$needle = Join-Path $install $exeName
$procs = @(Get-CimInstance Win32_Process -Filter "Name = '$exeName'" -ErrorAction SilentlyContinue)
foreach ($proc in $procs) {
    $path = [string]$proc.ExecutablePath
    $cmd = [string]$proc.CommandLine
    $ours = (($path -like "*$needle*") -or ($cmd -like "*$needle*"))
    if ($ours) {
        Write-Log "kill-pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        continue
    }
    if (-not $path) {
        # Teardown race: a just-stopped instance lingers briefly in Win32_Process
        # with NULL path/commandline. Re-query before declaring it unproven.
        $resolved = $false
        for ($try = 0; $try -lt 10; $try++) {
            Start-Sleep -Milliseconds 500
            $again = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.ProcessId)" -ErrorAction SilentlyContinue
            if (-not $again) { $resolved = $true; break }
            $p2 = [string]$again.ExecutablePath
            $c2 = [string]$again.CommandLine
            if (($p2 -like "*$needle*") -or ($c2 -like "*$needle*")) {
                Write-Log "kill-pid=$($proc.ProcessId)"
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                $resolved = $true; break
            }
            if ($p2) { Write-Log "unproven-main-exe pid=$($proc.ProcessId) path=$p2"; exit 5 }
        }
        if (-not $resolved) { Write-Log "unproven-main-exe pid=$($proc.ProcessId)"; exit 5 }
    }
}
Start-Sleep -Seconds 1

$configPath = Join-Path $install "control_config.json"
$configBefore = ""
if (Test-Path $configPath) {
    $configBefore = (Get-FileHash -Algorithm SHA256 -Path $configPath).Hash
}
Write-Log "copy-dist"
& robocopy $dist $install /E /XF control_config.json last_mode.state /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
    Write-Log "robocopy-failed=$LASTEXITCODE"
    exit 6
}
if ($configBefore -ne "") {
    $configAfter = (Get-FileHash -Algorithm SHA256 -Path $configPath).Hash
    if ($configBefore -ne $configAfter) {
        Write-Log "config-changed"
        exit 7
    }
    Write-Log "config-unchanged"
}

Write-Log "write-task-template"
& $python -c "import sys; sys.path.insert(0, r'$sourceRoot'); from tools.task_scheduler import _get_default_task_xml_content; open(r'$install\task_template.xml','wb').write(_get_default_task_xml_content())"
if ($LASTEXITCODE -ne 0) { Write-Log "template-write-failed"; exit 8 }

Write-Log "acl"
& icacls $install /inheritance:r
if ($LASTEXITCODE -ne 0) { Write-Log "icacls-inheritance-failed"; exit 9 }
& icacls $install /grant "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX"
if ($LASTEXITCODE -ne 0) { Write-Log "icacls-grant-failed"; exit 9 }

$userXml = ""
if ($userId) {
    $userXml = "      <UserId>$(Escape-XmlText $userId)</UserId>`r`n"
}
$filled = @"
<?xml version="1.0" encoding="UTF-16"?>
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
$userXml      <LogonType>InteractiveToken</LogonType>
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
      <Command>C:\Tools\Gigahelm\Gigahelm.exe</Command>
      <Arguments>--minimized</Arguments>
      <WorkingDirectory>C:\Tools\Gigahelm</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$filledPath = Join-Path $env:TEMP "gfb-startup-task.xml"
$utf16 = New-Object System.Text.UnicodeEncoding $false, $true
[System.IO.File]::WriteAllText($filledPath, $filled, $utf16)

Write-Log "register-task"
& schtasks /Create /TN "$taskName" /XML "$filledPath" /F
if ($LASTEXITCODE -ne 0) { Write-Log "schtasks-failed"; exit 10 }

# BIOS-revert watchdog: register through the app's own scheduler code so the
# task always matches what a running app would self-heal to (1-minute tick).
Write-Log "register-watchdog-task"
# EAP=Continue while capturing: under Windows PowerShell 5.1, 2>&1 with
# EAP=Stop promotes any stderr line to a terminating error.
$ErrorActionPreference = "Continue"
$wdOut = & $python -c "import sys; sys.path.insert(0, r'$sourceRoot'); from core.path_manager import PathManager; from tools.task_scheduler import create_bios_watchdog_task; create_bios_watchdog_task(PathManager(base_dir=r'$install', executable_path=r'$install\$exeName', main_script_path=''))" 2>&1
$wdExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($wdExit -ne 0) { Write-Log "watchdog-register-failed: $($wdOut -join ' ')"; exit 12 }

Write-Log "start-task"
Start-ScheduledTask -TaskName $taskName
# Deploy race: the previous instance may still be dying and holding the
# single-instance mutex, making the new instance exit silently. Poll for the
# mutex; if it never appears, retry the task start once before failing.
& $python -c @"
import sys, time
sys.path.insert(0, r'$sourceRoot')
from tools.bios_watchdog import app_instance_running
deadline = time.time() + 20
while time.time() < deadline:
    if app_instance_running():
        sys.exit(0)
    time.sleep(1)
sys.exit(1)
"@
if ($LASTEXITCODE -ne 0) {
    Write-Log "start-retry"
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 8
    & $python -c @"
import sys
sys.path.insert(0, r'$sourceRoot')
from tools.bios_watchdog import app_instance_running
sys.exit(0 if app_instance_running() else 1)
"@
    if ($LASTEXITCODE -ne 0) { Write-Log "start-verify-failed"; exit 11 }
}
# The app needs a few seconds after the mutex appears to initialize WMI and
# write its first fan state; probing earlier races it and reads mid-transition.
Start-Sleep -Seconds 8

Write-Log "probe"
& $python -c @"
import sys
sys.path.insert(0, r'$sourceRoot')
import wmi
from core.fan_duty import percent_to_raw

def call(obj, name, **kwargs):
    result = getattr(obj, name)(**kwargs)
    if isinstance(result, tuple) and result:
        return result[0]
    return result

def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value

conn = wmi.WMI(namespace=r'root\WMI')
getter = conn.query('SELECT * FROM GB_WMIACPI_Get')[0]
setter = conn.query('SELECT * FROM GB_WMIACPI_Set')[0]
names = [
    'getCpuTemp', 'getGpuTemp1', 'getGpuTemp2', 'getRpm1', 'getRpm2',
    'GetFixedFanStatus', 'GetAutoFanStatus', 'GetStepFanStatus',
    'GetFixedFanSpeed', 'GetChargePolicy', 'GetChargeStop',
]
values = {}
for name in names:
    try:
        values[name] = call(getter, name)
    except Exception as exc:
        values[name] = 'ERR:%s' % exc
for name in names:
    print('%s=%s' % (name, values[name]))

def num(name):
    try:
        return float(values[name])
    except (TypeError, ValueError):
        return None

temps = [num('getCpuTemp'), num('getGpuTemp1'), num('getGpuTemp2')]
rpms = [num('getRpm1'), num('getRpm2')]
hot = any(t is not None and t >= 90 for t in temps)
stalled = all(r == 0 for r in rpms)
if hot and stalled:
    raw = float(percent_to_raw(80))
    setter.SetFixedFanStatus(Data=1.0)
    setter.SetSuperQuiet(Data=0.0)
    setter.SetAutoFanStatus(Data=0.0)
    setter.SetStepFanStatus(Data=0.0)
    setter.SetFixedFanSpeed(Data=raw)
    setter.SetGPUFanDuty(Data=raw)
    print('EMERGENCY')
    sys.exit(0)

fixed = num('GetFixedFanStatus')
auto = num('GetAutoFanStatus')
step = num('GetStepFanStatus')
speed = num('GetFixedFanSpeed')
latched = fixed == 1 and auto == 0 and step == 0
raw_ok = speed == 0 or (speed is not None and 57 <= speed <= 227)
if speed == 229 or (speed is not None and 1 <= speed <= 56) or not latched or not raw_ok:
    print('FAIL')
    sys.exit(1)
print('PASS')
"@
$probe = $LASTEXITCODE
Write-Log "probe-exit=$probe"
exit $probe
