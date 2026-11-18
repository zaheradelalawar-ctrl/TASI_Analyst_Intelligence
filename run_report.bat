@echo off
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python launcher 'py' not found.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TASI Analyst Intelligence - Report Only
echo   %DATE%  %TIME%
echo ============================================================
echo.

py -3.12 run.py report

echo.
pause