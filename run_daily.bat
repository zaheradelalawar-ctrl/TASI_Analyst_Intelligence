@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  TASI Analyst Consensus Intelligence - Daily Pipeline
REM ------------------------------------------------------------
REM  Runs, in order:
REM    1. Collect recommendations from Argaam
REM    2. Collect price history from Yahoo Finance
REM    3. Build HTML / MD / XLSX report (Excel opens automatically)
REM
REM  Double-click this file to run everything in one go.
REM ============================================================

REM Always work from the folder that contains this .bat
cd /d "%~dp0"

REM --- Locate Python 3.12 -----------------------------------------
where py >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Python launcher 'py' not found.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo Make sure "Add Python to PATH" is checked during install.
    pause
    exit /b 1
)

set PY=py -3.12

REM --- Verify we are in the right project -------------------------
%PY% -c "import run" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Cannot import run.py.
    echo Current folder: %CD%
    echo Make sure this .bat is inside F:\TASI_Analyst_Intelligence
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   TASI Analyst Intelligence - Daily Run
echo   %DATE%  %TIME%
echo ============================================================
echo.

echo [1/3] Collecting Argaam recommendations...
echo ------------------------------------------------------------
%PY% run.py collect --source argaam
if errorlevel 1 (
    echo.
    echo [WARN] Argaam collection returned errors (see above).
    echo        Continuing to prices and report.
)
echo.

echo [2/3] Collecting price history from Yahoo Finance...
echo ------------------------------------------------------------
%PY% run.py collect --source prices
if errorlevel 1 (
    echo.
    echo [WARN] Price collection returned errors (see above).
    echo        Continuing to report.
)
echo.

echo [3/3] Building report...
echo ------------------------------------------------------------
%PY% run.py report
if errorlevel 1 (
    echo.
    echo [ERROR] Report generation failed. See traceback above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done. Reports saved to the output\ folder.
echo   The newest .xlsx was opened in Excel automatically.
echo ============================================================
echo.
pause