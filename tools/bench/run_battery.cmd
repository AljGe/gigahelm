@echo off
rem Battery-phase bench. Prompts print on THIS console - watch for them:
rem   1) charge gate (>=90 %, press Enter after charging if needed)
rem   2) "Unplug AC, then press Enter"
rem Runs about 40-50 minutes on battery; plugs nothing back in for you.
set "BENCH=%~dp0"
set "ROOT=%BENCH%..\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "RESULTS=%BENCH%results"
if not exist "%RESULTS%" mkdir "%RESULTS%"
if not exist "%PY%" (
  echo VENV-MISSING: create .venv and install deps first
  exit /b 3
)
echo [battery] charge gate + unplug prompt will appear below.
"%PY%" -X utf8 "%BENCH%bench.py" --phase all --power battery --quick --resume --reps 1 --duties 40,70,100 --out results/bench-battery.jsonl 2> "%RESULTS%\bench-battery.err.log"
set RC=%ERRORLEVEL%
echo BATTERY-DONE RC=%RC% > "%RESULTS%\chain-status3.txt"
exit /b %RC%
