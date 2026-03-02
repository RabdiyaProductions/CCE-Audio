@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [CCE Audio] ERROR: venv missing. Run 00_setup.bat first.
  pause
  exit /b 1
)

"%VENV_PY%" "%ROOT%tools\run_full_tests.py"
pause
endlocal
