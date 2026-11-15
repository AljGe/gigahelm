@echo off
echo AC re-run on fixed build: ladder 40/70/100 x idle/dual/all + profiles + validate.
C:\Users\geier\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe -X utf8 C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\bench.py --phase all --power ac --quick --resume --reps 1 --duties 40,70,100 --out results/bench-ac2.jsonl 2> C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\bench-ac2.err.log
set RC=%ERRORLEVEL%
echo AC2-DONE RC=%RC% > C:\Users\geier\AppData\Local\Temp\gfb-center\tools\bench\results\chain-status4.txt
exit /b %RC%
