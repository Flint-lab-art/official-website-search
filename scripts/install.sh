#!/usr/bin/env bash
# 官网检索器 依赖安装（Linux / macOS 通用）
# UV 优先（pyproject.toml + uv.lock 锁版本），无 uv 时回退 python3 + pip
# 用法： bash scripts/install.sh
set -e
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
    echo "检测到 uv，使用 UV 安装（版本由 uv.lock 锁定）..."
    uv sync
    echo "安装 Chromium（Playwright）..."
    uv run playwright install chromium
else
    echo "未检测到 uv，回退 python3 + pip（requirements.txt）..."
    PY=python3
    if ! command -v "$PY" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 python3，请先安装 Python 3.10+（或安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh）"
        exit 1
    fi
    if [ ! -d .venv ]; then
        echo "创建虚拟环境 .venv ..."
        "$PY" -m venv .venv
    fi
    echo "升级 pip 并安装依赖..."
    ./.venv/bin/python -m pip install --upgrade pip
    ./.venv/bin/python -m pip install -r requirements.txt
    echo "安装 Chromium（Playwright）..."
    ./.venv/bin/python -m playwright install chromium
fi

echo ""
echo "安装完成。启动方式："
echo "  bash scripts/start.sh"
echo "  或手动： ./.venv/bin/python server.py --no-browser"
