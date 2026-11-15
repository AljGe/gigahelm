@echo off
rem Battery-phase bench. Prompts print on THIS console - watch for them:
rem   1) charge gate (>=90 %, press Enter after charging if needed)
rem   2) "Unplug AC, then press Enter"
rem Runs about 40-50 minutes on battery; plugs nothing back in for you.
echo [battery] charge gate + unplug prompt will appear below.
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\bench.py --phase all --power battery --quick --resume --reps 1 --duties 40,70,100 --out results/bench-battery.jsonl 2> C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\bench-battery.err.log
set RC=%ERRORLEVEL%
echo BATTERY-DONE RC=%RC% > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\chain-status3.txt
exit /b %RC%
