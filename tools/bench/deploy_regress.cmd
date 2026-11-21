@echo off
rem Deploy the freshly built Gigahelm + run the Gaming regression experiment.
set "BENCH=%~dp0"
set "ROOT=%BENCH%..\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "RESULTS=%BENCH%results"
if not exist "%RESULTS%" mkdir "%RESULTS%"
if not exist "%PY%" (
  echo VENV-MISSING: create .venv and install deps first
  exit /b 3
)
if "%GIGAHELM_HOME%"=="" set "GIGAHELM_HOME=C:\Tools\Gigahelm"
echo [1/3] stopping app
taskkill /F /IM Gigahelm.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo [2/3] deploying build
robocopy "%ROOT%\dist\Gigahelm" "%GIGAHELM_HOME%" /E /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 (
  echo DEPLOY-FAIL robocopy=%ERRORLEVEL%
  exit /b 8
)
schtasks /Run /TN "Gigahelm Startup Task" >nul 2>&1
ping -n 14 127.0.0.1 >nul
echo [3/3] regression experiment (about 5 minutes)
"%PY%" -X utf8 "%BENCH%experiment_gaming.py" > "%RESULTS%\experiment-gaming-fixed.log" 2>&1
set RC=%ERRORLEVEL%
echo CHAIN-DONE RC=%RC% > "%RESULTS%\chain-status2.txt"
exit /b %RC%
