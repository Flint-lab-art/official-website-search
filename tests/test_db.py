"""db.py 任务表/断点续跑/单平台重跑测试"""

from core import db


def test_init_creates_table(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_ensure_keywords_skip_duplicate(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A", "词A", "词B"])
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_load_pending_default_all_engines(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A"])
        pend = db.load_pending(conn)
        assert pend == {"词A": list(db.ALL_ENGINES)}
    finally:
        conn.close()


def test_load_pending_custom_engines(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A"], engines=["baidu", "baidu_m"])
        pend = db.load_pending(conn)
        assert pend == {"词A": ["baidu", "baidu_m"]}
    finally:
        conn.close()


def test_update_result_excludes_finished_engine(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A"])
        db.update_result(conn, "词A", "baidu", "hit", rank=2, page=1, evidence="x", shot="s.png")
        pend = db.load_pending(conn)
        # baidu 已命中，只剩其余 3 平台
        assert pend == {"词A": ["bing", "baidu_m", "bing_m"]}
    finally:
        conn.close()


def test_retry_engine_resets_to_pending(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A"])
        db.update_result(conn, "词A", "bing", "none")
        assert "bing" not in db.load_pending(conn)["词A"]
        db.retry_engine(conn, "词A", "bing")
        assert "bing" in db.load_pending(conn)["词A"]
    finally:
        conn.close()


def test_summary_counts(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    try:
        db.ensure_keywords(conn, ["词A", "词B"])
        db.update_result(conn, "词A", "baidu", "hit")
        db.update_result(conn, "词B", "bing", "hit")
        s = db.summary(conn)
        assert s["baidu_hit"] == 1
        assert s["bing_hit"] == 1
        assert s["total"] == 2
    finally:
        conn.close()
