@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"

if exist "%ROOT%.venv" (
  echo [CCE Audio] .venv already exists.
) else (
  python -m venv "%ROOT%.venv"
  if errorlevel 1 (
    echo [CCE Audio] ERROR: Failed to create venv.
    pause
    exit /b 1
  )
)

call "%ROOT%.venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [CCE Audio] ERROR: Failed to activate venv.
  pause
  exit /b 1
)

python -m pip install --upgrade pip setuptools wheel >nul 2>&1
pip install -r "%ROOT%requirements.txt"
if errorlevel 1 (
  echo.
  echo [CCE Audio] ERROR: pip install failed.
  echo Fix: move repo to a short path like C:\KR\CCAudio\CCE_Audio
  echo Then delete .venv and run setup again.
  echo.
  pause
  exit /b 1
)

echo [CCE Audio] Setup complete.
pause
endlocal
