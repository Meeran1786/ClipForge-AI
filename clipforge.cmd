@echo off
rem ClipForge AI launcher: starts the app and opens the dashboard.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ClipForge AI: the Python environment is missing at backend\.venv
  exit /b 1
)
"%PY%" -m clipforge %*
