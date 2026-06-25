@echo off
REM ============================================================
REM  Terra - Acquisition Intelligence : local launcher
REM  Double-click to run on this machine (internet on = full UI).
REM  Self-contained: data lives in .\data, seeded on first run.
REM ============================================================
cd /d "%~dp0"
title Terra - Acquisition Intelligence (local)

REM keep all runtime data inside this folder (self-contained)
set "RE_DATA=%~dp0data"

echo Installing/updating Python packages (first run only)...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check

echo.
echo ============================================================
echo   Terra is starting at  http://127.0.0.1:5000
echo   First start takes ~30-60s (it loads 545k scored homes).
echo   Leave this window open. Press Ctrl+C here to stop.
echo ============================================================
echo.

REM open the browser a few seconds AFTER the server has booted
start "" cmd /c "timeout /t 8 >nul & start """" http://127.0.0.1:5000"

REM run the production WSGI server (single worker, threaded)
python -m waitress --port=5000 --threads=8 app:app

echo.
echo Server stopped. Press any key to close.
pause >nul
