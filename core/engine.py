# -*- coding: utf-8 -*-
"""浏览器会话管理：持久化用户目录（cookie 复用）+ 验证码人工处理"""
import os, random, time, sys
from playwright.sync_api import sync_playwright

from . import config

# 引擎 → 面板显示名（用于截图页码水印）
ENGINE_LABEL = {"baidu": "百度PC", "baidu_m": "百度移动", "bing": "必应PC", "bing_m": "必应移动"}


def scroll_trigger(page):
    """截图前滚动到底部再回顶，触发底部元素（页码条等）懒加载渲染"""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:
        pass


def stamp_page_mark(path, engine, pn):
    """在截图底部叠加仿原生页码条（引擎名 + 1/2/3/… 当前页高亮），
    保证受限页等无原生页码条的截图也能看出命中页码"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return
    try:
        img = Image.open(path).convert("RGB")
        w, h = img.size
        fs = max(18, int(w / 45))
        font_path = r"C:\Windows\Fonts\msyh.ttc"
        try:
            font = ImageFont.truetype(font_path, fs)
        except Exception:
            font = ImageFont.load_default()
        label = f"{ENGINE_LABEL.get(engine, engine)}"
        d = ImageDraw.Draw(img)
        pad = int(fs * 0.7)
        # 页码块：1..min(5, pn)，当前页高亮
        nums = list(range(1, min(5, pn) + 1))
        lw = d.textlength(label, font=font)
        cell_w = int(fs * 1.9)
        gap = int(fs * 0.35)
        bar_w = pad * 2 + lw + gap + len(nums) * cell_w + gap * max(0, len(nums) - 1) + cell_w
        bar_h = int(fs * 2.1)
        x0 = (w - bar_w) / 2
        y0 = h - bar_h - 16
        # 半透明底条
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.rounded_rectangle([x0, y0, x0 + bar_w, y0 + bar_h], radius=12, fill=(0, 0, 0, 165))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(img)
        # 引擎名
        d.text((x0 + pad, y0 + (bar_h - fs) / 2 - 2), label, fill=(255, 255, 255), font=font)
        # 页码块
        cx = x0 + pad * 2 + lw + gap
        cy = y0 + (bar_h - cell_w) / 2
        for n in nums:
            cur = (n == pn)
            d.rounded_rectangle([cx, cy, cx + cell_w, cy + cell_w], radius=6,
                                fill=(78, 110, 242, 255) if cur else (255, 255, 255, 40),
                                outline=(255, 255, 255, 200) if cur else None, width=1)
            tw = d.textlength(str(n), font=font)
            d.text((cx + (cell_w - tw) / 2, cy + (cell_w - fs) / 2 - 1), str(n),
                   fill=(255, 255, 255) if cur else (230, 232, 235), font=font)
            cx += cell_w + gap
        # 右箭头
        d.rounded_rectangle([cx, cy, cx + cell_w, cy + cell_w], radius=6,
                            fill=(255, 255, 255, 40), outline=(255, 255, 255, 200), width=1)
        d.text((cx + (cell_w - fs) / 2, cy + (cell_w - fs) / 2 - 2), ">",
               fill=(230, 232, 235), font=font)
        img.save(path, "PNG")
    except Exception:
        pass


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
    import os
    for name in ("Current Session", "Last Session", "Current Tabs", "Last Tabs"):
        p = os.path.join(profile_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


_PW = None


def _pw_singleton():
    """Playwright 实例单例：Sync API 同一线程只能 start 一次，多个浏览器共享一个实例"""
    global _PW
    if _PW is None:
        _PW = sync_playwright().start()
    return _PW


def stop_playwright():
    global _PW
    if _PW is not None:
        try:
            _PW.stop()
        except Exception:
            pass
        _PW = None


class BrowserSession:
    def __init__(self, profile_dir=None, mobile=False):
        self._pw = _pw_singleton()
        profile = profile_dir or (config.MOBILE_PROFILE_DIR if mobile else config.PROFILE_DIR)
        _clean_session_files(profile)
        kw = dict(
            headless=False,                       # 必须显示窗口（用户人工验证）
            locale="zh-CN",
            args=["--start-maximized", "--disable-session-crashed-bubble", "--no-first-run"],
        )
        if mobile:
            kw["user_agent"] = config.MOBILE_USER_AGENT
            kw["viewport"] = config.MOBILE_VIEWPORT
            kw["device_scale_factor"] = config.MOBILE_DSF
            kw["is_mobile"] = True
            kw["has_touch"] = True
        else:
            kw["user_agent"] = config.USER_AGENT
            kw["viewport"] = config.VIEWPORT
        self.ctx = self._pw.chromium.launch_persistent_context(profile, **kw)
        self.pages = []

    def new_page(self):
        pg = self.ctx.new_page()
        self.pages.append(pg)
        return pg

    def close_page(self, pg):
        try:
            pg.close()
        except Exception:
            pass

    def random_delay(self):
        time.sleep(random.randint(config.DELAY_MIN_MS, config.DELAY_MAX_MS) / 1000)

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass
        # 不 stop 共享 _pw（由 stop_playwright() 统一收尾）
