@echo off
rem PUBG Tracker launcher for Windows.
rem Usage: start.bat [--host 0.0.0.0] [--port 8000] [--data-dir D:\pubg-storage] [--no-browser]
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (where py >nul 2>nul && set "PY=py -3")
if not defined PY (
    echo Python 3.10+ is required: https://www.python.org/downloads/
    pause
    exit /b 1
)

rem First run without dependencies: install them
%PY% -c "import requests" >nul 2>nul || %PY% -m pip install -r requirements.txt

%PY% app.py %*
if errorlevel 1 pause
