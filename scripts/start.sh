#!/usr/bin/env bash
# 官网检索器 Web 面板启动（Linux / macOS 通用）
# 已在跑则直接开页面；否则后台启动并轮询就绪
# 用法： bash scripts/start.sh
cd "$(dirname "$0")/.."

PORT=27531
URL="http://127.0.0.1:${PORT}"
PY="./.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "[ERROR] 未找到 $PY ，请先运行 scripts/install.sh"
    exit 1
fi

open_url() {
    case "$(uname -s)" in
        Darwin) open "$URL" ;;
        Linux)
            if command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$URL"
            else
                echo "请手动打开浏览器访问 $URL"
            fi
            ;;
        *) echo "请手动打开浏览器访问 $URL" ;;
    esac
}

if curl -s -o /dev/null --connect-timeout 2 "$URL/"; then
    echo "面板已在运行，打开 $URL"
    open_url
    exit 0
fi

echo "后台启动服务 ..."
nohup "$PY" server.py --no-browser >/dev/null 2>&1 &

echo -n "等待服务就绪"
for i in $(seq 1 30); do
    sleep 1
    if curl -s -o /dev/null --connect-timeout 1 "$URL/"; then
        echo " [OK]"
        open_url
        exit 0
    fi
    echo -n "."
done
echo ""
echo "[ERROR] 启动失败，请检查端口 $PORT 是否被占用"
exit 1
