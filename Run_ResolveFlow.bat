@echo off
title ResolveFlow Assistant v4.1
cd /d "%~dp0"

echo =======================================================
echo    Khoi dong ResolveFlow Assistant v4.1...
echo =======================================================

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py
) else (
    python main.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Gap loi khi khoi chay! Vui long chay setup_project.ps1 de cai dat moi truong.
    pause
)
