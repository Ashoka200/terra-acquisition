@echo off
REM ============================================================
REM  Terra - Acquisition Intelligence : local launcher
REM  Double-click to run. Opens http://127.0.0.1:5000
REM ============================================================
cd /d "%~dp0"
title Terra - Acquisition Intelligence (local)

REM keep all runtime data inside this folder (self-contained)
set "RE_DATA=%~dp0data"

REM --- OPTIONAL: paste keys between the quotes to unlock extra features ---
REM Live for-sale listings on the map + zip download (free tier at rentcast.io/api):
set "RENTCAST_API_KEY="
REM Full ATLAS chat (otherwise ATLAS runs in offline rule-based mode):
set "ANTHROPIC_API_KEY="

echo ============================================================
echo   Terra - Acquisition Intelligence
echo ============================================================
echo Installing Python packages (first run can take a few minutes)...
echo.
python -m pip install -r requirements.txt --disable-pip-version-check
python -m pip install waitress --disable-pip-version-check

echo.
echo ============================================================
echo   Terra is RUNNING at:    http://127.0.0.1:5000
echo.
echo   * A browser tab opens automatically in ~10 seconds.
echo   * If it doesn't, just open that address yourself.
echo   * This window looks idle while the server runs - that's NORMAL.
echo   * First load takes 30-60s (loading 545k homes). Refresh if blank.
echo   * Press Ctrl+C here to stop the server.
echo ============================================================
echo.

REM open the browser ~10s later, in the background (won't block the server)
start "" powershell -WindowStyle Hidden -Command "Start-Sleep 10; Start-Process 'http://127.0.0.1:5000'"

REM run the production server (foreground; prints nothing while healthy)
python -m waitress --port=5000 --threads=8 app:app

echo.
echo Server stopped. Press any key to close this window.
pause >nul
