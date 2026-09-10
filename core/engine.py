"""浏览器会话管理：持久化用户目录（cookie 复用）+ 验证码人工处理"""

import contextlib
import os
import random
import shutil
import threading
import time

from playwright.sync_api import BrowserContext, sync_playwright

from . import config


def scroll_trigger(page):
    """截图前滚动到底部再回顶，触发底部元素（页码条等）懒加载渲染"""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:
        pass


def wait_render_ready(page, selector="li.b_algo"):
    """截图前等页面真实渲染完成。
    必应结果区为异步渲染：DOM 出现（可判定命中）≠ 已绘制，截图太早会白屏。
    等元素可见 + 网络空闲 + 短延时后返回。"""
    with contextlib.suppress(Exception):
        page.wait_for_selector(selector, state="visible", timeout=8000)
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=8000)
    page.wait_for_timeout(1200)


def wait_page_stable(page, stable_ms=300, timeout_ms=12000):
    """等页面渲染稳定：li.b_algo 数量与页面总高度在 stable_ms 内不再变化。
    Bing 结果流式注入，DOM 出现 ≠ 渲染完；数量/高度稳定后才可截图。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_ms:
        try:
            n = page.evaluate("document.querySelectorAll('li.b_algo').length")
            h = page.evaluate("document.documentElement.scrollHeight")
        except Exception:
            return False
        cur = (n, h)
        if cur == last and n > 0:
            page.wait_for_timeout(stable_ms)
            try:
                n2 = page.evaluate("document.querySelectorAll('li.b_algo').length")
                h2 = page.evaluate("document.documentElement.scrollHeight")
            except Exception:
                return False
            if (n2, h2) == cur:
                return True
        last = cur
        page.wait_for_timeout(200)
    return False


def kill_content_visibility(page):
    """只处理 content-visibility:auto 的元素：强制 visible + contain:none。
    这是必应页码条/视口外模块「只留占位不渲染」的唯一根因处理。
    不做全文档 visibility/display/opacity 兜底——否则会把必应隐藏元素
    （如屏幕阅读器专用的 h4.b_hide「分页」标题、功能提示条）误显示进截图。"""
    with contextlib.suppress(Exception):
        page.evaluate(
            """() => {
                document.querySelectorAll('*').forEach(el => {
                    if (getComputedStyle(el).contentVisibility === 'auto') {
                        el.style.contentVisibility = 'visible';
                        el.style.contain = 'none';
                    }
                });
            }"""
        )
        page.wait_for_timeout(150)


def disable_animations_and_unstick(page):
    """注入 CSS：fixed/sticky 转 static（避免整页截图里重复绘制或只画在顶部）
    + 禁用 transition/animation（避免截到中间帧）。"""
    with contextlib.suppress(Exception):
        page.add_style_tag(content="""
            *[style*="position: fixed"],
            *[style*="position: sticky"],
            header, #b_header, .b_header, #sb_form_contain, #b_sydTiger {
                position: static !important;
            }
            * { transition: none !important; animation: none !important; }
        """)


def warm_up(page):
    """滚到底触发懒加载，再回顶。"""
    with contextlib.suppress(Exception):
        page.evaluate(
            """async () => {
                const d = ms => new Promise(r => setTimeout(r, ms));
                const total = () => Math.max(
                    document.body.scrollHeight, document.documentElement.scrollHeight);
                let y = 0;
                while (y < total()) {
                    window.scrollTo(0, y); await d(120);
                    y += window.innerHeight * 0.8;
                }
                window.scrollTo(0, total()); await d(300);
                window.scrollTo(0, 0); await d(150);
            }"""
        )


def wait_resources(page):
    """等所有图片加载完成 + 字体就绪 + 双 rAF（确保绘制提交到合成器帧）。"""
    with contextlib.suppress(Exception):
        page.evaluate(
            """() => Promise.all(Array.from(document.images).map(i =>
                i.complete ? Promise.resolve() : new Promise(r => { i.onload = i.onerror = r; })
            ))"""
        )
    with contextlib.suppress(Exception):
        page.evaluate("() => document.fonts.ready.then(() => true)")
    with contextlib.suppress(Exception):
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame("
            "() => requestAnimationFrame(() => r(true))))"
        )
    page.wait_for_timeout(150)


def cdp_full_screenshot(page, path, max_height=16000):
    """CDP captureBeyondViewport 整页截图；超过单张上限（约 16384）时分段拼接。
    与 page.screenshot(full_page=True) 相比，clip 坐标/视口解释更稳定。"""
    import base64
    import io

    m = page.evaluate(
        """() => {
            const b = document.body, e = document.documentElement;
            return {
                width:  Math.max(b.scrollWidth,  e.scrollWidth,  window.innerWidth),
                height: Math.max(b.scrollHeight, e.scrollHeight, window.innerHeight)
            };
        }"""
    )
    # 截图前强制合成器提交最新帧（偶发空白：fromSurface 抓到未提交的表面）
    with contextlib.suppress(Exception):
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame("
            "() => requestAnimationFrame(() => r(true))))"
        )
    page.wait_for_timeout(250)

    cdp = page.context.new_cdp_session(page)
    try:

        def _shot(y, h):
            return cdp.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                    "fromSurface": True,
                    "clip": {"x": 0, "y": y, "width": m["width"], "height": h, "scale": 1},
                },
            )["data"]

        if m["height"] <= max_height:
            with open(path, "wb") as f:
                f.write(base64.b64decode(_shot(0, m["height"])))
        else:
            from PIL import Image

            parts, y = [], 0
            while y < m["height"]:
                h = min(max_height, m["height"] - y)
                parts.append(Image.open(io.BytesIO(base64.b64decode(_shot(y, h)))))
                y += h
            canvas = Image.new("RGB", (parts[0].width, sum(p.height for p in parts)), "white")
            off = 0
            for p in parts:
                canvas.paste(p, (0, off))
                off += p.height
            canvas.save(path)
    finally:
        cdp.detach()


def is_captcha(page, engine):
    """判断当前页是否为验证码页"""
    url = page.url
    title = page.title()
    if engine == "baidu":
        if "wappass.baidu.com" in url:
            return True
        if "百度安全验证" in title or "安全验证" in title:
            return True
    return False


def wait_for_captcha(page, engine, keyword, skip_evt=None, timeout_s=1800, notify=None):
    """出现验证码时暂停，提示用户手动完成。
    自动轮询页面：用户完成滑块后页面跳回结果页，立即继续。
    默认等待 30 分钟（人工验证可能需要较长时间）。
    skip_evt 被触发（GUI 点「跳过当前词」）时返回 'skip'。
    notify(engine, keyword) 用于 GUI 提示。"""
    if notify:
        notify(engine, keyword)
    print("\n" + "=" * 56)
    print(f"[验证码] {engine} 搜索「{keyword}」时要求人机验证。")
    print("浏览器窗口已打开，请在窗口里完成滑块/点选验证。")
    print("完成后脚本会自动检测到并继续，无需任何操作。")
    print("=" * 56)
    waited = 0
    while True:
        time.sleep(3)
        waited += 3
        if skip_evt is not None and skip_evt.is_set():
            print("[验证码] 已跳过该词。")
            return "skip"
        try:
            if not is_captcha(page, engine):
                print("[验证码] 已通过，继续。")
                return "ok"
        except Exception:
            pass
        if waited >= timeout_s:
            waited = 0
            print("[验证码] 仍未完成，请在浏览器窗口继续操作…")


def _clean_session_files(profile_dir):
    """清理 Chromium 上次异常退出残留的标签页会话文件（不影响 cookie/登录态）"""
    for name in ("Current Session", "Last Session", "Current Tabs", "Last Tabs"):
        p = os.path.join(profile_dir, name)
        if os.path.exists(p):
            with contextlib.suppress(Exception):
                os.remove(p)


_PW = None
_PW_TID = None


def _pw_singleton():
    """Playwright 实例单例：Sync API 绑定创建线程，跨线程使用会崩。
    若实例由其他线程（如已退出的健康检查线程）创建，自动废弃重建。"""
    global _PW, _PW_TID
    tid = threading.get_ident()
    if _PW is not None and tid != _PW_TID:
        with contextlib.suppress(Exception):
            _PW.stop()
        _PW = None
    if _PW is None:
        _PW = sync_playwright().start()
        _PW_TID = tid
    return _PW


def stop_playwright():
    global _PW, _PW_TID
    if _PW is not None:
        with contextlib.suppress(Exception):
            _PW.stop()
        _PW = None
        _PW_TID = None


def par_profile_dir(eng):
    """并行模式独立 profile：首次从主 profile 复制（继承 cookie/登录态），之后自更新。
    并行实例若共用主 profile 目录会触发 Chromium 目录锁冲突（同时启动必崩）。"""
    base = config.PROFILE_DIR if eng in ("baidu", "bing") else config.MOBILE_PROFILE_DIR
    par = f"{base}_{eng}"
    if not os.path.exists(par) and os.path.exists(base):
        with contextlib.suppress(Exception):
            shutil.copytree(base, par)
    return par


class BrowserSession:
    def __init__(self, profile_dir=None, mobile=False, own_pw=False):
        self._own = own_pw
        if own_pw:
            # 独立 playwright 实例：sync API 线程绑定，必须在线程内 start/使用/stop
            self._pw = sync_playwright().start()
        else:
            self._pw = _pw_singleton()
        profile = profile_dir or (config.MOBILE_PROFILE_DIR if mobile else config.PROFILE_DIR)
        _clean_session_files(profile)
        kw = {
            "headless": False,  # 必须显示窗口（用户人工验证）
            "locale": "zh-CN",
            "args": ["--start-maximized", "--disable-session-crashed-bubble", "--no-first-run"],
        }
        if mobile:
            kw["user_agent"] = config.MOBILE_USER_AGENT
            kw["viewport"] = config.MOBILE_VIEWPORT
            kw["device_scale_factor"] = config.MOBILE_DSF
            kw["is_mobile"] = True
            kw["has_touch"] = True
        else:
            kw["user_agent"] = config.USER_AGENT
            kw["viewport"] = config.VIEWPORT
        self.ctx: BrowserContext = self._pw.chromium.launch_persistent_context(profile, **kw)
        # 注意：不能关闭启动自带的默认空白页——关掉最后一个标签页会导致整个浏览器窗口关闭，
        # 后续 new_page 会报 "Failed to open a new tab"。默认页由调用方复用（见 server.py）。
        self.pages = []

    def new_page(self):
        pg = self.ctx.new_page()
        self.pages.append(pg)
        return pg

    def ensure_cookie_size(self):
        """任务开始前调用：Cookie 体积超限时自动裁剪（保留核心身份、丢追踪域）。
        正常情况零成本；避免 header 超限被搜索引擎拒绝，减少「只能重启清 Cookie」的场景。"""
        from .cookie_tools import ensure_cookie_size as _ensure

        with contextlib.suppress(Exception):
            _ensure(self.ctx)

    def close_page(self, pg):
        with contextlib.suppress(Exception):
            pg.close()

    def random_delay(self):
        time.sleep(random.randint(config.DELAY_MIN_MS, config.DELAY_MAX_MS) / 1000)

    def close(self):
        with contextlib.suppress(Exception):
            self.ctx.close()
        if self._own:
            with contextlib.suppress(Exception):
                self._pw.stop()
        # 非 own：不 stop 共享 _pw（由 stop_playwright() 统一收尾）
