@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and check "Add python.exe to PATH".
    pause
    exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Install failed.
    pause
    exit /b 1
)
echo Install finished. Double-click 启动.bat to open AutoConnect.
pause
