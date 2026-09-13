@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title QQ Bot - Full Local Launcher

set "LAUNCH_MODE="
if /i "%~1"=="--check" set "LAUNCH_MODE=-Check"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" %LAUNCH_MODE%
set "BOT_EXIT_CODE=%ERRORLEVEL%"

echo.
if "%BOT_EXIT_CODE%"=="0" (
    echo [INFO] Launcher finished.
) else (
    echo [ERROR] Launcher exited with code %BOT_EXIT_CODE%.
)
pause
exit /b %BOT_EXIT_CODE%
