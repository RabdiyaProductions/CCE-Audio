@echo off
setlocal
cd /d %~dp0

if not exist exports mkdir exports
echo [OK] Exports folder ensured at: %CD%\exports
pause
