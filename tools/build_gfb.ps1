$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv .venv }
& $py -m pip install --upgrade pip
& $py -m pip install pyinstaller PySide6 wmi pywin32
$env:QT_QPA_PLATFORM = "offscreen"
& $py -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { Write-Error "unit tests failed"; exit 1 }
& $py -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin --name Gigahelm --icon app_icon.ico --hidden-import wmi --hidden-import pythoncom --hidden-import win32api main.py
if ($LASTEXITCODE -ne 0) { exit 1 }
New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\Gigahelm\gui") | Out-Null
Copy-Item (Join-Path $root "gui\style.qss") (Join-Path $root "dist\Gigahelm\gui\style.qss") -Force
Write-Output "BUILD_OK"
