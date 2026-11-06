@echo off
cd /d %~dp0
set "PY=%~dp0.venv\Scripts\python.exe"
set "IDX=https://pypi.tuna.tsinghua.edu.cn/simple"

echo ============================================
echo   RecordReg 环境安装 (uv + Python 3.11)
echo ============================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv 命令
    echo        请先安装：winget install astral-sh.uv
    echo.
    pause
    exit /b 1
)

echo [1/6] 创建 Python 3.11 虚拟环境 ...
if exist "%PY%" (
    echo       已存在，跳过
) else (
    uv venv --python 3.11 .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
echo.

echo [2/6] 安装 PyTorch 2.5.1 + CUDA 12.1 （约 2.3GB，最慢的一步）...
uv pip install --python "%PY%" --index https://download.pytorch.org/whl/cu121 --index %IDX% torch==2.5.1 torchaudio==2.5.1
if errorlevel 1 (
    echo.
    echo [错误] PyTorch 安装失败
    echo        可换阿里镜像重试：
    echo        uv pip install --python "%PY%" --index https://mirrors.aliyun.com/pytorch-wheels/cu121 --index %IDX% torch==2.5.1 torchaudio==2.5.1
    pause
    exit /b 1
)
echo.

echo [3/6] 安装 pip 与 antlr4 运行时（该包只有源码，需从源码构建）...
uv pip install --python "%PY%" --index %IDX% pip
if errorlevel 1 (
    echo [错误] pip 安装失败
    pause
    exit /b 1
)
"%PY%" -m pip install --no-cache-dir "antlr4-python3-runtime==4.9.3" -i %IDX%
if errorlevel 1 (
    echo [错误] antlr4 安装失败
    pause
    exit /b 1
)
echo.

echo [4/6] 安装其余依赖 ...
uv pip install --python "%PY%" --index %IDX% -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo.

echo [5/6] 安装 funasr 1.2.9（跳过 oss2 等无关依赖）...
uv pip install --python "%PY%" --index %IDX% --no-deps funasr==1.2.9
if errorlevel 1 (
    echo [错误] funasr 安装失败
    pause
    exit /b 1
)
echo.

echo [6/6] 环境自检 ...
"%PY%" scripts\check_env.py
echo.

echo ============================================
echo   环境安装完成
echo   下一步：运行 [下载模型.bat]
echo ============================================
pause
