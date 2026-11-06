@echo off
cd /d %~dp0
title RecordReg 录音转写
set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PY%" (
    echo [错误] 虚拟环境不存在，请先运行 安装环境.bat
    pause
    exit /b 1
)
if not exist "models\SenseVoiceSmall\model.pt" (
    echo [错误] 模型未下载，请先运行 下载模型.bat
    pause
    exit /b 1
)

"%PY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [错误] 缺少界面组件 PySide6
    echo        请重新运行 安装环境.bat
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0app\main.py"
exit /b 0
