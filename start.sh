#!/bin/bash
# mynozi — macOS / Linux 一键启动脚本

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================"
echo "  mynozi — 智能配音工坊"
echo "  http://$(hostname -s 2>/dev/null || echo 'localhost'):8000"
echo "================================================"

# ── Python backend ──
echo ""
echo "[1/3] 初始化 Python 虚拟环境..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  venv 已创建"
fi

source .venv/bin/activate

echo "[2/3] 安装 Python 依赖..."
# 预安装纯 Python editdistance（修复 Python 3.13+ 编译问题）
pip install -q backend/_editdistance_py 2>/dev/null || true
# PyTorch（FunASR 模型推理需要）
PY_MINOR=$(python -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MINOR" -ge 14 ]; then
    echo "  Python 3.14+ 需要 PyTorch nightly..."
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/nightly/cpu
else
    pip install torch torchaudio
fi
pip install -r backend/requirements.txt
echo "  依赖已就绪"

# ── Frontend build ──
echo "[3/3] 检查前端..."
if [ ! -d "frontend/dist" ]; then
    echo "  首次运行，构建前端..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
    echo "  前端构建完成"
fi

# ── Start ──
echo ""
echo "  启动服务..."
.venv/bin/python -c "import sys; sys.path.insert(0, 'backend')" 2>/dev/null
cd backend
HF_HUB_OFFLINE=1 ../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
