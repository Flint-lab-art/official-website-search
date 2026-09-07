# -*- coding: utf-8 -*-
"""官网检索器 Web 面板（本地服务 + 浏览器界面）
启动: python server.py  → 自动打开 http://127.0.0.1:27531
采集核心复用 core/（Playwright + SQLite + 判定），仅替换展示层。
"""
import os, sys, io, json, queue, threading, time, sqlite3, webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# pythonw.exe 下 stdout/stderr 为 None，print 会崩溃——给哑对象兜底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from core import config, db
from core.engine import BrowserSession
from core.baidu import run_baidu
from core.bing import run_bing

PORT = 27531
ST_LABEL = {"pending": "等待", "hit": "命中", "none": "未命中", "error": "错误"}


def conclusion(bd, bg):
    b, g = bd == "hit", bg == "hit"
    if b and g:
        return "官网可见"
    if b or g:
        return "部分可见"
    if bd == "error" or bg == "error":
        return "含错误"
    return "10页未见"


# ================= 状态与日志 =================
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.worker = None
        self.pause_evt = threading.Event()
        self.stop_evt = threading.Event()
        self.skip_evt = threading.Event()
        self.current = None      # (kw, engine)
        self.captcha = None      # (engine, kw)
        self.logs = deque(maxlen=800)

    def log(self, text):
        with self.lock:
            self.logs.append((time.strftime("%H:%M:%S"), text))

    def snapshot_logs(self, since=0):
        with self.lock:
            items = list(self.logs)[since:]
        return items


STATE = State()


# ================= 采集 Worker =================
class Worker(threading.Thread):
    def __init__(self, keywords):
        super().__init__(daemon=True)
        self.keywords = keywords

    def notify_captcha(self, engine, keyword):
        with STATE.lock:
            STATE.captcha = (engine, keyword)
        STATE.log(f"[验证码] {engine}「{keyword}」请在浏览器窗口手动完成验证")

    def run(self):
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        session = BrowserSession()
        # 两个引擎各固定一个标签页复用，避免每次新建标签
        bd_page = session.new_page()
        bg_page = session.new_page()
        done = 0
        try:
            for kw in self.keywords:
                if STATE.stop_evt.is_set():
                    break
                while STATE.pause_evt.is_set() and not STATE.stop_evt.is_set():
                    time.sleep(0.3)
                if STATE.stop_evt.is_set():
                    break
                if STATE.skip_evt.is_set():
                    STATE.skip_evt.clear()

                with STATE.lock:
                    STATE.current, STATE.captcha = (kw, "百度"), None
                STATE.log(f"▶ {kw}")

                r = run_baidu(session, kw, config.SCREENSHOT_DIR,
                              skip_evt=STATE.skip_evt, notify=self.notify_captcha,
                              page=bd_page)
                db.update_result(conn, kw, "baidu", r["status"],
                                 r.get("rank"), r.get("page"),
                                 r.get("evidence"), r.get("shot"))
                STATE.log(f"   百度 {ST_LABEL.get(r['status'], r['status'])} "
                          f"排名{r.get('rank')} 第{r.get('page')}页 | {r.get('evidence', '')[:50]}")

                if STATE.skip_evt.is_set():
                    STATE.skip_evt.clear()
                    db.update_result(conn, kw, "bing", "error", evidence="验证码跳过")
                    STATE.log("   已跳过（验证码）")
                else:
                    with STATE.lock:
                        STATE.current, STATE.captcha = (kw, "必应"), None
                    g = run_bing(session, kw, config.SCREENSHOT_DIR, page=bg_page)
                    db.update_result(conn, kw, "bing", g["status"],
                                     g.get("rank"), g.get("page"),
                                     g.get("evidence"), g.get("shot"))
                    STATE.log(f"   必应 {ST_LABEL.get(g['status'], g['status'])} "
                              f"排名{g.get('rank')} 第{g.get('page')}页 | {g.get('evidence', '')[:50]}")

                done += 1
        except Exception as e:
            STATE.log(f"!! 运行异常: {e}")
        finally:
            try:
                session.close()
            except Exception:
                pass
            with STATE.lock:
                STATE.worker, STATE.current, STATE.captcha = None, None, None
        s = db.summary(conn)
        STATE.log(f"===== 完成：百度命中 {s['baidu_hit']}/{s['total']}，必应命中 {s['bing_hit']}/{s['total']} =====")


# ================= HTTP 服务 =================
def _db_rows():
    conn = sqlite3.connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT keyword, baidu_status, baidu_rank, baidu_page, bing_status, bing_rank, bing_page, "
        "baidu_shot, bing_shot FROM tasks ORDER BY id").fetchall()
    conn.close()
    return rows


def _api_state():
    rows = _db_rows()
    tasks = []
    for kw, bs, br, bp, gs, gr, gp, bsh, gsh in rows:
        tasks.append({
            "kw": kw, "bd": ST_LABEL.get(bs, bs), "bd_rank": br, "bd_page": bp,
            "bg": ST_LABEL.get(gs, gs), "bg_rank": gr, "bg_page": gp,
            "concl": conclusion(bs, gs),
            "shot_bd": bsh if (bs == "hit" and bsh) else "",
            "shot_bg": gsh if (gs == "hit" and gsh) else "",
        })
    hits = [0, 0]
    for t in tasks:
        if t["bd"] == "命中":
            hits[0] += 1
        if t["bg"] == "命中":
            hits[1] += 1
    with STATE.lock:
        running = STATE.worker is not None and STATE.worker.is_alive()
        paused = STATE.pause_evt.is_set()
        current = STATE.current
        captcha = STATE.captcha
    return {
        "running": running, "paused": paused,
        "current": current, "captcha": captcha,
        "total": len(tasks),
        "hits": hits,
        "tasks": tasks,
        "logs": STATE.snapshot_logs(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 非 200 响应打印到 stderr 便于排查
        if args and args[0] != "200":
            sys.stderr.write(f"[http] {fmt % args}\n")

    def _handle(self, fn):
        try:
            return fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send_json({"error": f"服务端异常: {e}"}, 500)
            except Exception:
                pass

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

    def do_GET(self):
        return self._handle(self._do_GET)

    def _do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send_html(PAGE)
        if path == "/api/state":
            return self._send_json(_api_state())
        if path == "/api/export":
            return self._export_xlsx()
        if path.startswith("/shots/"):
            return self._serve_shot(path[len("/shots/"):])
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        return self._handle(self._do_POST)

    def _do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/start":
            with STATE.lock:
                if STATE.worker and STATE.worker.is_alive():
                    return self._send_json({"error": "已在运行"})
            pending = db.load_pending(db.init_db(config.DB_PATH))
            if not pending:
                return self._send_json({"error": "没有待处理关键词"})
            STATE.stop_evt.clear()
            STATE.pause_evt.clear()
            STATE.skip_evt.clear()
            w = Worker(pending)
            with STATE.lock:
                STATE.worker = w
            w.start()
            STATE.log(f"开始处理 {len(pending)} 个关键词…")
            return self._send_json({"ok": True, "pending": len(pending)})
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
        if path == "/api/import":
            data = self._read_body()
            kws = data.get("keywords", [])
            kws = [str(k).strip() for k in kws if str(k).strip()]
            if not kws:
                return self._send_json({"error": "没有有效关键词"})
            db.ensure_keywords(db.init_db(config.DB_PATH), kws)
            STATE.log(f"已导入 {len(kws)} 个关键词")
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

    def _export_xlsx(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            conn = db.init_db(config.DB_PATH)
            rows = conn.execute(
                "SELECT keyword, baidu_status, baidu_rank, baidu_page, baidu_evidence, baidu_shot, "
                "bing_status, bing_rank, bing_page, bing_evidence, bing_shot, updated_at "
                "FROM tasks ORDER BY id").fetchall()
            conn.close()
            wb = Workbook()
            ws = wb.active
            ws.title = "明细"
            head = ["关键词", "百度", "百度排名", "百度页码", "百度依据", "百度截图",
                    "必应", "必应排名", "必应页码", "必应依据", "必应截图", "结论", "更新时间"]
            ws.append(head)
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in rows:
                kw, bs, br, bp, be, bsh, gs, gr, gp, ge, gsh, up = r
                ws.append([kw, ST_LABEL.get(bs, bs), br or "", bp or "", be or "", bsh or "",
                           ST_LABEL.get(gs, gs), gr or "", gp or "", ge or "", gsh or "",
                           conclusion(bs, gs), up])
            for idx, r in enumerate(rows, start=2):
                for col in (6, 11):
                    shot = r[col - 2]
                    if shot and os.path.exists(shot):
                        ws.cell(row=idx, column=col).hyperlink = os.path.abspath(shot)
            for i, w in enumerate([220, 60, 55, 55, 260, 160, 60, 55, 55, 260, 160, 90, 140], 1):
                ws.column_dimensions[chr(64 + i)].width = w
            ws2 = wb.create_sheet("汇总")
            total = len(rows)
            b_hit = sum(1 for r in rows if r[1] == "hit")
            g_hit = sum(1 for r in rows if r[6] == "hit")
            both = sum(1 for r in rows if r[1] == "hit" and r[6] == "hit")
            none = [r[0] for r in rows if r[1] == "none" and r[6] == "none"]
            err = [r[0] for r in rows if r[1] == "error" or r[6] == "error"]
            for row in [["指标", "数值"], ["关键词总数", total], ["百度官网标识命中", b_hit],
                        ["必应域名命中", g_hit], ["双引擎均命中", both],
                        ["双引擎均未命中（10页内未见）", len(none)],
                        ["含错误（验证码跳过等）", len(err)], [],
                        ["双引擎均未命中关键词", "、".join(none) if none else "无"],
                        ["含错误关键词", "、".join(err) if err else "无"]]:
                ws2.append(row)
            for c in ws2[1]:
                c.font = Font(bold=True)
            ws2.column_dimensions["A"].width = 26
            ws2.column_dimensions["B"].width = 80
            import io as _io
            buf = _io.BytesIO()
            wb.save(buf)
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", "attachment; filename=官网检索结果.xlsx")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            STATE.log(f"已导出 Excel（{total} 行）")
        except Exception as e:
            self._send_json({"error": f"导出失败: {e}"}, 500)

    def _serve_shot(self, name):
        name = os.path.basename(unquote(name))
        path = os.path.join(config.SCREENSHOT_DIR, name)
        if not os.path.exists(path):
            return self._send_json({"error": "not found"}, 404)
        with open(path, "rb") as f:
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
td.kw{max-width:280px;overflow:hidden;text-overflow:ellipsis;}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px;}
.t-hit{background:rgba(31,163,92,.12);color:var(--green);}
.t-none{background:rgba(138,143,152,.12);color:var(--gray);}
.t-error{background:rgba(192,57,43,.12);color:var(--red);}
.t-pending{background:rgba(138,143,152,.08);color:var(--sub);}
a.shot{color:var(--accent);text-decoration:none;}
a.shot:hover{text-decoration:underline;}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-top:12px;}
.panel h2{font-size:13px;font-weight:600;color:var(--sub);padding:10px 16px 6px;}
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
  <h1><span class="dot"></span>官网检索器 <span style="font-size:12px;color:var(--sub);font-weight:400;">Elo / elotouch.com.cn · 百度官网标识 + 必应域名 · 前10页</span></h1>

  <div class="toolbar">
    <button id="btnImportFile">导入文件</button>
    <button id="btnPaste">粘贴关键词</button>
    <button id="btnClear" class="danger">清空</button>
    <input type="file" id="fileInput" accept=".txt,.xlsx,.xls">
    <span class="spacer"></span>
    <button id="btnStart" class="primary">▶ 开始</button>
    <button id="btnPause" disabled>⏸ 暂停</button>
    <button id="btnSkip" disabled>⏭ 跳过当前</button>
    <button id="btnStop" disabled>⏹ 停止</button>
    <button id="btnExport">导出 Excel</button>
    <button id="btnQuit" class="danger">退出服务</button>
  </div>

  <div class="statbar">
    <span>进度 <b id="stProg">0 / 0</b></span>
    <span>百度命中 <b id="stBd">0</b></span>
    <span>必应命中 <b id="stBg">0</b></span>
    <span>状态 <b id="stCur" class="cur">就绪</b></span>
  </div>

  <div class="panel" style="overflow:auto;max-height:52vh;">
    <table id="tbl">
      <thead><tr>
        <th data-k="kw">关键词</th><th data-k="bd">百度</th><th data-k="bd_rank">排名</th><th data-k="bd_page">页码</th>
        <th data-k="bg">必应</th><th data-k="bg_rank">排名</th><th data-k="bg_page">页码</th>
        <th data-k="concl">结论</th><th>百度截图</th><th>必应截图</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

  <div class="panel">
    <h2>日志</h2>
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
    return s==="命中"?"t-hit":s==="未命中"?"t-none":s==="错误"?"t-error":"t-pending";
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
      h+="<tr><td class='kw' title='"+esc(t.kw)+"'>"+esc(t.kw)+"</td>"
        +"<td><span class='tag "+tagCls(t.bd)+"'>"+esc(t.bd)+"</span></td><td>"+esc(t.bd_rank)+"</td><td>"+esc(t.bd_page)+"</td>"
        +"<td><span class='tag "+tagCls(t.bg)+"'>"+esc(t.bg)+"</span></td><td>"+esc(t.bg_rank)+"</td><td>"+esc(t.bg_page)+"</td>"
        +"<td>"+esc(t.concl)+"</td>"
        +"<td>"+(t.shot_bd?"<a class='shot' href='/shots/"+encodeURIComponent(t.shot_bd.split(/[\\\\\\/]/).pop())+"' target='_blank'>查看</a>":"")+"</td>"
        +"<td>"+(t.shot_bg?"<a class='shot' href='/shots/"+encodeURIComponent(t.shot_bg.split(/[\\\\\\/]/).pop())+"' target='_blank'>查看</a>":"")+"</td></tr>";
    }
    by("tbody").innerHTML=h;
  }
  function poll(){
    fetch("/api/state").then(function(r){return r.json();}).then(function(s){
      var sig=JSON.stringify(s.tasks);
      if(sig!==lastTasks){lastTasks=sig;tbl=s.tasks;render();}
      by("stProg").textContent=(s.total-(s.tasks.filter(function(t){return t.bd!=="等待"&&t.bg!=="等待";}).length))+" / "+s.total;
      by("stBd").textContent=s.hits[0];
      by("stBg").textContent=s.hits[1];
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

  btn.start.onclick=function(){api("/api/start","POST").then(function(r){if(r.error)alert(r.error);});};
  btn.pause.onclick=function(){api("/api/pause","POST");};
  btn.skip.onclick=function(){api("/api/skip","POST");};
  btn.stop.onclick=function(){api("/api/stop","POST");};
  by("btnExport").onclick=function(){location.href="/api/export";};
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
      api("/api/import","POST",{keywords:kws}).then(function(r){if(r.error)alert(r.error);});
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
    api("/api/import","POST",{keywords:kws}).then(function(r){if(r.error)alert(r.error);});
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
