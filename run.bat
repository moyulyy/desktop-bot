@echo off
setlocal
cd /d "%~dp0"
title Floating Ball

rem ---- locate Python: prefer the prepared conda env (desktop-rag) ----
set "PY="
for %%P in (
  "%USERPROFILE%\.conda\envs\desktop-rag\python.exe"
  "%USERPROFILE%\miniconda3\envs\desktop-rag\python.exe"
  "%USERPROFILE%\anaconda3\envs\desktop-rag\python.exe"
  "%LOCALAPPDATA%\miniconda3\envs\desktop-rag\python.exe"
  "%LOCALAPPDATA%\Continuum\anaconda3\envs\desktop-rag\python.exe"
  "C:\ProgramData\miniconda3\envs\desktop-rag\python.exe"
  "C:\ProgramData\anaconda3\envs\desktop-rag\python.exe"
  "D:\miniconda3\envs\desktop-rag\python.exe"
  "D:\anaconda3\envs\desktop-rag\python.exe"
) do (
  if not defined PY if exist "%%~P" set "PY=%%~P"
)
if not defined PY (
  for /f "delims=" %%I in ('conda info --base 2^>nul') do (
    if not defined PY if exist "%%I\envs\desktop-rag\python.exe" set "PY=%%I\envs\desktop-rag\python.exe"
  )
)
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 or the conda env "desktop-rag" was not found.
  echo         Create it once: conda create -y -n desktop-rag python=3.11
  echo         or install Python 3.10+ from https://www.python.org/downloads/
  pause
  exit /b 1
)

rem Keep the app self-contained: ignore the user-global site-packages.
set "PYTHONNOUSERSITE=1"

rem ---- install dependencies on first run ----
"%PY%" -c "import PySide6, requests" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies: PySide6 requests ...
  "%PY%" -m pip install -r requirements.txt
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
