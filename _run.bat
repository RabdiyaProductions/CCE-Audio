@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [CCE Audio] ERROR: venv missing. Run 00_setup.bat first.
  pause
  exit /b 1
)

if not exist "%ROOT%logs" mkdir "%ROOT%logs" >nul 2>&1

REM Read port from meta.json (default 5204)
for /f "usebackq delims=" %%p in (`"%VENV_PY%" -c "import json;print(json.load(open('meta.json','r',encoding='utf-8')).get('port',5204))"`) do set "PORT=%%p"
if not defined PORT set "PORT=5204"

echo [CCE Audio] Starting server on port %PORT%...

REM Start server in a persistent window so crashes stay visible
start "CCE Audio Server" cmd /k ""%VENV_PY%" "%ROOT%run_server.py""

REM Wait for /health to respond (timeout 40s)
"%VENV_PY%" -c "import time,urllib.request,sys;port=int('%PORT%');url=f'http://127.0.0.1:{port}/health';deadline=time.time()+40;ok=False; \
while time.time()<deadline: \
  try: urllib.request.urlopen(url, timeout=2).read(); ok=True; break \
  except Exception: time.sleep(1); \
print('READY' if ok else 'NOT_READY'); sys.exit(0 if ok else 1)"
if errorlevel 1 (
  echo [CCE Audio] ERROR: server did not become ready on port %PORT%.
  echo Check the "CCE Audio Server" window for the actual Python error.
  pause
  exit /b 1
)

echo [CCE Audio] Ready at http://127.0.0.1:%PORT%/ready
start "" "http://127.0.0.1:%PORT%/ready"

endlocal
