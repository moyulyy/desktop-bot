@echo off
setlocal
cd /d "%~dp0"
title Floating Ball

rem ---- find a working Python (py launcher first, then python) ----
set "PY="
for %%C in ("py -3" "python") do (
    if not defined PY (
        %%~C -c "import sys" >nul 2>nul && set "PY=%%~C"
    )
)
if not defined PY (
    echo [ERROR] Python 3 not found. Please install it from https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ---- install dependencies on first run ----
%PY% -c "import PySide6, requests" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies: PySide6 requests ...
    %PY% -m pip install PySide6 requests
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo Starting Floating Ball silently ... (right-click the ball or the tray icon to quit)
set PYTHONIOENCODING=utf-8
start "" wscript "%~dp0start.vbs"
endlocal
