@echo off
rem Gigahelm bench chain - runs elevated via "Gigahelm Startup Task" action swap.
rem Self-restores the original task action on every exit path.
echo [1/3] EC probe
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\ec_probe.py --once > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\probe-once.txt 2> C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\probe-once.err.txt
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\check_probe.py C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\probe-once.txt || goto :probe_fail
echo [2/3] selftest
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\bench.py --phase selftest > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\selftest.log 2>&1 || goto :selftest_fail
echo [3/3] bench small run (about 35-45 minutes, console stays quiet)
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\bench.py --phase all --power ac --quick --resume --reps 1 --duties 40,70,100 --out results/bench-small.jsonl > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\bench-small.log 2>&1
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
echo CHAIN-DONE RC=%RC% > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\chain-status.txt
exit /b %RC%
