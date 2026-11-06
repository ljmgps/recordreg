@echo off
cd /d %~dp0
set "PY=%~dp0.venv\Scripts\python.exe"

echo ============================================
echo   RecordReg 模型下载（从 ModelScope）
echo ============================================
echo.

if not exist "%PY%" (
    echo [错误] 虚拟环境不存在，请先运行 安装环境.bat
    pause
    exit /b 1
)

echo 即将下载 3 个模型，总计约 930MB
echo   - SenseVoiceSmall        语音识别   约 897MB
echo   - fsmn-vad               语音分段   约 4MB
echo   - cam++                  声纹       约 28MB
echo.
echo 下载支持断点续传，中断后重新运行本脚本即可。
echo.

"%PY%" scripts\download_models.py %*
if errorlevel 1 (
    echo.
    echo [错误] 模型下载未全部完成
    pause
    exit /b 1
)

echo.
echo ============================================
echo   模型就绪，运行 [启动M1.bat] 开始转写
echo ============================================
pause
