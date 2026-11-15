@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run install-windows.ps1 first.
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "genta.py" %*
