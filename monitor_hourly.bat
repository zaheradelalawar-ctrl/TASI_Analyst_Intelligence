@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  TASI Monitor - hourly watchlist check
REM  Writes all output to logs\monitor_YYYY-MM-DD.log
REM  Never blocks on input. Safe for Task Scheduler.
REM ============================================================

REM Use UTF-8 codepage so any unicode in output does not crash
chcp 65001 >nul

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

for /f "tokens=2 delims==" %%i in ('wmic os get localdatetime /value') do set DT=%%i
set TODAY=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%
set LOGFILE=logs\monitor_%TODAY%.log

set TASI_NO_OPEN=1
set PYTHONIOENCODING=utf-8

REM --- Locate Python ------------------------------------------
set PY=
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if "%PY%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 set PY=py -3.12
)
if "%PY%"=="" (
    where python >nul 2>nul
    if not errorlevel 1 set PY=python
)
if "%PY%"=="" (
    echo [%DATE% %TIME%] ERROR: Python not found >> "%LOGFILE%"
    exit /b 9009
)

echo. >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
echo Run started: %DATE% %TIME% ^(python: %PY%^) >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"

echo [1/3] Collecting Argaam recommendations... >> "%LOGFILE%"
%PY% run.py collect --source argaam >> "%LOGFILE%" 2>&1
set ARGAAM_EXIT=%errorlevel%
echo Argaam exit: %ARGAAM_EXIT% >> "%LOGFILE%"

echo [2/3] Collecting price history... >> "%LOGFILE%"
%PY% run.py collect --source prices >> "%LOGFILE%" 2>&1
set PRICES_EXIT=%errorlevel%
echo Prices exit: %PRICES_EXIT% >> "%LOGFILE%"

echo [3/3] Running monitor... >> "%LOGFILE%"
%PY% run.py monitor >> "%LOGFILE%" 2>&1
set MONITOR_EXIT=%errorlevel%
echo Monitor exit: %MONITOR_EXIT% >> "%LOGFILE%"

echo. >> "%LOGFILE%"
echo Run finished: %DATE% %TIME% >> "%LOGFILE%"
echo Exit codes -- argaam=%ARGAAM_EXIT% prices=%PRICES_EXIT% monitor=%MONITOR_EXIT% >> "%LOGFILE%"

REM Heartbeat: one line per run so you can prove the task is alive
echo [%DATE% %TIME%] argaam=%ARGAAM_EXIT% prices=%PRICES_EXIT% monitor=%MONITOR_EXIT% >> logs\monitor_heartbeat.log

if not "%MONITOR_EXIT%"=="0" exit /b %MONITOR_EXIT%
exit /b 0