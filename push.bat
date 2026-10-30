@echo off
title Push Farming Monitor to GitHub
cd /d "%~dp0"
echo ===============================================================================
echo        🌱 PUSHING FARMING MONITOR TO GITHUB 🌱
echo ===============================================================================
echo.
echo Launching Git push...
"C:\Program Files\Git\cmd\git.exe" push -u origin main
echo.
if %errorlevel% equ 0 (
    echo ===============================================================================
    echo [SUCCESS] All code has been successfully pushed to GitHub!
    echo ===============================================================================
) else (
    echo ===============================================================================
    echo [NOTICE] If prompted above or in your browser, please authenticate.
    echo ===============================================================================
)
echo.
pause
