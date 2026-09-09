"""CLI 入口：加载关键词 → 逐词跑百度/必应 → 入库 → 断点续跑"""

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core import config, db
from core.baidu import run_baidu
from core.bing import run_bing
from core.engine import BrowserSession, stop_playwright


def load_keywords():
    if not os.path.exists(config.KEYWORDS_FILE):
        print(f"缺少关键词文件: {config.KEYWORDS_FILE}")
        print("请创建 keywords.txt，每行一个关键词。")
        sys.exit(1)
    try:
        with open(config.KEYWORDS_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        with open(config.KEYWORDS_FILE, encoding="gbk", errors="replace") as f:
            lines = f.read().splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def main():
    os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)
    conn = db.init_db(config.DB_PATH)
    keywords = load_keywords()
    db.ensure_keywords(conn, keywords)

    pending = db.load_pending(conn)
    if not pending:
        print("没有待处理关键词，全部完成。")
        db.summary(conn)
        return

    print(f"待处理 {len(pending)} 个关键词（断点续跑，已完成自动跳过）")
    session = BrowserSession()

    try:
        for kw in pending:
            print(f"\n▶ 关键词：{kw}")
            # 百度
            b = run_baidu(session, kw, config.SCREENSHOT_DIR)
            db.update_result(
                conn,
                kw,
                "baidu",
                b["status"],
                b.get("rank"),
                b.get("page"),
                b.get("evidence"),
                b.get("shot"),
            )
            tag = {"hit": "命中", "none": "未命中", "error": "错误"}[b["status"]]
            print(
                f"   百度: {tag} 排名{b.get('rank')} 第{b.get('page')}页 | "
                f"{b.get('evidence', '')[:60]}"
            )
            # 必应
            g = run_bing(session, kw, config.SCREENSHOT_DIR)
            db.update_result(
                conn,
                kw,
                "bing",
                g["status"],
                g.get("rank"),
                g.get("page"),
                g.get("evidence"),
                g.get("shot"),
            )
            tag = {"hit": "命中", "none": "未命中", "error": "错误"}[g["status"]]
            print(
                f"   必应: {tag} 排名{g.get('rank')} 第{g.get('page')}页 | "
                f"{g.get('evidence', '')[:60]}"
            )
    finally:
        session.close()
        stop_playwright()

    s = db.summary(conn)
    print(
        f"\n===== 汇总：百度命中 {s['baidu_hit']}/{s['total']}，"
        f"必应命中 {s['bing_hit']}/{s['total']} ====="
    )
    print(f"数据在 {config.DB_PATH}，截图在 {config.SCREENSHOT_DIR}")


if __name__ == "__main__":
    main()
