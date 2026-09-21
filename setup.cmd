@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 echo Setup failed. Please copy the error text to your AI assistant.
pause
