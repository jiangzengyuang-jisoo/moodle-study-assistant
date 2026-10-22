@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "LIBRARY_PYTHON=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%LIBRARY_PYTHON%" (
  echo Project Python environment not found. Run setup.cmd from the project folder first.
  exit /b 1
)
"%LIBRARY_PYTHON%" "%~dp0library.py" %*
exit /b %errorlevel%
