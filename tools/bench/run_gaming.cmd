@echo off
set PY=C:\Users\REDACTED\AppData\Local\Temp\gfb-center\.venv\Scripts\python.exe
set DIR=C:\Users\REDACTED\AppData\Local\Temp\gfb-center\tools\bench
set INST=C:\Users\REDACTED\AppData\Local\Temp\gfb-center\Unigine_Heaven-4.0.exe

if exist "C:\Program Files (x86)\Unigine\Heaven Benchmark 4.0\bin\heaven.exe" goto :installed
if exist "C:\Program Files\Unigine\Heaven Benchmark 4.0\bin\heaven.exe" goto :installed

echo [gaming] Heaven installer wizard opening - click Next, Install, Finish
start "" /wait "%INST%"
ping -n 11 127.0.0.1 >nul

:installed
if exist "C:\Program Files (x86)\Unigine\Heaven Benchmark 4.0\bin\heaven.exe" set HEAVEN=ok
if exist "C:\Program Files\Unigine\Heaven Benchmark 4.0\bin\heaven.exe" set HEAVEN=ok
if "%HEAVEN%" NEQ "ok" (
  echo HEAVEN-INSTALL-FAILED
  echo GAMING-FAILED install > "%DIR%\results\chain-status6.txt"
  exit /b 9
)
echo [gaming] running experiment (~6 min, fullscreen benchmark + max fans)
%PY% -X utf8 "%DIR%\gaming_bench.py" > "%DIR%\results\gaming-out.log" 2> "%DIR%\results\gaming-err.log"
set RC=%ERRORLEVEL%
echo GAMING-DONE RC=%RC% > "%DIR%\results\chain-status6.txt"
exit /b %RC%
