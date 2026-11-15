# Requires elevation. Hands fans back to BIOS, replaces C:\Tools\Gigahelm,
# migrates the old install's config/state and re-registers the startup task.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$taskName = "Gigahelm Startup Task"
$legacyTaskName = "FanBatteryControl Startup Task"
$install = "C:\Tools\Gigahelm"
$legacyInstall = "C:\Tools\GFBC"
$sourceRoot = "C:\Users\geier\AppData\Local\Temp\gfb-center"
$dist = Join-Path $sourceRoot "dist\Gigahelm"
$exeName = "Gigahelm.exe"
$python = Join-Path $sourceRoot ".venv\Scripts\python.exe"
$log = Join-Path $env:TEMP "gigahelm-deploy.log"

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Host $line
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
if (-not (Test-Path (Join-Path $dist "app_icon.ico"))) {
    Write-Log "build-icon-missing"
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
if (-not $existing) {
    $existing = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
}
if ($existing) {
    $userId = [string]$existing.Principal.UserId
    Write-Log "task-user=$userId"
    Stop-ScheduledTask -TaskName $existing.TaskName -ErrorAction SilentlyContinue
} else {
    Write-Log "task-missing"
}

$ourPaths = @($install, $legacyInstall, $sourceRoot)
$procs = @(Get-CimInstance Win32_Process -Filter "Name = 'Gigahelm.exe' OR Name = 'main.exe'" -ErrorAction SilentlyContinue)
foreach ($proc in $procs) {
    $path = [string]$proc.ExecutablePath
    $cmd = [string]$proc.CommandLine
    $ours = $false
    foreach ($ourPath in $ourPaths) {
        if (($path -like "*$ourPath*") -or ($cmd -like "*$ourPath*")) { $ours = $true }
    }
    if ($ours) {
        Write-Log "kill-pid=$($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        continue
    }
    if (-not $path) {
        # Stale or protected entry: no readable path, so nothing to prove and nothing to kill.
        Write-Log "unproven-process pid=$($proc.ProcessId) (skipped)"
        continue
    }
    Write-Log "foreign-process pid=$($proc.ProcessId) path=$path"
    exit 5
}
Start-Sleep -Seconds 1

New-Item -ItemType Directory -Force -Path $install | Out-Null

# Keep the user's profiles and crash-recovery state when moving from C:\Tools\GFBC.
$configPath = Join-Path $install "control_config.json"
$legacyConfigPath = Join-Path $legacyInstall "control_config.json"
if ((Test-Path $legacyConfigPath) -and -not (Test-Path $configPath)) {
    Copy-Item $legacyConfigPath $configPath -Force
    Write-Log "config-migrated"
}
$statePath = Join-Path $install "last_mode.state"
$legacyStatePath = Join-Path $legacyInstall "last_mode.state"
if ((Test-Path $legacyStatePath) -and -not (Test-Path $statePath)) {
    Copy-Item $legacyStatePath $statePath -Force
    Write-Log "state-migrated"
}

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

Write-Log "acl"
& icacls $install /inheritance:r
if ($LASTEXITCODE -ne 0) { Write-Log "icacls-inheritance-failed"; exit 9 }
& icacls $install /grant "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX"
if ($LASTEXITCODE -ne 0) { Write-Log "icacls-grant-failed"; exit 9 }

Write-Log "write-task-xml"
$filledPath = Join-Path $env:TEMP "gigahelm-startup-task.xml"
& $python -c "import sys; sys.path.insert(0, r'$sourceRoot'); from tools.task_scheduler import build_startup_task_xml; open(r'$filledPath','wb').write(build_startup_task_xml(command=r'$install\$exeName', arguments='--minimized', working_directory=r'$install', user_id=r'$userId'))"
if ($LASTEXITCODE -ne 0) { Write-Log "task-xml-failed"; exit 8 }

Write-Log "delete-legacy-task"
& schtasks /Delete /TN "$legacyTaskName" /F | Out-Null

Write-Log "register-task"
& schtasks /Create /TN "$taskName" /XML "$filledPath" /F
if ($LASTEXITCODE -ne 0) { Write-Log "schtasks-failed"; exit 10 }

Write-Log "start-task"
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8

Write-Log "probe"
& $python -c @"
import os
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

mode_path = os.path.join(r'$install', 'last_mode.state')
try:
    with open(mode_path, encoding='utf-8') as handle:
        mode = handle.read().strip()
except OSError:
    mode = ''

if mode == 'bios':
    if auto == 1 and fixed == 0:
        print('PASS-BIOS')
        sys.exit(0)
    print('FAIL-BIOS fixed=%s auto=%s step=%s' % (fixed, auto, step))
    sys.exit(1)

if mode == 'emergency':
    raw = float(percent_to_raw(80))
    if fixed == 1 and auto == 0 and speed == raw:
        print('PASS-EMERGENCY')
        sys.exit(0)
    print('FAIL-EMERGENCY fixed=%s auto=%s speed=%s' % (fixed, auto, speed))
    sys.exit(1)

latched = fixed == 1 and auto == 0 and step == 0
raw_ok = speed == 0 or (speed is not None and 57 <= speed <= 227)
if speed == 229 or (speed is not None and 1 <= speed <= 56) or not latched or not raw_ok:
    print('FAIL-SOFTWARE mode=%s fixed=%s auto=%s step=%s speed=%s' % (mode, fixed, auto, step, speed))
    sys.exit(1)
print('PASS-SOFTWARE mode=%s speed=%s' % (mode, speed))
"@
$probe = $LASTEXITCODE
Write-Log "probe-exit=$probe"
exit $probe
