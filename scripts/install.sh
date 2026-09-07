#!/usr/bin/env bash
# 官网检索器 依赖安装（Linux / macOS 通用）
# 用法： bash scripts/install.sh
set -e
cd "$(dirname "$0")/.."

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERROR] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

if [ ! -d .venv ]; then
    echo "创建虚拟环境 .venv ..."
    "$PY" -m venv .venv
fi

echo "升级 pip 并安装依赖（见 requirements.txt）..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo "安装 Chromium（Playwright）..."
./.venv/bin/python -m playwright install chromium

echo ""
echo "安装完成。启动方式："
echo "  bash scripts/start.sh"
echo "  或手动： ./.venv/bin/python server.py --no-browser"
