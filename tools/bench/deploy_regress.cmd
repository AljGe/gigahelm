@echo off
rem Deploy the freshly built Gigahelm + run the Gaming regression experiment.
echo [1/3] stopping app
taskkill /F /IM Gigahelm.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo [2/3] deploying build
robocopy "C:\Users\geier\AppData\Local\Temp\gfb-center\dist\Gigahelm" "C:\Tools\Gigahelm" /E /NFL /NDL /NJH /NJS /NP
if %ERRORLEVEL% GEQ 8 (
  echo DEPLOY-FAIL robocopy=%ERRORLEVEL%
  exit /b 8
)
schtasks /Run /TN "Gigahelm Startup Task" >nul 2>&1
ping -n 14 127.0.0.1 >nul
echo [3/3] regression experiment (about 5 minutes)
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\experiment_gaming.py > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\experiment-gaming-fixed.log 2>&1
set RC=%ERRORLEVEL%
echo CHAIN-DONE RC=%RC% > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\chain-status2.txt
exit /b %RC%
