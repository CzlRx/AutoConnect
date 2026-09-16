@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and check "Add python.exe to PATH".
    pause
    exit /b 1
)
python autoconnect.py %*
if errorlevel 1 pause
