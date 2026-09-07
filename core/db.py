# -*- coding: utf-8 -*-
"""SQLite 存取：任务表、断点续跑"""
import sqlite3, datetime

def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT UNIQUE NOT NULL,
        baidu_status TEXT NOT NULL DEFAULT 'pending',
        baidu_rank INTEGER, baidu_page INTEGER, baidu_evidence TEXT,
        bing_status TEXT NOT NULL DEFAULT 'pending',
        bing_rank INTEGER, bing_page INTEGER, bing_evidence TEXT,
        baidu_shot TEXT, bing_shot TEXT,
        updated_at TEXT
    )""")
    conn.commit()
    return conn

def ensure_keywords(conn, keywords):
    """把关键词插入任务表（已存在的跳过）"""
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO tasks (keyword, updated_at) VALUES (?, ?)",
            (kw, _now()))
    conn.commit()

def load_pending(conn):
    """取未完成的词：任一侧还是 pending 的"""
    rows = conn.execute(
        """SELECT keyword FROM tasks
           WHERE baidu_status='pending' OR bing_status='pending'
           ORDER BY id""").fetchall()
    return [r[0] for r in rows]

def update_result(conn, keyword, engine, status, rank=None, page=None, evidence=None, shot=None):
    """engine: 'baidu' | 'bing'"""
    col = {
        "baidu": ("baidu_status", "baidu_rank", "baidu_page", "baidu_evidence", "baidu_shot"),
        "bing":  ("bing_status",  "bing_rank",  "bing_page",  "bing_evidence",  "bing_shot"),
    }[engine]
    conn.execute(
        f"UPDATE tasks SET {col[0]}=?, {col[1]}=?, {col[2]}=?, {col[3]}=?, {col[4]}=?, updated_at=? WHERE keyword=?",
        (status, rank, page, evidence, shot, _now(), keyword))
    conn.commit()

def summary(conn):
    rows = conn.execute(
        """SELECT
             SUM(CASE WHEN baidu_status='hit' THEN 1 ELSE 0 END),
             SUM(CASE WHEN bing_status='hit' THEN 1 ELSE 0 END),
             COUNT(*) FROM tasks""").fetchone()
    b_hit, g_hit, total = rows
    return {"baidu_hit": b_hit or 0, "bing_hit": g_hit or 0, "total": total or 0}
