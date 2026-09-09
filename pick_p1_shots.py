"""挑出「第一页命中」的截图，归集到 Elo官网检索截图/第一页命中/ 并按平台分子文件夹。

用法: python pick_p1_shots.py [数据库路径(默认 results.db)]
客户需要「第一页就命中」的证据：只挑 page=1 且 status=hit 的截图。
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config
from core.db import init_db

ENGINES = [
    ("baidu", "百度/PC"),
    ("bing", "必应/PC"),
    ("baidu_m", "百度/MOB"),
    ("bing_m", "必应/MOB"),
]


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else config.DB_PATH
    conn = init_db(db_path)

    base = os.path.join(config.SCREENSHOT_DIR, "第一页命中")
    os.makedirs(base, exist_ok=True)

    rows, missing, total = [], 0, 0
    for eng, folder in ENGINES:
        col_st, col_pg, col_shot = f"{eng}_status", f"{eng}_page", f"{eng}_shot"
        cur = conn.execute(
            f"SELECT keyword, {col_pg}, {col_shot} FROM tasks "  # noqa: S608
            f"WHERE {col_st}='hit' AND {col_pg}=1 AND {col_shot} IS NOT NULL AND {col_shot} != ''"
        )
        dst_dir = os.path.join(base, folder)
        os.makedirs(dst_dir, exist_ok=True)
        for kw, page, shot in cur.fetchall():
            total += 1
            if not os.path.exists(shot):
                missing += 1
                print(f"!! 截图缺失: {shot}")
                continue
            name = os.path.basename(shot)
            dst = os.path.join(dst_dir, name)
            shutil.copy2(shot, dst)
            rows.append((kw, folder, os.path.basename(name)))

    # 清单
    manifest = os.path.join(base, "第一页命中清单.txt")
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("关键词\t平台\t截图文件\n")
        for kw, folder, name in rows:
            f.write(f"{kw}\t{folder}\t{name}\n")
    print(f"共挑出 {len(rows)} 张第一页命中截图（缺失 {missing}）")
    print(f"输出目录: {base}")
    print(f"清单: {manifest}")
    for folder, cnt in _group(rows):
        print(f"  {folder}: {cnt} 张")


def _group(rows):
    d = {}
    for _, folder, _ in rows:
        d[folder] = d.get(folder, 0) + 1
    return sorted(d.items())


if __name__ == "__main__":
    main()
