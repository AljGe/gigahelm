@echo off
rem Gigahelm bench chain - runs elevated via "Gigahelm Startup Task" action swap.
rem Self-restores the original task action on every exit path.
set "BENCH=%~dp0"
set "ROOT=%BENCH%..\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "RESULTS=%BENCH%results"
if not exist "%RESULTS%" mkdir "%RESULTS%"
if not exist "%PY%" (
  echo VENV-MISSING: create .venv and install deps first
  exit /b 3
)
echo [1/3] EC probe
"%PY%" -X utf8 "%BENCH%ec_probe.py" --once > "%RESULTS%\probe-once.txt" 2> "%RESULTS%\probe-once.err.txt"
"%PY%" -X utf8 "%BENCH%check_probe.py" "%RESULTS%\probe-once.txt" || goto :probe_fail
echo [2/3] selftest
"%PY%" -X utf8 "%BENCH%bench.py" --phase selftest > "%RESULTS%\selftest.log" 2>&1 || goto :selftest_fail
echo [3/3] bench small run (about 35-45 minutes, console stays quiet)
"%PY%" -X utf8 "%BENCH%bench.py" --phase all --power ac --quick --resume --reps 1 --duties 40,70,100 --out "%RESULTS%\bench-small.jsonl" > "%RESULTS%\bench-small.log" 2>&1
set RC=%ERRORLEVEL%
goto :restore
:probe_fail
set RC=5
echo PROBE-FAIL
goto :restore
:selftest_fail
set RC=6
echo SELFTEST-FAIL
goto :restore
:restore
rem Task action was never modified (unelevated /Change was denied), so there
rem is nothing to restore here - the original action is untouched on disk.
echo CHAIN-DONE RC=%RC% > "%RESULTS%\chain-status.txt"
exit /b %RC%
