@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "IPG_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%IPG_PYTHON%" (
  echo Project Python environment not found: %IPG_PYTHON%
  echo Please follow README to install the local environment.
  pause
  exit /b 1
)
"%IPG_PYTHON%" -m ipg_pipeline.review_assistant
if errorlevel 1 pause
