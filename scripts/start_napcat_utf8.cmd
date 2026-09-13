@echo off
setlocal EnableExtensions

rem NapCat writes UTF-8 logs and uses Unicode block characters for its QR code.
rem Set the code page in the console that directly owns NapCat's stdout.
chcp 65001 >nul

set "NAPCAT_BOOT=%~1"
set "BOT_QQ=%~2"
if not defined NAPCAT_BOOT exit /b 2

cd /d "%~dp1"
title NapCat - QQ %BOT_QQ%
"%NAPCAT_BOOT%" "%BOT_QQ%"

echo.
echo [INFO] NapCat has stopped. Press any key to close this window.
pause >nul
