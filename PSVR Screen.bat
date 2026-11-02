@echo off
rem Starts PSVR Screen. Turn on the headset and start SteamVR first (it also waits for SteamVR).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" -m psvrscreen
