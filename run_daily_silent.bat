@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  TASI Analyst Consensus Intelligence - Silent daily run
REM  Designed for Windows Task Scheduler.
REM  Never waits for input. Logs every run to logs\daily_YYYY-MM-DD.log
REM ============================================================

cd /d "%~dp0"

REM Ensure the logs folder exists
if not exist "logs" mkdir "logs"

REM One log file per day; append if we run twice in a day
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set TODAY=%%c-%%b-%%a
if "%TODAY%"=="" set TODAY=%DATE%
set LOGFILE=logs\daily_%TODAY%.log

where py >nul 2>nul
if errorlevel 1 (
    echo [%DATE% %TIME%] ERROR: py launcher not found >> "%LOGFILE%"
    exit /b 1
)

set PY=py -3.12

echo. >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
echo Run started: %DATE% %TIME% >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"

echo [1/3] Argaam collection >> "%LOGFILE%"
%PY% run.py collect --source argaam >> "%LOGFILE%" 2>&1
set ARGAAM_EXIT=%errorlevel%

echo [2/3] Price collection >> "%LOGFILE%"
%PY% run.py collect --source prices >> "%LOGFILE%" 2>&1
set PRICES_EXIT=%errorlevel%

echo [3/3] Report generation >> "%LOGFILE%"
%PY% run.py report >> "%LOGFILE%" 2>&1
set REPORT_EXIT=%errorlevel%

echo. >> "%LOGFILE%"
echo Run finished: %DATE% %TIME% >> "%LOGFILE%"
echo Exit codes -- argaam=%ARGAAM_EXIT% prices=%PRICES_EXIT% report=%REPORT_EXIT% >> "%LOGFILE%"

REM Non-zero exit only if the final report failed
if not "%REPORT_EXIT%"=="0" exit /b %REPORT_EXIT%
exit /b 0