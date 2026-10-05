@echo off
chcp 65001 >nul
set "IPG_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%IPG_PYTHON%" (
  echo 未找到项目 Python 环境：%IPG_PYTHON%
  echo 请先按照 README 完成本地环境安装。
  pause
  exit /b 1
)
"%IPG_PYTHON%" -m ipg_pipeline.review_assistant
if errorlevel 1 pause
