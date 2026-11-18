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
echo   TASI Analyst Intelligence - Dashboard
echo   Opening at http://localhost:8501
echo   Press Ctrl+C in this window to stop the server.
echo ============================================================
echo.

py -3.12 run.py dashboard

pause