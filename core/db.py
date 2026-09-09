# -*- coding: utf-8 -*-
"""SQLite 存取：任务表、断点续跑（4 引擎：baidu/bing/baidu_m/bing_m）"""
import sqlite3, datetime

_ENGINE_COLS = {
    "baidu":   ("baidu_status", "baidu_rank", "baidu_page", "baidu_evidence", "baidu_shot"),
    "bing":    ("bing_status", "bing_rank", "bing_page", "bing_evidence", "bing_shot"),
    "baidu_m": ("baidu_m_status", "baidu_m_rank", "baidu_m_page", "baidu_m_evidence", "baidu_m_shot"),
    "bing_m":  ("bing_m_status", "bing_m_rank", "bing_m_page", "bing_m_evidence", "bing_m_shot"),
}

_BASE_COLS = [
    ("baidu_status", "TEXT NOT NULL DEFAULT 'pending'"), ("baidu_rank", "INTEGER"),
    ("baidu_page", "INTEGER"), ("baidu_evidence", "TEXT"),
    ("bing_status", "TEXT NOT NULL DEFAULT 'pending'"), ("bing_rank", "INTEGER"),
    ("bing_page", "INTEGER"), ("bing_evidence", "TEXT"),
    ("baidu_shot", "TEXT"), ("bing_shot", "TEXT"),
    ("baidu_m_status", "TEXT NOT NULL DEFAULT 'pending'"), ("baidu_m_rank", "INTEGER"),
    ("baidu_m_page", "INTEGER"), ("baidu_m_evidence", "TEXT"), ("baidu_m_shot", "TEXT"),
    ("bing_m_status", "TEXT NOT NULL DEFAULT 'pending'"), ("bing_m_rank", "INTEGER"),
    ("bing_m_page", "INTEGER"), ("bing_m_evidence", "TEXT"), ("bing_m_shot", "TEXT"),
]

ALL_ENGINES = ("baidu", "bing", "baidu_m", "bing_m")

def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT UNIQUE NOT NULL,
        updated_at TEXT
    )""")
    # 迁移：旧表补列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for name, decl in _BASE_COLS:
        if name not in cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
    if "engines" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN engines TEXT DEFAULT 'baidu,bing,baidu_m,bing_m'")
    conn.commit()
    return conn

def ensure_keywords(conn, keywords, engines=None):
    """把关键词插入任务表；已存在的词也更新平台配置（导入勾选对新旧词都生效）。
    只改 engines 字段，保留已有结果状态——已跑过的平台不会因导入而重跑。"""
    engines = engines or list(ALL_ENGINES)
    engines_str = ",".join(engines)
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        conn.execute(
            "INSERT INTO tasks (keyword, engines, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(keyword) DO UPDATE SET engines=excluded.engines, updated_at=excluded.updated_at",
            (kw, engines_str, _now()))
    conn.commit()

def load_pending(conn):
    """取待处理词：返回 {keyword: [该词要跑且仍 pending 的引擎]}"""
    rows = conn.execute(
        "SELECT keyword, engines, baidu_status, bing_status, baidu_m_status, bing_m_status "
        "FROM tasks").fetchall()
    out = {}
    for kw, engines, bs, gs, bms, gms in rows:
        engs = [e for e in (engines or "").split(",") if e in _ENGINE_COLS]
        st = {"baidu": bs, "bing": gs, "baidu_m": bms, "bing_m": gms}
        todo = [e for e in engs if st[e] == "pending"]
        if todo:
            out[kw] = todo
    return out

def update_result(conn, keyword, engine, status, rank=None, page=None, evidence=None, shot=None):
    """engine: 'baidu' | 'bing' | 'baidu_m' | 'bing_m'"""
    col = _ENGINE_COLS[engine]
    conn.execute(
        f"UPDATE tasks SET {col[0]}=?, {col[1]}=?, {col[2]}=?, {col[3]}=?, {col[4]}=?, updated_at=? WHERE keyword=?",
        (status, rank, page, evidence, shot, _now(), keyword))
    conn.commit()

def retry_engine(conn, keyword, engine):
    """把指定词的指定引擎重置为 pending（单独重跑该平台）"""
    col = _ENGINE_COLS[engine][0]
    conn.execute(f"UPDATE tasks SET {col}='pending', updated_at=? WHERE keyword=?",
                 (_now(), keyword))
    conn.commit()

def summary(conn):
    rows = conn.execute(
        """SELECT
             SUM(CASE WHEN baidu_status='hit' THEN 1 ELSE 0 END),
             SUM(CASE WHEN bing_status='hit' THEN 1 ELSE 0 END),
             SUM(CASE WHEN baidu_m_status='hit' THEN 1 ELSE 0 END),
             SUM(CASE WHEN bing_m_status='hit' THEN 1 ELSE 0 END),
             COUNT(*) FROM tasks""").fetchone()
    b_hit, g_hit, bm_hit, gm_hit, total = rows
    return {"baidu_hit": b_hit or 0, "bing_hit": g_hit or 0,
            "baidu_m_hit": bm_hit or 0, "bing_m_hit": gm_hit or 0,
            "total": total or 0}
