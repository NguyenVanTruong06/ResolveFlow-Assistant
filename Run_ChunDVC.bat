@echo off
title ResolveFlow-Assistant v1.0.0
cd /d "%~dp0"

echo =======================================================
echo    ResolveFlow-Assistant v1.0.0 (Windows 11 Edition)   
echo =======================================================

set PYTHONPATH=%~dp0;%PYTHONPATH%

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m src.main %*
) else if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -m src.main %*
) else (
    python -m src.main %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Gap loi khi khoi chay ResolveFlow-Assistant!
    echo Vui long kiem tra moi truong Python / virtualenv (.venv).
    pause
)
