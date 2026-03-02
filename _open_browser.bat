@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

set "PORT=5204"
if exist "%ROOT%meta.json" (
  if exist "%VENV_PY%" (
    for /f "usebackq delims=" %%p in (`"%VENV_PY%" -c "import json;print(json.load(open('meta.json','r',encoding='utf-8')).get('port',5204))"`) do set "PORT=%%p"
  ) else (
    for /f "usebackq delims=" %%p in (`python -c "import json;print(json.load(open('meta.json','r',encoding='utf-8')).get('port',5204))"`) do set "PORT=%%p"
  )
)

start "" "http://127.0.0.1:%PORT%/ready"
endlocal
