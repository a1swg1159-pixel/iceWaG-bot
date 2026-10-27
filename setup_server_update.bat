@echo off
setlocal EnableExtensions
chcp 65001 >nul
title iceWaG Bot - Save Server Login
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_server.ps1" -Configure %*
set "UPDATE_EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %UPDATE_EXIT_CODE%
