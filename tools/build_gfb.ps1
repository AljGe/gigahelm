$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv .venv }
& $py -m pip install --upgrade pip
& $py -m pip install pyinstaller PySide6 wmi pywin32
& $py -m unittest tests.test_fan_duty tests.test_fan_ramp
if ($LASTEXITCODE -ne 0) { Write-Error "unit tests failed"; exit 1 }
& $py -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin --name main --icon app_icon.ico --hidden-import wmi --hidden-import pythoncom --hidden-import win32api main.py
if ($LASTEXITCODE -ne 0) { exit 1 }
New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\main\gui") | Out-Null
Copy-Item (Join-Path $root "gui\style.qss") (Join-Path $root "dist\main\gui\style.qss") -Force
Write-Output "BUILD_OK"
