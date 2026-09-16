@echo off
cd /d "%~dp0"
python -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo Failed to install build dependencies.
    pause
    exit /b 1
)
python -m PyInstaller --noconfirm --clean AutoConnect.spec
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo.
echo Built: dist\AutoConnect.exe
pause
