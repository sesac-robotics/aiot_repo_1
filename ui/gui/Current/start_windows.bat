@echo off
setlocal
cd /d "%~dp0"
if not exist "..\.venv\Scripts\python.exe" (
  echo Create gui\.venv and install requirements first. See gui\README_KO.md.
  pause
  exit /b 1
)
set PYTHONUTF8=1
"..\.venv\Scripts\python.exe" run.py
if errorlevel 1 pause
