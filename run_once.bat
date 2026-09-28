@echo off
REM ============================================================
REM  run_once.bat - one trading cycle. Called by Task Scheduler.
REM  Edit the two paths below if your layout differs.
REM ============================================================

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

REM Use the venv python if it exists, otherwise fall back to the launcher.
if exist "%PROJECT_DIR%.venv\Scripts\python.exe" (
    "%PROJECT_DIR%.venv\Scripts\python.exe" bot.py >> "%PROJECT_DIR%bot.log" 2>&1
) else (
    py -3 bot.py >> "%PROJECT_DIR%bot.log" 2>&1
)
