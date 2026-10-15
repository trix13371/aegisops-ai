@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\openrouter-local.ps1"
set "aegisopsExit=%ERRORLEVEL%"
echo.
pause
exit /b %aegisopsExit%
