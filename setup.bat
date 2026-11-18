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
echo   TASI Analyst Intelligence - First-time setup
echo ============================================================
echo.

echo [1/3] Installing Python packages (requirements.txt)...
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo.

echo [2/3] Downloading Playwright Chromium (~150 MB, one time)...
py -3.12 -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright install failed. The Argaam and Euroland
    echo        collectors need Playwright. Price and PDF collectors
    echo        will still work.
)
echo.

echo [3/3] Initialising database...
py -3.12 run.py init
echo.

echo ============================================================
echo   Setup complete.
echo   Next: double-click run_daily.bat to start the pipeline.
echo ============================================================
echo.
pause