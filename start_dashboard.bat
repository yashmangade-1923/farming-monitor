@echo off
title Smart Irrigation Dashboard Launcher
cd /d "%~dp0"

echo ===============================================================================
echo        🌱 SMART IRRIGATION SYSTEM - LIVING GARDEN LAUNCHER 🌱
echo ===============================================================================
echo.

:: Priority 1: Check for Node.js (Installed on this system: v24.22.0)
where node >nul 2>nul
if %errorlevel% equ 0 (
    echo [1/2] Node.js detected!
    echo [2/2] Launching local dashboard server on http://localhost:5000...
    echo.
    echo Press Ctrl+C in this window at any time to stop the server.
    echo ===============================================================================
    start "" "http://localhost:5000"
    node server.js
    pause
    exit /b
)

:: Priority 2: Check for Python
where python >nul 2>nul
if %errorlevel% equ 0 (
    echo [1/2] Python detected!
    echo [2/2] Launching Flask Serial Gateway...
    start "" "http://localhost:5000"
    python app.py
    pause
    exit /b
)

:: Fallback: Open index.html directly
echo [Notice] Opening dashboard directly in your default web browser...
start "" "%~dp0templates\index.html"
echo.
echo TIP: Open with Google Chrome or Microsoft Edge and click "Connect USB" to link your Arduino!
pause
