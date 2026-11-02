@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
rem Keep browser temporary files local to this invocation, without changing Windows settings.
if not exist "tmp\auth-runtime" mkdir "tmp\auth-runtime" >nul 2>&1
if not exist "tmp\auth-runtime" (
  echo Cannot create the login temporary directory. Check this project folder is writable.
  pause
  exit /b 1
)
set "TEMP=%CD%\tmp\auth-runtime"
set "TMP=%TEMP%"
".venv\Scripts\python.exe" tools\moodle\auth.py --browser chrome %*
pause
