@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\connect-openrouter-local.ps1"
set "aegisopsExit=%ERRORLEVEL%"
echo.
pause
exit /b %aegisopsExit%
