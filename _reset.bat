@echo off
setlocal
cd /d %~dp0

echo This will delete local state folders: data, exports, logs (if present).
choice /m "Proceed"
if errorlevel 2 exit /b 0

for %%D in (data exports logs) do (
  if exist %%D rmdir /s /q %%D
)

echo [OK] Reset complete.
pause
