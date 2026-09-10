"""官网检索器 Web 面板（本地服务 + 浏览器界面）
启动: python server.py  → 自动打开 http://127.0.0.1:27531
采集核心复用 core/（Playwright + SQLite + 判定），仅替换展示层。
"""

import contextlib
import json
import os
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# pythonw.exe 下 stdout/stderr 为 None，print 会崩溃——给哑对象兜底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        with contextlib.suppress(Exception):
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import contextlib

from core import config, db, logger
from core.baidu import run_baidu
from core.baidu_m import run_baidu_m
from core.bing import run_bing
from core.bing_m import run_bing_m
from core.engine import BrowserSession, par_profile_dir, stop_playwright

# 支持 --port N 覆盖默认端口（dev/stable 双目录可同时运行不同端口）
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 27531
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAVICON = os.path.join(BASE_DIR, "favicon.png")
LOG_DIR = os.path.join(BASE_DIR, "logs")
ST_LABEL = {
    "pending": "等待",
    "hit": "命中",
    "none": "未命中",
    "error": "错误",
    "restricted": "受限",
    "malfunction": "解析异常",
}
ENG_LABEL = {"baidu": "百度PC", "bing": "必应PC", "baidu_m": "百度移动", "bing_m": "必应移动"}


def _append_log_file(line):
    """面板日志落盘：统一走 core.logger（logs/run_YYYYMMDD.log）"""
    logger.info(line)


# 导出可选列：(id, 表头, 行索引, 类型, 列宽)
# 类型: raw=直取, st=状态转标签, shot=截图(文件名+超链接), concl=结论, kw=关键词
EXPORT_COLS = [
    ("kw", "关键词", 0, "kw", 30),
    ("bd", "百度PC状态", 1, "st", 12),
    ("bd_rank", "百度PC排名", 2, "raw", 10),
    ("bd_page", "百度PC页码", 3, "raw", 10),
    ("bd_ev", "百度PC依据", 4, "raw", 80),
    ("bd_shot", "百度PC截图", 5, "shot", 60),
    ("bg", "必应PC状态", 6, "st", 12),
    ("bg_rank", "必应PC排名", 7, "raw", 10),
    ("bg_page", "必应PC页码", 8, "raw", 10),
    ("bg_ev", "必应PC依据", 9, "raw", 80),
    ("bg_shot", "必应PC截图", 10, "shot", 60),
    ("bm", "百度移动状态", 11, "st", 12),
    ("bm_rank", "百度移动排名", 12, "raw", 10),
    ("bm_page", "百度移动页码", 13, "raw", 10),
    ("bm_ev", "百度移动依据", 14, "raw", 80),
    ("bm_shot", "百度移动截图", 15, "shot", 60),
    ("gm", "必应移动状态", 16, "st", 12),
    ("gm_rank", "必应移动排名", 17, "raw", 10),
    ("gm_page", "必应移动页码", 18, "raw", 10),
    ("gm_ev", "必应移动依据", 19, "raw", 80),
    ("gm_shot", "必应移动截图", 20, "shot", 60),
    ("concl", "结论", None, "concl", 16),
    ("upd", "更新时间", 21, "raw", 45),
]


def conclusion(*st):
    hits = [s == "hit" for s in st]
    n = sum(hits)
    if n == len(st):
        return "官网全可见"
    if n:
        return f"部分可见 {n}/{len(st)}"
    if any(s == "error" for s in st):
        return "含错误"
    return "10页未见"


# ================= 状态与日志 =================
class State:
    worker: "Worker | None"
    healthcheck: "threading.Thread | None"
    current: "tuple[str, str] | None"
    captcha: "tuple[str, str] | None"
    parallel: bool = False  # 并行模式：一个词内勾选平台同时跑

    def __init__(self):
        self.lock = threading.Lock()
        self.worker = None
        self.healthcheck = None
        self.pause_evt = threading.Event()
        self.stop_evt = threading.Event()
        self.skip_evt = threading.Event()
        self.current = None  # (kw, engine)
        self.captcha = None  # (engine, kw)
        self.parallel = False
        self.start_ts: float | None = None  # 本次任务开始时刻（time.time），用于计时
        self.done = 0  # 已处理完的关键词数
        self.total_kw = 0  # 本次任务关键词总数
        self.logs = deque(maxlen=800)

    def log(self, text):
        ts = time.strftime("%H:%M:%S")
        with self.lock:
            self.logs.append((ts, text))
        _append_log_file(f"[{ts}] {text}")

    def snapshot_logs(self, since=0):
        with self.lock:
            items = list(self.logs)[since:]
        return items


STATE = State()
# SQLite 并发写保护：并行模式下多线程共用一个 conn（check_same_thread=False）
DB_LOCK = threading.Lock()


# ================= 采集 Worker =================
RUNNERS = {"baidu": run_baidu, "bing": run_bing, "baidu_m": run_baidu_m, "bing_m": run_bing_m}


class Worker(threading.Thread):
    def __init__(self, pending):
        """pending: {keyword: [engines 待跑列表]}"""
        super().__init__(daemon=True)
        self.pending = pending

    def notify_captcha(self, engine, keyword):
        with STATE.lock:
            STATE.captcha = (engine, keyword)
        STATE.log(f"[验证码] {engine}「{keyword}」请在浏览器窗口手动完成验证")

    def on_page(self, keyword, engine, pn, count=None):
        """每个关键词翻到第几页时实时写日志；count 为解析到的结果条数"""
        if count is None:
            STATE.log(f"   [{keyword}] {ENG_LABEL.get(engine, engine)} 正在查第{pn}页…")
        else:
            STATE.log(
                f"   [{keyword}] {ENG_LABEL.get(engine, engine)} 正在查第{pn}页（解析到{count}条结果）…"
            )

    def _ensure_page(self, eng, session, m_session, pages):
        """懒开标签：优先复用浏览器启动自带的默认空白页（导航到目标平台），
        没有再新建——避免窗口出现多余空白标签，也避免关默认页导致窗口关闭。
        必须在主线程调用（playwright 跨线程 new_page 不安全）。"""
        sess = session if eng in ("baidu", "bing") else m_session
        if sess is None:
            raise RuntimeError("浏览器会话未初始化")
        if eng not in pages:
            reused = None
            for p in sess.ctx.pages:
                if p.url in ("about:blank", "") and p not in pages.values():
                    reused = p
                    break
            pages[eng] = reused if reused is not None else sess.new_page()

    def _run_one(self, kw, eng, session, m_session, pages, conn):
        """执行单个引擎的采集：搜索→命中→落库→日志。
        并行模式下由各引擎线程调用；返回 True 表示该引擎因验证码被跳过。"""
        func = RUNNERS[eng]
        with STATE.lock:
            STATE.current, STATE.captcha = (kw, ENG_LABEL[eng]), None
        STATE.log(f"▶ {kw} [{ENG_LABEL[eng]}]")
        sess = session if eng in ("baidu", "bing") else m_session
        if sess is None:
            raise RuntimeError("浏览器会话未初始化")
        if eng in ("baidu", "baidu_m"):
            r = func(
                sess,
                kw,
                config.SCREENSHOT_DIR,
                skip_evt=STATE.skip_evt,
                notify=self.notify_captcha,
                page=pages[eng],
                on_page=self.on_page,
            )
        else:
            r = func(sess, kw, config.SCREENSHOT_DIR, page=pages[eng], on_page=self.on_page)
        with DB_LOCK:
            db.update_result(
                conn,
                kw,
                eng,
                r["status"],
                r.get("rank"),
                r.get("page"),
                r.get("evidence"),
                r.get("shot"),
            )
        STATE.log(
            f"   {ENG_LABEL[eng]} {ST_LABEL.get(r['status'], r['status'])} "
            f"排名{r.get('rank')} 第{r.get('page')}页 | {r.get('evidence', '')[:50]}"
        )
        if STATE.skip_evt.is_set():
            STATE.skip_evt.clear()
            with DB_LOCK:
                db.update_result(conn, kw, eng, "error", evidence="验证码跳过")
            STATE.log("   已跳过（验证码）")
            return True
        return False

    def _run_one_own(self, kw, eng, sess, page, conn):
        """并行模式：平台流水线中的单个词。sess/page 由平台线程创建并全程复用。"""
        with STATE.lock:
            STATE.current, STATE.captcha = (kw, ENG_LABEL[eng]), None
        STATE.log(f"▶ {kw} [{ENG_LABEL[eng]}]")
        func = RUNNERS[eng]
        if eng in ("baidu", "baidu_m"):
            r = func(
                sess,
                kw,
                config.SCREENSHOT_DIR,
                skip_evt=STATE.skip_evt,
                notify=self.notify_captcha,
                page=page,
                on_page=self.on_page,
            )
        else:
            r = func(sess, kw, config.SCREENSHOT_DIR, page=page, on_page=self.on_page)
        with DB_LOCK:
            db.update_result(
                conn,
                kw,
                eng,
                r["status"],
                r.get("rank"),
                r.get("page"),
                r.get("evidence"),
                r.get("shot"),
            )
        STATE.log(
            f"   {ENG_LABEL[eng]} {ST_LABEL.get(r['status'], r['status'])} "
            f"排名{r.get('rank')} 第{r.get('page')}页 | {r.get('evidence', '')[:50]}"
        )
        if STATE.skip_evt.is_set():
            STATE.skip_evt.clear()
            with DB_LOCK:
                db.update_result(conn, kw, eng, "error", evidence="验证码跳过")
            STATE.log("   已跳过（验证码）")

    def _pipeline_eng(self, eng, conn):
        """并行模式：单平台流水线线程——一个持久窗口依次跑完本平台全部待跑关键词。
        sync API 线程绑定，实例必须在本线程 start/使用/stop。"""
        mobile = eng in ("baidu_m", "bing_m")
        STATE.log(f"启动浏览器（{'移动版' if mobile else 'PC 版'}，{ENG_LABEL[eng]}）…")
        try:
            sess = BrowserSession(profile_dir=par_profile_dir(eng), mobile=mobile, own_pw=True)
        except Exception as e:
            STATE.log(f"   !! 浏览器启动失败（{ENG_LABEL[eng]}）: {e}")
            with DB_LOCK:
                for kw, engs in self.pending.items():
                    if eng in engs:
                        db.update_result(conn, kw, eng, "error", evidence=f"浏览器启动失败:{e}")
            return
        try:
            # 复用启动自带的默认空白页，全程一个标签，避免窗口出现多余标签
            page = None
            for p in sess.ctx.pages:
                if p.url in ("about:blank", ""):
                    page = p
                    break
            if page is None:
                page = sess.new_page()
            for kw, engs in self.pending.items():
                if eng not in engs:
                    continue
                if STATE.stop_evt.is_set():
                    break
                while STATE.pause_evt.is_set() and not STATE.stop_evt.is_set():
                    time.sleep(0.3)
                if STATE.stop_evt.is_set():
                    break
                if STATE.skip_evt.is_set():
                    STATE.skip_evt.clear()
                self._run_one_own(kw, eng, sess, page, conn)
                with STATE.lock:
                    self._kw_left[kw] -= 1
                    if self._kw_left[kw] <= 0:
                        STATE.done += 1
        finally:
            sess.close()
            STATE.log(f"   {ENG_LABEL[eng]} 浏览器已关闭")

    def run(self):
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        # 按待跑引擎决定开哪些浏览器窗口：只开需要的
        engs_needed = set()
        for engs in self.pending.values():
            engs_needed.update(engs)
        session: BrowserSession | None = None
        m_session: BrowserSession | None = None
        if not STATE.parallel:
            need_pc = bool(engs_needed & {"baidu", "bing"})
            need_m = bool(engs_needed & {"baidu_m", "bing_m"})
            try:
                if need_pc:
                    STATE.log("启动浏览器（PC 版）…")
                    session = BrowserSession()
                    STATE.log("PC 浏览器已就绪")
                if need_m:
                    STATE.log("启动浏览器（移动版）…")
                    m_session = BrowserSession(mobile=True)
                    STATE.log("移动浏览器已就绪")
                if not need_pc and not need_m:
                    with STATE.lock:
                        STATE.worker, STATE.current, STATE.captcha, STATE.start_ts = (
                            None,
                            None,
                            None,
                            None,
                        )
                    return
            except Exception as e:
                STATE.log(f"!! 浏览器启动失败: {e}")
            try:
                if session:
                    session.close()
                if m_session:
                    m_session.close()
            except Exception:
                pass
            with STATE.lock:
                STATE.worker, STATE.current, STATE.captcha, STATE.start_ts = None, None, None, None
            return
        pages = {}
        # 标签页懒开：用到哪个平台才 new_page，避免启动时预开一堆空白标签
        done = 0
        with STATE.lock:
            STATE.start_ts = time.time()
            STATE.done = 0
            STATE.total_kw = len(self.pending)
        try:
            if STATE.parallel:
                # 平台流水线：每个平台一个持久窗口/线程，各自依次跑完本平台全部待跑关键词
                self._kw_left = {kw: len(e) for kw, e in self.pending.items()}
                eng_threads = []
                for eng in sorted(engs_needed):
                    t = threading.Thread(target=self._pipeline_eng, args=(eng, conn), daemon=True)
                    t.start()
                    eng_threads.append(t)
                for t in eng_threads:
                    t.join()
            else:
                for kw, engs in self.pending.items():
                    if STATE.stop_evt.is_set():
                        break
                    while STATE.pause_evt.is_set() and not STATE.stop_evt.is_set():
                        time.sleep(0.3)
                    if STATE.stop_evt.is_set():
                        break
                    if STATE.skip_evt.is_set():
                        STATE.skip_evt.clear()
                    for eng in engs:
                        if STATE.stop_evt.is_set():
                            break
                        self._ensure_page(eng, session, m_session, pages)
                        if self._run_one(kw, eng, session, m_session, pages, conn):
                            break  # 验证码跳过：不再跑该词剩余引擎

                    done += 1
                    with STATE.lock:
                        STATE.done = done
        except Exception as e:
            STATE.log(f"!! 运行异常: {e}")
        finally:
            try:
                if session:
                    session.close()
                if m_session:
                    m_session.close()
                stop_playwright()
            except Exception:
                pass
            with STATE.lock:
                STATE.worker, STATE.current, STATE.captcha, STATE.start_ts = None, None, None, None
        s = db.summary(conn)
        STATE.log(
            f"===== 完成：百度PC命中 {s['baidu_hit']}/{s['total']}，必应PC命中 {s['bing_hit']}/{s['total']}，"
            f"百度移动命中 {s['baidu_m_hit']}/{s['total']}，必应移动命中 {s['bing_m_hit']}/{s['total']} ====="
        )


# ================= HTTP 服务 =================
def _db_rows():
    conn = sqlite3.connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT keyword, baidu_status, baidu_rank, baidu_page, bing_status, bing_rank, bing_page, "
        "baidu_shot, bing_shot, "
        "baidu_m_status, baidu_m_rank, baidu_m_page, baidu_m_shot, "
        "bing_m_status, bing_m_rank, bing_m_page, bing_m_shot "
        "FROM tasks ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def _api_state():
    rows = _db_rows()
    tasks = []
    for kw, bs, br, bp, gs, gr, gp, bsh, gsh, bms, bmr, bmp, bmsh, gms, gmr, gmp, gmsh in rows:
        tasks.append(
            {
                "kw": kw,
                "bd": ST_LABEL.get(bs, bs),
                "bd_rank": br,
                "bd_page": bp,
                "bg": ST_LABEL.get(gs, gs),
                "bg_rank": gr,
                "bg_page": gp,
                "bm": ST_LABEL.get(bms, bms),
                "bm_rank": bmr,
                "bm_page": bmp,
                "gm": ST_LABEL.get(gms, gms),
                "gm_rank": gmr,
                "gm_page": gmp,
                "concl": conclusion(bs, gs, bms, gms),
                "shot_bd": bsh if (bs == "hit" and bsh) else "",
                "shot_bg": gsh if (gs == "hit" and gsh) else "",
                "shot_bm": bmsh if (bms == "hit" and bmsh) else "",
                "shot_gm": gmsh if (gms == "hit" and gmsh) else "",
            }
        )
    hits = [0, 0, 0, 0]
    for t in tasks:
        if t["bd"] == "命中":
            hits[0] += 1
        if t["bg"] == "命中":
            hits[1] += 1
        if t["bm"] == "命中":
            hits[2] += 1
        if t["gm"] == "命中":
            hits[3] += 1
    with STATE.lock:
        running = STATE.worker is not None and STATE.worker.is_alive()
        paused = STATE.pause_evt.is_set()
        current = STATE.current
        captcha = STATE.captcha
        start_ts = STATE.start_ts
        done = STATE.done
        total_kw = STATE.total_kw
    return {
        "running": running,
        "paused": paused,
        "current": current,
        "captcha": captcha,
        "start_ts": start_ts,
        "done": done,
        "total_kw": total_kw,
        "total": len(tasks),
        "hits": hits,
        "tasks": tasks,
        "logs": STATE.snapshot_logs(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 非 200 响应打印到 stderr 便于排查
        if args and args[0] != "200":
            sys.stderr.write(f"[http] {format % args}\n")

    def _handle(self, fn):
        try:
            return fn()
        except Exception as e:
            import traceback

            traceback.print_exc()
            with contextlib.suppress(Exception):
                self._send_json({"error": f"服务端异常: {e}"}, 500)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _hc_worker(self):
        """健康检查线程：跑 run_healthcheck，进度写入日志区"""
        try:
            from healthcheck import run_healthcheck

            run_healthcheck(["baidu", "bing"], captcha_wait=120, log=STATE.log)
            STATE.log("健康检查完成")
        except Exception as e:
            import traceback

            traceback.print_exc()
            STATE.log(f"健康检查异常: {e}")
        finally:
            with STATE.lock:
                STATE.healthcheck = None

    def do_GET(self):
        return self._handle(self._do_GET)

    def _do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send_html(PAGE)
        if path == "/favicon.ico":
            return self._serve_favicon()
        if path == "/favicon.png":
            return self._serve_favicon()
        if path == "/api/state":
            return self._send_json(_api_state())
        if path == "/api/export":
            qs = urlparse(self.path).query
            cols = parse_qs(qs).get("cols", [""])[0] if qs else ""
            return self._export_xlsx(cols or None)
        if path.startswith("/shots/"):
            return self._serve_shot(path[len("/shots/") :])
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        return self._handle(self._do_POST)

    def _do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/start":
            data = self._read_body()
            with STATE.lock:
                if STATE.worker and STATE.worker.is_alive():
                    return self._send_json({"error": "已在运行"})
                if STATE.healthcheck and STATE.healthcheck.is_alive():
                    return self._send_json({"error": "健康检查进行中，请先等它完成"})
            pending = db.load_pending(db.init_db(config.DB_PATH))
            if not pending:
                return self._send_json({"error": "没有待处理关键词"})
            # 按勾选的平台过滤（取消勾选的平台本次不跑，也不开对应浏览器窗口）
            want = [
                str(e).strip()
                for e in (data.get("engines") or [])
                if str(e).strip() in db.ALL_ENGINES
            ]
            if want:
                ws = set(want)
                pending = {
                    kw: [e for e in engs if e in ws]
                    for kw, engs in pending.items()
                    if any(e in ws for e in engs)
                }
                if not pending:
                    return self._send_json({"error": "勾选平台下没有待处理关键词"})
            # 本次上限：填了数字就只跑前 N 个关键词，剩余保持等待、下次可续跑
            limit = 0
            raw = str(data.get("limit", "")).strip()
            if raw:
                try:
                    limit = int(raw)
                except ValueError:
                    limit = 0
            note = ""
            if limit > 0:
                total_pending = len(pending)
                if total_pending > limit:
                    pending = dict(list(pending.items())[:limit])
                    note = f"，本次上限 {limit} 条，剩余 {total_pending - limit} 条保持等待"
                else:
                    note = f"，本次上限 {limit} 条（全部 {total_pending} 条均处理）"
            STATE.stop_evt.clear()
            STATE.pause_evt.clear()
            STATE.skip_evt.clear()
            STATE.parallel = bool(data.get("parallel"))
            w = Worker(pending)
            with STATE.lock:
                STATE.worker = w
            w.start()
            STATE.log(f"开始处理 {len(pending)} 个关键词…{note}")
            return self._send_json({"ok": True, "pending": len(pending)})
        if path == "/api/retry":
            data = self._read_body()
            kw = str(data.get("keyword", "")).strip()
            eng = str(data.get("engine", "")).strip()
            if eng not in ("baidu", "bing", "baidu_m", "bing_m"):
                return self._send_json({"error": "未知平台"})
            conn = db.init_db(config.DB_PATH)
            n = conn.execute("SELECT COUNT(*) FROM tasks WHERE keyword=?", (kw,)).fetchone()[0]
            if not n:
                return self._send_json({"error": "关键词不存在"})
            db.retry_engine(conn, kw, eng)
            STATE.log(f"已重置「{kw}」的 {ENG_LABEL[eng]}，可点开始补跑")
            return self._send_json({"ok": True})
        if path == "/api/pause":
            if STATE.pause_evt.is_set():
                STATE.pause_evt.clear()
                STATE.log("继续")
            else:
                STATE.pause_evt.set()
                STATE.log("暂停（当前词完成后停止）")
            return self._send_json({"ok": True})
        if path == "/api/skip":
            STATE.skip_evt.set()
            STATE.log("将跳过当前关键词…")
            return self._send_json({"ok": True})
        if path == "/api/stop":
            STATE.stop_evt.set()
            STATE.log("正在停止…")
            return self._send_json({"ok": True})
        if path == "/api/healthcheck":
            with STATE.lock:
                if STATE.worker and STATE.worker.is_alive():
                    return self._send_json({"error": "采集任务运行中，请先停止"})
                if STATE.healthcheck and STATE.healthcheck.is_alive():
                    return self._send_json({"error": "健康检查已在进行"})
                t = threading.Thread(target=self._hc_worker, daemon=True)
                STATE.healthcheck = t
                t.start()
            STATE.log("健康检查已启动（真实浏览器验证解析逻辑，约 1-2 分钟）…")
            return self._send_json({"ok": True})
        if path == "/api/import":
            data = self._read_body()
            kws = data.get("keywords", [])
            kws = [str(k).strip() for k in kws if str(k).strip()]
            if not kws:
                return self._send_json({"error": "没有有效关键词"})
            engines = data.get("engines") or list(db.ALL_ENGINES)
            engines = [e for e in engines if e in db.ALL_ENGINES]
            db.ensure_keywords(db.init_db(config.DB_PATH), kws, engines=engines)
            STATE.log(f"已导入 {len(kws)} 个关键词（{'、'.join(ENG_LABEL[e] for e in engines)}）")
            return self._send_json({"ok": True, "count": len(kws)})
        if path == "/api/clear":
            conn = db.init_db(config.DB_PATH)
            conn.execute("DELETE FROM tasks")
            conn.commit()
            STATE.log("任务已清空")
            return self._send_json({"ok": True})
        if path == "/api/shutdown":
            STATE.log("服务即将退出")
            threading.Timer(0.5, _shutdown).start()
            return self._send_json({"ok": True})
        self._send_json({"error": "not found"}, 404)

    def _export_xlsx(self, cols_ids=None):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font

            # 列选择：None/空 = 全选
            if not cols_ids:
                selected = EXPORT_COLS
            else:
                want = {c.strip() for c in cols_ids.split(",") if c.strip()}
                selected = [c for c in EXPORT_COLS if c[0] in want]
            conn = db.init_db(config.DB_PATH)
            rows = conn.execute(
                "SELECT keyword, "
                "baidu_status, baidu_rank, baidu_page, baidu_evidence, baidu_shot, "
                "bing_status, bing_rank, bing_page, bing_evidence, bing_shot, "
                "baidu_m_status, baidu_m_rank, baidu_m_page, baidu_m_evidence, baidu_m_shot, "
                "bing_m_status, bing_m_rank, bing_m_page, bing_m_evidence, bing_m_shot, "
                "updated_at FROM tasks ORDER BY id"
            ).fetchall()
            conn.close()
            wb = Workbook()
            ws0 = wb.active
            if ws0 is not None:
                wb.remove(ws0)

            def _engine_of(cid):
                for pre, name in (
                    ("bd", "百度PC"),
                    ("bg", "必应PC"),
                    ("bm", "百度移动"),
                    ("gm", "必应移动"),
                ):
                    if cid == pre or cid.startswith(pre + "_"):
                        return name
                return None

            def _val(r, col):
                _id, _label, idx, typ, _w = col
                if typ == "kw":
                    return r[0]
                if typ == "st":
                    return ST_LABEL.get(r[idx], r[idx]) or ""
                if typ == "concl":
                    return conclusion(r[1], r[6], r[11], r[16])
                if typ == "shot":
                    return os.path.basename(r[idx] or "") or ""
                return r[idx] or ""

            # 按平台分组生成 sheet
            groups = {}
            for c in selected:
                eng = _engine_of(c[0])
                if eng:
                    groups.setdefault(eng, []).append(c)
            from openpyxl.styles import Alignment, Border, PatternFill, Side
            from openpyxl.styles import Font as XFont

            HEAD_FILL = PatternFill("solid", fgColor="4F6EF2")
            HEAD_FONT = XFont(color="FFFFFF", bold=True, size=11)
            THIN = Side(style="thin", color="D9D9D9")
            BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
            ST_FILL = {
                "hit": ("C6EFCE", "006100"),
                "none": ("F2F2F2", "808080"),
                "error": ("FFC7CE", "9C0006"),
                "restricted": ("FFEB9C", "9C6500"),
                "malfunction": ("E3DFF2", "5B4B8A"),
            }
            REV_LABEL = {v: k for k, v in ST_LABEL.items()}

            def _style_head(wsx, ncol):
                for i in range(1, ncol + 1):
                    c = wsx.cell(row=1, column=i)
                    c.fill = HEAD_FILL
                    c.font = HEAD_FONT
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    c.border = BORDER
                wsx.row_dimensions[1].height = 20

            def _style_data(wsx, nrow, ncol, st_cols=()):
                for r_ in range(2, nrow + 1):
                    for i in range(1, ncol + 1):
                        c = wsx.cell(row=r_, column=i)
                        c.border = BORDER
                        if i == 1:
                            c.alignment = Alignment(vertical="center")
                        else:
                            c.alignment = Alignment(horizontal="center", vertical="center")
                    for i in st_cols:
                        cell = wsx.cell(row=r_, column=i)
                        st = REV_LABEL.get(cell.value, "")
                        if st in ST_FILL:
                            bg, fg = ST_FILL[st]
                            if bg:
                                cell.fill = PatternFill("solid", fgColor=bg)
                            cell.font = XFont(color=fg, bold=True)

            for eng, cols in groups.items():
                ws = wb.create_sheet(eng)
                head = ["关键词"] + [c[1] for c in cols]
                ws.append(head)
                for r in rows:
                    ws.append([r[0]] + [_val(r, c) for c in cols])
                shot_cols = [i + 2 for i, c in enumerate(cols) if c[3] == "shot"]
                for idx, r in enumerate(rows, start=2):
                    for col in shot_cols:
                        shot = r[cols[col - 2][2]]
                        if shot and os.path.exists(shot):
                            ws.cell(row=idx, column=col).hyperlink = os.path.abspath(shot)
                st_cols = [i + 2 for i, c in enumerate(cols) if c[3] == "st"]
                _style_head(ws, len(head))
                _style_data(ws, ws.max_row, len(head), st_cols)
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = f"A1:{chr(64 + len(head)) if len(head) <= 26 else 'A' + chr(64 + len(head) - 26)}{ws.max_row}"

            # 汇总 sheet：勾选的结论/更新时间 + 固定统计
            ws2 = wb.create_sheet("汇总")
            extra = [c for c in selected if c[0] in ("concl", "upd")]
            head2 = [c[1] for c in extra] + (["关键词"] if not extra else [])
            total = len(rows)
            b_hit = sum(1 for r in rows if r[1] == "hit")
            g_hit = sum(1 for r in rows if r[6] == "hit")
            bm_hit = sum(1 for r in rows if r[11] == "hit")
            gm_hit = sum(1 for r in rows if r[16] == "hit")
            any_hit = sum(
                1
                for r in rows
                if r[1] == "hit" or r[6] == "hit" or r[11] == "hit" or r[16] == "hit"
            )
            none = [
                r[0]
                for r in rows
                if r[1] == "none" and r[6] == "none" and r[11] == "none" and r[16] == "none"
            ]
            err = [r[0] for r in rows if "error" in (r[1], r[6], r[11], r[16])]
            if extra:
                ws2.append(head2)
                for c in ws2[1]:
                    c.font = Font(bold=True)
                for r in rows:
                    ws2.append([_val(r, c) for c in extra])
            stat = [
                ["指标", "数值"],
                ["关键词总数", total],
                ["百度PC官网标识命中", b_hit],
                ["必应PC域名命中", g_hit],
                ["百度移动官网标识命中", bm_hit],
                ["必应移动域名命中", gm_hit],
                ["四引擎任一命中", any_hit],
                ["四引擎均未命中（10页内未见）", len(none)],
                ["含错误（验证码跳过等）", len(err)],
                [],
                ["四引擎均未命中关键词", "、".join(none) if none else "无"],
                ["含错误关键词", "、".join(err) if err else "无"],
            ]
            for row in stat:
                ws2.append(row)
            # 汇总样式：表头深底白字，统计区加粗+边框
            if extra:
                _style_head(ws2, len(head2))
                for r_ in range(2, 2 + len(rows)):
                    for i in range(1, len(head2) + 1):
                        c = ws2.cell(row=r_, column=i)
                        c.border = BORDER
                        c.alignment = Alignment(horizontal="center", vertical="center")
                start = 2 + len(rows)
            else:
                start = 1
            for r_ in range(start, ws2.max_row + 1):
                for i in range(1, 3):
                    c = ws2.cell(row=r_, column=i)
                    c.border = BORDER
                    c.alignment = Alignment(vertical="center", horizontal="left")
                ws2.cell(row=r_, column=1).font = XFont(bold=True)
            # 全部 sheet 按内容自动列宽（中文 2 字符，英文 1，上限防超宽）
            for wsx in wb.worksheets:
                cap = 80 if wsx.title == "汇总" else 60
                for col_cells in wsx.columns:
                    mx = 0
                    letter = getattr(col_cells[0], "column_letter", "A")
                    for cell in col_cells:
                        if cell.value is None:
                            continue
                        ln = sum(2 if ord(ch) > 127 else 1 for ch in str(cell.value))
                        mx = max(mx, ln)
                    wsx.column_dimensions[letter].width = min(mx + 2, cap)
            # 汇总 sheet 移到第一个
            if "汇总" in wb.sheetnames and wb.sheetnames[0] != "汇总":
                wb.move_sheet("汇总", offset=-len(wb.sheetnames) + 1)
            import io as _io

            buf = _io.BytesIO()
            wb.save(buf)
            body = buf.getvalue()
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            from urllib.parse import quote as _quote

            fn = _quote("官网检索结果.xlsx")
            self.send_header(
                "Content-Disposition", f"attachment; filename=result.xlsx; filename*=UTF-8''{fn}"
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            STATE.log(f"已导出 Excel（{total} 行）")
        except Exception as e:
            self._send_json({"error": f"导出失败: {e}"}, 500)

    def _serve_favicon(self):
        if not os.path.isfile(FAVICON):
            return self._send_json({"error": "not found"}, 404)
        with open(FAVICON, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_shot(self, name):
        import glob as _glob

        name = os.path.basename(unquote(name))
        hits = _glob.glob(os.path.join(config.SCREENSHOT_DIR, "**", name), recursive=True)
        if not hits or not os.path.isfile(hits[0]):
            return self._send_json({"error": "not found"}, 404)
        with open(hits[0], "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ================= 前端页面 =================
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.png" type="image/png">
<title>官网检索器 · Elo / elotouch.com.cn</title>
<style>
:root{
  --bg:#F4F3EE; --card:#FFFFFF; --card2:#F1F0EA; --line:#E4E3DD;
  --text:#1A1B1C; --sub:#6B7280; --accent:#4E6EF2; --green:#1FA35C;
  --red:#C0392B; --orange:#B9770E; --gray:#8A8F98;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font:14px/1.6 "Microsoft YaHei UI","PingFang SC",Segoe UI,Arial,sans-serif;padding:18px 22px;}
h1{font-size:17px;font-weight:600;display:flex;align-items:center;gap:8px;margin-bottom:14px;}
h1 .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px;}
button{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:8px;
  padding:7px 14px;font-size:13px;cursor:pointer;transition:background .15s;}
button:hover{background:#E9E8E2;}
button:disabled{opacity:.45;cursor:not-allowed;}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;}
button.primary:hover{background:#3D5FD8;}
button.danger{background:transparent;border-color:#E8B4B0;color:var(--red);}
button.danger:hover{background:#FBE9E7;}
.spacer{flex:1;}
.statbar{display:flex;flex-wrap:wrap;gap:18px;background:var(--card);border:1px solid var(--line);
  border-radius:10px;padding:9px 16px;margin-bottom:12px;font-size:13px;color:var(--sub);}
.statbar b{color:var(--text);font-weight:600;}
.statbar .cur{color:var(--accent);}
.statbar .cap{color:var(--red);font-weight:600;}
table{width:100%;border-collapse:collapse;font-size:13px;}
thead th{position:sticky;top:0;background:var(--card2);color:var(--sub);text-align:left;padding:8px 10px;
  cursor:pointer;user-select:none;border-bottom:1px solid var(--line);white-space:nowrap;}
thead th:hover{color:var(--text);}
thead th .arr{color:var(--accent);font-size:11px;}
tbody td{padding:7px 10px;border-bottom:1px solid #ECEAE3;white-space:nowrap;}
tbody tr:nth-child(even){background:rgba(0,0,0,.018);}
tbody tr:hover{background:rgba(78,110,242,.07);}
/* 跑过的行：当前勾选平台全部出结果 → 浅绿底 */
tbody tr.done td{background:rgba(31,163,92,.06);}
tbody tr.done:hover td{background:rgba(31,163,92,.10);}
td.kw{max-width:280px;overflow:hidden;text-overflow:ellipsis;}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px;}
.t-hit{background:rgba(31,163,92,.12);color:var(--green);}
.t-none{background:rgba(138,143,152,.12);color:var(--gray);}
.t-error{background:rgba(192,57,43,.12);color:var(--red);}
.t-restricted{background:rgba(185,119,14,.12);color:var(--orange);}
.t-malfunction{background:rgba(91,75,138,.12);color:#5B4B8A;}
.t-pending{background:rgba(138,143,152,.08);color:var(--sub);}
a.shot{color:var(--accent);text-decoration:none;}
a.shot:hover{text-decoration:underline;}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-top:12px;}
.panel h2{font-size:13px;font-weight:600;color:var(--sub);padding:10px 16px 6px;}
.loghead{display:flex;align-items:center;gap:10px;padding:8px 16px 0;}
.loghead h2{padding:0;flex:1;}
.loghead button{padding:4px 10px;font-size:12px;background:var(--card2);border:1px solid var(--line);border-radius:6px;cursor:pointer;}
#log{height:150px;overflow-y:auto;padding:2px 16px 12px;font:12px/1.7 Consolas,"Cascadia Mono",monospace;color:#3A3F45;white-space:pre-wrap;}
#log .t{color:#A0A5AB;margin-right:8px;}
.wrap{max-width:1180px;margin:0 auto;}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;z-index:50;}
.modal.show{display:flex;}
.mbox{background:var(--card);border:1px solid var(--line);border-radius:12px;width:440px;max-width:92vw;padding:16px;}
.mbox h3{font-size:14px;margin-bottom:10px;}
.mbox textarea{width:100%;height:260px;background:#FFFFFF;color:var(--text);border:1px solid var(--line);
  border-radius:8px;padding:10px;font-size:13px;resize:vertical;}
.mbox .mrow{display:flex;justify-content:flex-end;gap:8px;margin-top:10px;}
input[type=file]{display:none;}
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="dot"></span>官网检索器 <span style="font-size:12px;color:var(--sub);font-weight:400;">百度官网标识 + 必应域名 · 前10页</span></h1>

  <div class="toolbar">
    <button id="btnImportFile">导入文件</button>
    <button id="btnPaste">粘贴关键词</button>
    <button id="btnClear" class="danger">清空</button>
    <input type="file" id="fileInput" accept=".txt,.xlsx,.xls">
    <span style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--sub);">
      本次上限 <input id="inpLimit" type="number" min="1" placeholder="全部"
      style="width:70px;padding:4px 8px;border:1px solid var(--line);border-radius:6px;font-size:13px;background:#fff;color:var(--text);"
      title="只跑前 N 个关键词，跑完自动停止，剩余保持等待"> 条
    </span>
    <span class="spacer"></span>
    <button id="btnStart" class="primary">▶ 开始</button>
    <button id="btnPause" disabled>⏸ 暂停</button>
    <button id="btnSkip" disabled>⏭ 跳过当前</button>
    <button id="btnStop" disabled>⏹ 停止</button>
    <button id="btnExport">导出 Excel</button>
    <button id="btnQuit" class="danger">退出服务</button>
  </div>

  <div class="engbar" style="display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-bottom:10px;font-size:13px;color:var(--sub);">
    <span>本次任务查的平台：</span>
    <label><input type="checkbox" id="chkBaidu" checked> 百度PC</label>
    <label><input type="checkbox" id="chkBing" checked> 必应PC</label>
    <label><input type="checkbox" id="chkBm" checked> 百度移动</label>
    <label><input type="checkbox" id="chkGm" checked> 必应移动</label>
    <label title="一个关键词内，勾选的多个平台同时跑（每个平台一个标签页），词与词之间仍按顺序"><input type="checkbox" id="chkParallel"> 并行模式</label>
    <span style="color:var(--gray);">（勾选决定「导入」给关键词配置哪些平台，以及「开始」时实际跑哪些平台；表格里单个平台出错可点 ↻ 单独补跑）</span>
  </div>

  <div class="statbar">
    <span>进度 <b id="stProg">0 / 0</b></span>
    <span>剩余 <b id="stLeft">--</b></span>
    <span>已用 <b id="stElapsed">--</b></span>
    <span>预计剩余 <b id="stEta">--</b></span>
    <span>百度PC <b id="stBd">0</b></span>
    <span>必应PC <b id="stBg">0</b></span>
    <span>百度移动 <b id="stBm">0</b></span>
    <span>必应移动 <b id="stGm">0</b></span>
    <span>状态 <b id="stCur" class="cur">就绪</b></span>
  </div>

  <div class="panel" style="overflow:auto;max-height:52vh;">
    <table id="tbl">
      <thead><tr>
        <th data-k="kw">关键词</th>
        <th data-k="bd">百度PC</th><th data-k="bg">必应PC</th>
        <th data-k="bm">百度移动</th><th data-k="gm">必应移动</th>
        <th data-k="concl">结论</th><th>PC截图</th><th>移动截图</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <div class="panel">
    <div class="loghead"><h2>日志</h2><button id="btnHC" title="真实浏览器跑样本词，验证解析逻辑是否仍有效（与采集互斥）">🧪 健康检查</button></div>
    <div id="log"></div>
  </div>
</div>

<!-- 粘贴弹窗 -->
<div class="modal" id="modal">
  <div class="mbox">
    <h3>粘贴关键词（每行一个）</h3>
    <textarea id="pasteBox" placeholder="Elo 触摸屏&#10;Elo 医疗触摸屏&#10;…"></textarea>
    <div class="mrow"><button id="pasteCancel">取消</button><button id="pasteOk" class="primary">导入</button></div>
  </div>
</div>

<!-- 导出列选择弹窗 -->
<div class="modal" id="exportModal">
  <div class="mbox" style="max-width:680px;">
    <h3>选择导出列</h3>
    <div id="exportCols" style="max-height:52vh;overflow-y:auto;font-size:13px;color:var(--sub);"></div>
    <div class="mrow">
      <button id="expAll">全选</button><button id="expNone">全不选</button>
      <span class="spacer"></span>
      <button id="exportCancel">取消</button><button id="exportOk" class="primary">导出</button>
    </div>
  </div>
</div>

<script>
(function(){
  var sortKey="id", sortDir=-1, lastTasks="", logSeen=0, lastLogs=0;
  var rowIds={};  // kw -> 原始顺序 index
  var tbl=[];
  var btn={start:by("btnStart"),pause:by("btnPause"),skip:by("btnSkip"),stop:by("btnStop")};
  function by(id){return document.getElementById(id);}

  function api(path,method,body){
    return fetch(path,{method:method||"GET",headers:{"Content-Type":"application/json"},
      body:body?JSON.stringify(body):undefined}).then(function(r){return r.json();});
  }
  function tagCls(s){
    return s==="命中"?"t-hit":s==="未命中"?"t-none":s==="错误"?"t-error":
           s==="受限"?"t-restricted":s==="解析异常"?"t-malfunction":"t-pending";
  }
  /* 勾选的平台 → 表格字段名（bd/bg/bm/gm） */
  function checkedEngs(){
    var r=[];
    if(by("chkBaidu").checked)r.push("bd");
    if(by("chkBing").checked)r.push("bg");
    if(by("chkBm").checked)r.push("bm");
    if(by("chkGm").checked)r.push("gm");
    return r;
  }
  /* 该行是否算「跑过」：勾选的平台都有结果（非等待） */
  function rowDone(t){
    var e=checkedEngs();
    if(!e.length)return false;
    for(var i=0;i<e.length;i++){if(t[e[i]]==="等待")return false;}
    return true;
  }
  /* 全局待跑：勾选平台中至少一个还是「等待」的关键词数 */
  function calcLeft(tasks){
    var e=checkedEngs();
    if(!e.length)return "-";
    var left=0;
    for(var i=0;i<tasks.length;i++){
      var t=tasks[i],ok=0;
      for(var j=0;j<e.length;j++){if(t[e[j]]!=="等待")ok++;}
      if(ok<e.length)left++;
    }
    return left;
  }
  function esc(v){return v==null?"":String(v);}
  function render(){
    var h="";
    var arr=tbl.slice();
    arr.sort(function(a,b){
      var va=a[sortKey], vb=b[sortKey];
      if(va==null)va="";if(vb==null)vb="";
      if(typeof va==="string"&&typeof vb==="string")return va.localeCompare(vb,"zh")*sortDir;
      return (va-vb)*sortDir;
    });
    for(var i=0;i<arr.length;i++){
      var t=arr[i];
      var isDone=rowDone(t);
      function cell(st,rank,page,eng,kw){return "<span class='tag "+tagCls(st)+"'>"+esc(st)+"</span>"+(rank?" <span style='color:var(--sub);font-size:12px'>#"+esc(rank)+" P"+esc(page)+"</span>":"")+" <a href='javascript:void(0)' onclick='retryEng("+JSON.stringify(kw)+","+JSON.stringify(eng)+")' title='单独重跑该平台' style='color:var(--accent);text-decoration:none;font-size:12px;'>↻</a>";}
      function shotLink(p){return p?"<a class='shot' href='/shots/"+encodeURIComponent(p.split(/[\\\\\\/]/).pop())+"' target='_blank'>查看</a>":"";}
      h+="<tr"+(isDone?" class='done'":"")+"><td class='kw' title='"+esc(t.kw)+"'>"+esc(t.kw)+"</td>"
        +"<td>"+cell(t.bd,t.bd_rank,t.bd_page,"baidu",t.kw)+"</td>"
        +"<td>"+cell(t.bg,t.bg_rank,t.bg_page,"bing",t.kw)+"</td>"
        +"<td>"+cell(t.bm,t.bm_rank,t.bm_page,"baidu_m",t.kw)+"</td>"
        +"<td>"+cell(t.gm,t.gm_rank,t.gm_page,"bing_m",t.kw)+"</td>"
        +"<td>"+esc(t.concl)+"</td>"
        +"<td>"+shotLink(t.shot_bd)+" "+shotLink(t.shot_bg)+"</td>"
        +"<td>"+shotLink(t.shot_bm)+" "+shotLink(t.shot_gm)+"</td></tr>";
    }
    by("tbody").innerHTML=h;
  }
  function fmtDur(sec){
    sec=Math.max(0,Math.round(sec));
    var h=Math.floor(sec/3600), m=Math.floor(sec%3600/60), s=sec%60;
    return (h>0?(h+":"):"")+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");
  }
  function poll(){
    fetch("/api/state").then(function(r){return r.json();}).then(function(s){
      var sig=JSON.stringify(s.tasks);
      if(sig!==lastTasks){lastTasks=sig;tbl=s.tasks;render();}
      by("stProg").textContent=s.done+" / "+s.total_kw;
      // 剩余 = 全局待跑关键词数（勾选平台中至少一个还没跑完的），常显
      by("stLeft").textContent=(tbl.length?calcLeft(tbl):"--");
      if(s.running&&s.start_ts){
        var el=Date.now()/1000-s.start_ts;
        var per=el/Math.max(s.done,1);
        by("stElapsed").textContent=fmtDur(el);
        by("stEta").textContent=fmtDur(per*Math.max(s.total_kw-s.done,0));
      }else{
        by("stElapsed").textContent="--";
        by("stEta").textContent="--";
      }
      by("stBd").textContent=s.hits[0];
      by("stBg").textContent=s.hits[1];
      by("stBm").textContent=s.hits[2];
      by("stGm").textContent=s.hits[3];
      var cur=by("stCur");
      if(s.captcha){cur.textContent="⚠ "+s.captcha[0]+" 要求人机验证：请在浏览器窗口完成滑块，自动继续";cur.className="cap";}
      else if(s.current){cur.textContent="处理中: "+s.current[0]+"（"+s.current[1]+"）";cur.className="cur";}
      else if(s.running){cur.textContent="收尾中…";cur.className="cur";}
      else if(s.paused){cur.textContent="已暂停";cur.className="cap";}
      else{cur.textContent="就绪";cur.className="";}
      btn.start.disabled=s.running;
      btn.pause.disabled=!s.running;
      btn.skip.disabled=!s.running;
      btn.stop.disabled=!s.running;
      btn.pause.textContent=s.paused?"▶ 继续":"⏸ 暂停";
      if(s.logs.length>logSeen){
        var log=by("log");
        for(var i=logSeen;i<s.logs.length;i++){
          var d=document.createElement("div");
          var t=document.createElement("span");t.className="t";t.textContent=s.logs[i][0];
          d.appendChild(t);d.appendChild(document.createTextNode(s.logs[i][1]));
          log.appendChild(d);
        }
        logSeen=s.logs.length;log.scrollTop=log.scrollHeight;
      }
    }).catch(function(){}).then(function(){setTimeout(poll,500);});
  }

  function selEngines(){
    var e=[];
    if(by("chkBaidu").checked)e.push("baidu");
    if(by("chkBing").checked)e.push("bing");
    if(by("chkBm").checked)e.push("baidu_m");
    if(by("chkGm").checked)e.push("bing_m");
    return e;
  }
  function retryEng(kw,eng){
    api("/api/retry","POST",{keyword:kw,engine:eng}).then(function(r){if(r.error)alert(r.error);});
  }
  window.retryEng=retryEng;
  btn.start.onclick=function(){
    var lim=by("inpLimit").value.trim();
    api("/api/start","POST",{limit:lim, engines:selEngines(), parallel:by("chkParallel").checked}).then(function(r){if(r.error)alert(r.error);});
  };
  // 勾选平台变化 → 重算行背景与待跑数（表格行是否「跑过」随勾选变化）
  ["chkBaidu","chkBing","chkBm","chkGm"].forEach(function(id){
    by(id).addEventListener("change",function(){
      render();
      by("stLeft").textContent=calcLeft(tbl);
    });
  });
  btn.pause.onclick=function(){api("/api/pause","POST");};
  btn.skip.onclick=function(){api("/api/skip","POST");};
  btn.stop.onclick=function(){api("/api/stop","POST");};
  by("btnExport").onclick=function(){renderExportCols();by("exportModal").classList.add("show");};
  by("btnHC").onclick=function(){
    if(by("btnHC").disabled)return;
    api("/api/healthcheck","POST",{}).then(function(r){
      if(r.error){alert(r.error);return;}
    });
  };
  by("exportCancel").onclick=function(){by("exportModal").classList.remove("show");};
  by("expAll").onclick=function(){document.querySelectorAll("#exportCols input").forEach(function(i){i.checked=true;});};
  by("expNone").onclick=function(){document.querySelectorAll("#exportCols input").forEach(function(i){i.checked=false;});};
  by("exportOk").onclick=function(){
    var types=[];
    document.querySelectorAll("#exportCols input:checked").forEach(function(i){types.push(i.value);});
    if(!types.length){alert("至少选择一项");return;}
    var map={"kw":["kw"],"st":["bd","bg","bm","gm"],"rank":["bd_rank","bg_rank","bm_rank","gm_rank"],
             "page":["bd_page","bg_page","bm_page","gm_page"],"ev":["bd_ev","bg_ev","bm_ev","gm_ev"],
             "shot":["bd_shot","bg_shot","bm_shot","gm_shot"],"concl":["concl"],"upd":["upd"]};
    var ids=[];
    types.forEach(function(t){ids=ids.concat(map[t]||[]);});
    localStorage.setItem("expCols", types.join(","));
    by("exportModal").classList.remove("show");
    location.href="/api/export?cols="+encodeURIComponent(ids.join(","));
  };
  function renderExportCols(){
    var types=[
      ["关键词",[["kw","关键词"]]],
      ["导出字段（勾选后四个平台都包含）",[["st","状态"],["rank","排名"],["page","页码"],["ev","依据"],["shot","截图"]]],
      ["其他",[["concl","结论"],["upd","更新时间"]]]
    ];
    var saved=(localStorage.getItem("expCols")||"").split(",").filter(Boolean);
    // 兼容旧格式：把旧的列 id（如 bd_rank/bg）映射成字段类型
    function oldToType(id){
      if(id==="kw"||id==="concl"||id==="upd")return id;
      if(id.indexOf("_rank")>=0)return "rank";
      if(id.indexOf("_page")>=0)return "page";
      if(id.indexOf("_ev")>=0)return "ev";
      if(id.indexOf("_shot")>=0)return "shot";
      return "st";
    }
    var savedT={};
    saved.forEach(function(id){savedT[oldToType(id)]=1;});
    delete savedT["ev"];  // 依据默认不要
    var defaults={kw:1,st:1,rank:1,page:1,shot:1,concl:1,upd:1};
    var h="";
    types.forEach(function(g){
      h+="<div style='margin:8px 0 4px;font-weight:600;color:var(--text);'>"+g[0]+"</div><div style='display:flex;flex-wrap:wrap;gap:4px 14px;'>";
      g[1].forEach(function(c){
        var on=(saved.length? savedT[c[0]] : defaults[c[0]])===1;
        h+="<label style='display:flex;align-items:center;gap:4px;'><input type='checkbox' value='"+c[0]+"'"+(on?" checked":"")+"> "+c[1]+"</label>";
      });
      h+="</div>";
    });
    by("exportCols").innerHTML=h;
  }
  by("btnClear").onclick=function(){
    if(confirm("清空全部任务数据？（关键词、结果、进度都会删除）"))api("/api/clear","POST");
  };
  by("btnQuit").onclick=function(){
    if(confirm("退出官网检索器服务？\n（正在跑的任务数据已落库，下次运行可续跑）")){
      api("/api/shutdown","POST");
      setTimeout(function(){document.body.innerHTML="<div style='padding:40px;text-align:center;color:#6B7280'>服务已退出，可以关闭本标签页</div>";},600);
    }
  };
  by("btnImportFile").onclick=function(){by("fileInput").click();};
  by("fileInput").onchange=function(){
    var f=this.files[0];if(!f)return;
    var rd=new FileReader();
    rd.onload=function(){
      var text=rd.result, kws=[];
      if(f.name.toLowerCase().endsWith(".txt")){
        kws=text.split(/\r?\n/).map(function(s){return s.trim();}).filter(Boolean);
      }else{
        alert("Excel 请先转成 txt（每行一个关键词）再导入，或直接用粘贴。");
        return;
      }
      api("/api/import","POST",{keywords:kws,engines:selEngines()}).then(function(r){if(r.error)alert(r.error);});
    };
    rd.readAsText(f,"utf-8");
    this.value="";
  };
  by("btnPaste").onclick=function(){by("modal").classList.add("show");by("pasteBox").focus();};
  by("pasteCancel").onclick=function(){by("modal").classList.remove("show");};
  by("pasteOk").onclick=function(){
    var kws=by("pasteBox").value.split(/\r?\n/).map(function(s){return s.trim();}).filter(Boolean);
    by("pasteBox").value="";
    by("modal").classList.remove("show");
    api("/api/import","POST",{keywords:kws,engines:selEngines()}).then(function(r){if(r.error)alert(r.error);});
  };

  document.querySelectorAll("thead th[data-k]").forEach(function(th){
    th.onclick=function(){
      var k=th.getAttribute("data-k");
      if(sortKey===k)sortDir*=-1;else{sortKey=k;sortDir=1;}
      render();
    };
  });

  poll();
})();
</script>
</body>
</html>
"""


HTTPD = None


def _shutdown():
    if HTTPD:
        HTTPD.shutdown()


def main():
    global HTTPD
    no_browser = "--no-browser" in sys.argv
    os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)
    db.init_db(config.DB_PATH)
    _append_log_file("")
    _append_log_file("=" * 64)
    _append_log_file(f"== 官网检索器启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ==")
    HTTPD = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"官网检索器已启动: {url}")
    print("按 Ctrl+C 停止服务（正在跑的任务数据已落库，重启可续跑）")
    if not no_browser:
        webbrowser.open(url)
    try:
        HTTPD.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
