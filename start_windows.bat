@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo 请先安装 Python 3.11 或更新版本：https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo 创建 Python 环境失败。
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
if errorlevel 1 (
  echo 安装依赖失败，请检查网络连接。
  pause
  exit /b 1
)
.venv\Scripts\python.exe desktop.py %*
pause
