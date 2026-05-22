@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ================================================
echo   mynozi — 智能配音工坊
echo   http://localhost:8000
echo ================================================

:: ── Detect Python command ──
echo.
echo [1/5] 检查 Python 环境...
set PYCMD=

:: Try py first (Windows Store / modern install)
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYCMD=py
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set PYCMD=python
    ) else (
        python3 --version >nul 2>&1
        if %errorlevel% equ 0 (
            set PYCMD=python3
        )
    )
)

if "!PYCMD!"=="" (
    echo [错误] 未找到 Python，请先安装 Python 3.10 - 3.12
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 检测到: !PYCMD!
!PYCMD! --version

:: ── Check Python version ──
for /f "tokens=2" %%v in ('!PYCMD! --version 2^>^&1') do set PYVER=%%v
echo Python 版本: !PYVER!

for /f "tokens=2 delims=." %%m in ("!PYVER!") do set PYMNR=%%m
if !PYMNR! geq 14 (
    echo.
    echo [提示] Python 3.14+ 需从 PyTorch nightly 下载（~2.7GB，较慢）
    echo 推荐安装 Python 3.12 以加速首次安装：
    echo   1. 在命令行输入: py -3.12
    echo   2. 会自动跳转 Microsoft Store 安装，几十秒完成
    echo   3. 然后删除 .venv 重新运行本脚本即可
    echo.
    echo 继续将使用 Python !PYVER! + PyTorch nightly...
)
echo 版本兼容 ✓

:: ── Check ffmpeg ──
echo.
echo [2/5] 检查 ffmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 未找到 ffmpeg，ASR 音频处理将无法运行
    echo 下载地址: https://ffmpeg.org/download.html
    echo 安装后请将 ffmpeg.exe 所在目录加入 PATH 环境变量
)

:: ── Virtualenv ──
echo.
echo [3/5] 初始化虚拟环境...
if not exist ".venv" (
    !PYCMD! -m venv .venv
    echo 虚拟环境已创建
) else (
    echo 虚拟环境已存在，跳过
)

call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [错误] 虚拟环境激活失败
    pause
    exit /b 1
)

echo.
echo [4/5] 安装 Python 依赖（首次约 2-5 分钟，请耐心等待）...
echo.

:: 配置国内镜像加速
set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
echo → 使用清华镜像加速下载

:: 预先安装纯 Python editdistance 替代（修复 Python 3.13+ 编译问题）
echo → 预处理 editdistance 兼容层...
pip install -q backend\_editdistance_py
if %errorlevel% neq 0 (
    echo [警告] editdistance 兼容层安装失败，继续安装主依赖...
)

:: 安装 PyTorch（FunASR 模型推理需要）
echo → 安装 PyTorch...
for /f "tokens=2 delims=." %%m in ("!PYVER!") do set PYMNR=%%m
if !PYMNR! geq 14 (
    echo   Python 3.14+ 需要 PyTorch nightly（约 2.7GB，请耐心等待）...
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cpu
) else (
    pip install torch torchaudio
)

echo → 安装主依赖...
pip install -r backend\requirements.txt

:: 清除镜像设置
set PIP_INDEX=

:: ── Frontend build ──
echo.
echo [5/5] 检查前端...
if not exist "frontend\dist" (
    echo 首次运行，构建前端...
    cd frontend
    call npm install --silent
    call npm run build
    cd ..
    echo 前端构建完成
) else (
    echo 前端已构建，跳过
)

:: ── Start ──
echo.
echo ================================================
echo   启动服务...
echo   后端: http://localhost:8000
echo   API docs: http://localhost:8000/docs
echo ================================================
echo.

set HF_HUB_OFFLINE=1
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000

endlocal
