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

:: Extract minor version (e.g. 3.12.9 → 12)
for /f "tokens=2 delims=." %%m in ("!PYVER!") do set PYMNR=%%m
if !PYMNR! leq 12 (
    echo 版本兼容 ✓
) else (
    echo.
    echo **********************************************************************
    echo [警告] Python !PYVER! 可能不兼容！
    echo FunASR 依赖的 editdistance 包暂无 Python 3.13+ 的 Windows 预编译版本。
    echo 建议安装 Python 3.10 - 3.12 后重新运行本脚本。
    echo 下载: https://www.python.org/downloads/release/python-3129/
    echo.
    echo 如果你已有 Python 3.12，可以运行：
    echo   py -3.12 -m venv .venv
    echo   然后重新执行 start.bat
    echo **********************************************************************
    echo.
    set /p CONTINUE="仍然继续？(y/n): "
    if /i not "!CONTINUE!"=="y" exit /b 1
)

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
pip install -r backend\requirements.txt

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
