# -*- coding: utf-8 -*-
"""浏览器会话管理：持久化用户目录（cookie 复用）+ 验证码人工处理"""
import random, time, sys
from playwright.sync_api import sync_playwright

from . import config

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
