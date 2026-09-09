"""百度移动端采集：m.baidu.com
判定：结果条目来源行 =「Elo®中国官网」标识（与 PC 同套）
翻页：点击「下一页」按钮（pn= 直达会被风控触发验证码，实测确认）
"""

import contextlib
import traceback
from urllib.parse import quote

from core import config, logger
from core.engine import is_captcha, scroll_trigger, wait_for_captcha

SEARCH_URL = "https://m.baidu.com/s?word={kw}"

# 移动版工具性容器（非结果条目）
_SKIP_CLASS = (
    "loading",
    "menu-",
    "searchboxtop",
    "search-wrap",
    "con-wrap",
    "fixed-placeholder",
    "resource-filter",
    "search-page",
)

# 移动版特殊模块（相关搜索/图片视频等，按文本前缀识别；AI 摘要不剔除，单独处理）
_SPECIAL_PREFIX = (
    "大家还在搜",
    "相关搜索",
    "百度图片",
    "百度视频",
    "百度地图",
    "百度热搜",
    "为你推荐",
)

# AI 摘要前缀（保留计数，正文参与官网标识命中）
_AI_PREFIX = ("解答", "总结全网")


def _is_skip(cls):
    return any(x in cls for x in _SKIP_CLASS)


def _close_popups(page):
    """关闭移动端百度插屏/弹窗广告（如推广弹窗、浮层）。
    只扫描 aria-label/class 含关闭语义的候选；兜底遍历限前 120 个元素，
    避免整页数千元素逐个检查拖慢翻页（每页会调用多次）。"""
    closed = 0
    for sel in (
        "[aria-label*='关闭']",
        "[class*='isclose']",
        "[class*='close']",
        "[class*='popup'] [class*='close']",
        "[class*='dialog'] [class*='close']",
    ):
        for e in page.query_selector_all(sel):
            if closed >= 3:
                return
            try:
                if e.is_visible() and (
                    e.get_attribute("aria-label") or (e.get_attribute("class") or "")
                ):
                    e.click()
                    page.wait_for_timeout(400)
                    closed += 1
            except Exception:
                continue
    # 兜底：可见的 × 按钮。只检查前 120 个元素，避免整页遍历（移动页面元素数千个）
    scanned = 0
    for e in page.query_selector_all("span, a, i, div"):
        scanned += 1
        if scanned > 120 or closed >= 3:
            return
        try:
            if not e.is_visible():
                continue
            if (e.inner_text() or "").strip() in ("×", "✕", "X", "关闭"):
                e.click()
                page.wait_for_timeout(400)
                closed += 1
        except Exception:
            continue


def _parse_page(page):
    """返回 [(rank, src_text, title), ...] 自然结果列表（跳过广告/特殊模块）"""
    results = []
    items = page.query_selector_all("div.c-result")
    if not items:  # 兜底选择器
        items = page.query_selector_all("div[class*='result'], div[class*='c-container']")
    rank = 0
    for it in items:
        cls = it.get_attribute("class") or ""
        if _is_skip(cls):
            continue
        txt = (it.text_content() or "").strip()
        # 广告/推广标记（条目头部）
        if any(m in txt[:12] for m in ("广告", "推广")):
            continue
        # AI 摘要：不剔除，保留排名位（后续结果排名如实前移），但正文不算官网标识命中
        if any(txt.startswith(p) for p in _AI_PREFIX):
            rank += 1
            results.append((rank, "", ""))
            continue
        # 其他特殊模块：相关搜索/图片视频等
        if any(txt.startswith(p) for p in _SPECIAL_PREFIX):
            continue
        h3 = it.query_selector("h3")
        src_el = it.query_selector("span.cosc-source-text")
        if not h3 and not src_el:
            continue  # 无标题无来源行 = 其他特殊模块
        rank += 1
        src = (src_el.text_content() or "").strip() if src_el else ""
        title = (h3.text_content() or "").strip() if h3 else ""
        results.append((rank, src, title))
    return results


def _is_hit(src, title):
    return any(m in src or m in title for m in config.BAIDU_OFFICIAL_MARKS)


def _current_page_no(page):
    """读取页面分页控件当前页码（移动百度显示「第N页」），读不到返回 None"""
    try:
        import re

        body = page.locator("body").inner_text(timeout=3000)
        m = re.search(r"第(\d+)页", body)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _url_pn(url):
    """从 URL 提取 pn 参数（0 基：pn=10 表示第2页；无参数=第1页）"""
    try:
        import re

        m = re.search(r"[?&]pn=(\d+)", url)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _click_next(page, target_pn):
    """点击「下一页」按钮，成功翻页返回 True。
    百度移动翻页是 SPA 内部更新（Ajax 换内容，不触发整页导航），
    所以「URL 变化」≠「内容已翻页」。判定标准：
    1) 只点「下一页」链接：href 的 pn 必须等于 当前pn+10——
       第3页起页面同时有「上一页」(pn=当前-10) 和「下一页」两个 pn= 链接，
       不按 pn 过滤会点到「上一页」，导致窗口在第2/3页来回跳、日志虚报。
    2) 翻页后优先校验页面「第N页」分页指示到达目标页（硬校验）；
       读不到分页指示时退化用结果签名（自然结果标题+来源行）变化判断。
    注意：签名不含 href——百度结果链接带动态跟踪参数，href 每次渲染都变。
    """
    import time

    _close_popups(page)  # 弹窗可能遮挡下一页按钮
    before = page.url
    target = target_pn + 1
    cur_pn = _url_pn(page.url)
    want_pn = cur_pn + 10  # 下一页的目标 pn（每页 10 条）

    def _sig():
        """结果区签名：自然结果（带 h3 条目）的 标题|来源行 序列"""
        try:
            items = page.query_selector_all("div.c-result")
            if not items:
                return ""
            parts = []
            for it in items[:8]:
                h3 = it.query_selector("h3")
                if not h3:
                    continue  # 只取自然结果，跳过相关搜索/AI摘要等模块
                src = it.query_selector("span.cosc-source-text")
                t = (h3.text_content() or "").strip()[:30]
                s = (src.text_content() or "").strip()[:30] if src else ""
                parts.append(f"{t}|{s}")
            return "||".join(parts)
        except Exception:
            return ""

    sig0 = _sig()
    for sel in ("a.new-nextpage-only", "[class*='nextpage'] a", "a[href*='pn=']"):
        for e in page.query_selector_all(sel):
            if not e.is_visible():
                continue
            href = e.get_attribute("href") or ""
            # 只接受指向「下一页」的链接（pn == want_pn），跳过上一页/其它 pn 链接
            try:
                import re as _re

                m = _re.search(r"[?&]pn=(\d+)", href)
            except Exception:
                m = None
            if not m or int(m.group(1)) != want_pn:
                continue
            try:
                e.click()
                # 1) 等 URL 真的变化（最多 8s）
                deadline = time.time() + 8
                while time.time() < deadline:
                    if page.url != before:
                        break
                    page.wait_for_timeout(300)
                # 2) 等页面到达目标页（最多 20s）
                ok = False
                deadline = time.time() + 20
                while time.time() < deadline:
                    if is_captcha(page, "baidu"):
                        return "captcha"
                    cur = _current_page_no(page)
                    if cur == target:
                        ok = True
                        break
                    # 读不到分页指示 → 退化用签名变化判断
                    if cur is None:
                        s1 = _sig()
                        if s1 and s1 != sig0:
                            ok = True
                            break
                    page.wait_for_timeout(1000)
                # 3) 渲染稳定后再返回
                page.wait_for_timeout(config.PAGE_WAIT_MS)
                if is_captcha(page, "baidu"):
                    return "captcha"
                return ok and page.url != before
            except Exception:
                continue
    return False


def _screenshot(page, keyword, pn, engine, shot_dir):
    import os
    import re

    safe = re.sub(r'[\\/:*?"<>|]+', "_", keyword)[:60]
    sub = config.SHOT_PLATFORM_DIR.get(engine, "")
    folder = os.path.join(shot_dir, sub)
    os.makedirs(folder, exist_ok=True)
    shot = os.path.join(folder, f"{safe}_{engine}_p{pn}.png")
    try:
        scroll_trigger(page)
        page.screenshot(path=shot, full_page=True)
        return shot
    except Exception:
        return None


def run_baidu_m(session, keyword, shot_dir, skip_evt=None, notify=None, page=None, on_page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    on_page(kw, engine, pn) 每翻到一页时回调（用于面板日志显示页码进度）。
    返回 dict: status / rank / page / evidence / screenshot"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        page.goto(
            SEARCH_URL.format(kw=quote(keyword)), timeout=60000, wait_until="domcontentloaded"
        )
        page.wait_for_timeout(config.PAGE_WAIT_MS)
        logger.debug("baidu_m", f"P1 URL: {page.url[:120]}")
        # 保险：首屏慢（冷启动/AI摘要流式输出）时等结果容器出现，避免误判 0 条触发熔断
        with contextlib.suppress(Exception):
            page.wait_for_selector("div.c-result", timeout=8000)

        zero_pages = 0  # 熔断计数：连续解析出 0 条的页数
        for pn in range(1, config.MAX_PAGES + 1):
            if is_captcha(page, "baidu"):
                logger.debug("baidu_m", f"P{pn} 检测到验证码")
                r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                if r == "skip":
                    return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            _close_popups(page)  # 先关弹窗广告，避免遮挡/干扰解析

            parsed = _parse_page(page)
            logger.debug("baidu_m", f"P{pn} 解析 {len(parsed)} 条")
            if on_page:
                # 解析后上报页码+结果条数，面板日志可见（替代 print，print 不会进面板日志）
                on_page(keyword, "baidu_m", pn, len(parsed))
            # 熔断：连续多页 0 条（页面结构失效/被拦截）→ 解析异常，避免误报未命中
            zero_pages = zero_pages + 1 if len(parsed) == 0 else 0
            if zero_pages >= config.ZERO_RESULT_BREAK:
                logger.debug("baidu_m", f"P{pn} 熔断：连续{zero_pages}页0条")
                return {
                    "status": config.ST_MALFUNCTION,
                    "evidence": f"连续{zero_pages}页解析出0条结果，疑似页面结构变化或搜索被拦截",
                }

            for rank, src, title in parsed:
                if _is_hit(src, title):
                    logger.debug("baidu_m", f"P{pn} 命中 自然第{rank}位 来源:{src or title[:20]}")
                    _close_popups(page)  # 截图前再关一次弹窗
                    shot = _screenshot(page, keyword, pn, "baidu_m", shot_dir)
                    evidence = f"自然第{rank}位｜来源:{src or title[:20]}"
                    return {
                        "status": config.ST_HIT,
                        "rank": rank,
                        "page": pn,
                        "evidence": evidence,
                        "shot": shot,
                    }
            if pn < config.MAX_PAGES:
                r = _click_next(page, pn)
                logger.debug("baidu_m", f"P{pn} 翻页结果: {r}")
                if r == "captcha":
                    r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                    if r == "skip":
                        return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                    page.wait_for_timeout(config.PAGE_WAIT_MS)
                    continue
                if not r:
                    return {
                        "status": config.ST_NONE,
                        "evidence": f"第{pn}页翻页失败（点击后内容未更新，疑似风控或按钮失效）",
                    }
                session.random_delay()
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现官网标识"}
    except Exception as e:
        logger.error("baidu_m", f"{keyword} 异常: {e}\n{traceback.format_exc()}")
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)
