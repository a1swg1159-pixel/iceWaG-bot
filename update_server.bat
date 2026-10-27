@echo off
setlocal EnableExtensions
chcp 65001 >nul
title iceWaG Bot - Server Update
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_server.ps1" %*
set "UPDATE_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%UPDATE_EXIT_CODE%"=="0" echo [ERROR] Update failed. See the message above.
pause
exit /b %UPDATE_EXIT_CODE%
