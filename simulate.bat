@echo off
REM Test version: paper-trade a simulated market. No OANDA account needed.
REM Extra options are passed through, e.g.:  simulate.bat --seed 7 --capital 100
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" simulate.py %*
) else (
    py -3 simulate.py %*
)
