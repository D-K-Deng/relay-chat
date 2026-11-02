@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 app.py %*
    if errorlevel 1 pause
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python app.py %*
    if errorlevel 1 pause
    exit /b
)
echo Install Python 3.10+ from https://www.python.org/downloads/ or use the portable Windows release.
pause
