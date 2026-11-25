@echo off
title KI Trading-Cockpit Starter
cd /d "%~dp0"

set HF_HUB_DISABLE_SYMLINKS_WARNING=1

if not exist "venv\Scripts\python.exe" (
    echo [FEHLER] venv nicht gefunden in %~dp0
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"
echo Starte KI Trading-Cockpit auf Monitor 2...
python step3_cockpit.py

if errorlevel 1 (
    echo.
    echo [HINWEIS] Das Cockpit wurde mit einem Fehler beendet.
    pause
)