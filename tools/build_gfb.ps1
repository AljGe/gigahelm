$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv .venv }
& $py -m pip install --upgrade pip
& $py -m pip install pyinstaller PySide6 wmi pywin32 nvidia-ml-py
$env:QT_QPA_PLATFORM = "offscreen"
& $py -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { Write-Error "unit tests failed"; exit 1 }
& $py tools/make_icon.py
if ($LASTEXITCODE -ne 0) { Write-Error "icon generation failed"; exit 1 }
& $py -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin --name Gigahelm --icon app_icon.ico --hidden-import wmi --hidden-import pythoncom --hidden-import win32api main.py
if ($LASTEXITCODE -ne 0) { exit 1 }
Copy-Item (Join-Path $root "app_icon.ico") (Join-Path $root "dist\Gigahelm\app_icon.ico") -Force
if (-not (Test-Path (Join-Path $root "dist\Gigahelm\app_icon.ico"))) { Write-Error "app_icon.ico missing from dist"; exit 1 }
Write-Output "BUILD_OK"
