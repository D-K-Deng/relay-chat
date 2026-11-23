@echo off
setlocal
cd /d "%~dp0.."
if exist "build\qt-desktop\Scripts\python.exe" (
    "build\qt-desktop\Scripts\python.exe" "scripts\windows_update.py" --launch
) else (
    py -3 "scripts\windows_update.py" --launch
)
if errorlevel 1 pause
