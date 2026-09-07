# -*- coding: utf-8 -*-
"""百度移动端采集：m.baidu.com
判定：结果条目来源行 =「Elo®中国官网」标识（与 PC 同套）
翻页：点击「下一页」按钮（pn= 直达会被风控触发验证码，实测确认）
"""
from urllib.parse import quote
from core import config
from core.engine import is_captcha, wait_for_captcha, scroll_trigger

SEARCH_URL = "https://m.baidu.com/s?word={kw}"

# 移动版工具性容器（非结果条目）
_SKIP_CLASS = ("loading", "menu-", "searchboxtop", "search-wrap", "con-wrap",
               "fixed-placeholder", "resource-filter", "search-page")

# 移动版特殊模块（相关搜索/图片视频等，按文本前缀识别；AI 摘要不剔除，单独处理）
_SPECIAL_PREFIX = ("大家还在搜", "相关搜索", "百度图片",
                   "百度视频", "百度地图", "百度热搜", "为你推荐")

# AI 摘要前缀（保留计数，正文参与官网标识命中）
_AI_PREFIX = ("解答", "总结全网")


def _is_skip(cls):
    return any(x in cls for x in _SKIP_CLASS)


def _close_popups(page):
    """关闭移动端百度插屏/弹窗广告（如推广弹窗、浮层）"""
    closed = 0
    for sel in ("[aria-label*='关闭']", "[class*='isclose']", "[class*='close']",
                "[class*='popup'] [class*='close']", "[class*='dialog'] [class*='close']"):
        for e in page.query_selector_all(sel):
            if closed >= 3:
                return
            try:
                if e.is_visible() and (e.get_attribute("aria-label") or
                                       (e.get_attribute("class") or "")):
                    e.click()
                    page.wait_for_timeout(400)
                    closed += 1
            except Exception:
                continue
    # 兜底：可见的 × 按钮
    for e in page.query_selector_all("span, a, i, div"):
        if closed >= 3:
            return
        try:
            if e.is_visible() and (e.inner_text() or "").strip() in ("×", "✕", "X", "关闭"):
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
        txt = (it.inner_text() or "").strip()
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
        src = (src_el.inner_text() or "").strip() if src_el else ""
        title = (h3.inner_text() or "").strip() if h3 else ""
        results.append((rank, src, title))
    return results


def _is_hit(src, title):
    for m in config.BAIDU_OFFICIAL_MARKS:
        if m in src or m in title:
            return True
    return False


def _click_next(page):
    """点击「下一页」按钮，成功翻页返回 True"""
    _close_popups(page)  # 弹窗可能遮挡下一页按钮
    before = page.url
    for sel in ("a.new-nextpage-only", "[class*='nextpage'] a", "a[href*='pn=']"):
        for e in page.query_selector_all(sel):
            if e.is_visible():
                href = e.get_attribute("href") or ""
                if "pn=" not in href and "nextpage" not in sel:
                    continue
                try:
                    e.click()
                    page.wait_for_timeout(config.PAGE_WAIT_MS + 2500)
                    after = page.url
                    # 验证码兜底
                    if is_captcha(page, "baidu"):
                        return "captcha"
                    return after != before
                except Exception:
                    continue
    return False


def _screenshot(page, keyword, pn, engine, shot_dir):
    import os, re
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


def run_baidu_m(session, keyword, shot_dir, skip_evt=None, notify=None, page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    返回 dict: status / rank / page / evidence / screenshot"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        page.goto(SEARCH_URL.format(kw=quote(keyword)), timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)

        zero_pages = 0  # 熔断计数：连续解析出 0 条的页数
        for pn in range(1, config.MAX_PAGES + 1):
            if is_captcha(page, "baidu"):
                r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                if r == "skip":
                    return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            _close_popups(page)  # 先关弹窗广告，避免遮挡/干扰解析

            parsed = _parse_page(page)
            # 熔断：连续多页 0 条（页面结构失效/被拦截）→ 解析异常，避免误报未命中
            zero_pages = zero_pages + 1 if len(parsed) == 0 else 0
            if zero_pages >= config.ZERO_RESULT_BREAK:
                return {"status": config.ST_MALFUNCTION,
                        "evidence": f"连续{zero_pages}页解析出0条结果，疑似页面结构变化或搜索被拦截"}

            for rank, src, title in parsed:
                if _is_hit(src, title):
                    _close_popups(page)  # 截图前再关一次弹窗
                    shot = _screenshot(page, keyword, pn, "baidu_m", shot_dir)
                    evidence = f"自然第{rank}位｜来源:{src or title[:20]}"
                    return {"status": config.ST_HIT, "rank": rank, "page": pn,
                            "evidence": evidence, "shot": shot}
            if pn < config.MAX_PAGES:
                r = _click_next(page)
                if r == "captcha":
                    r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                    if r == "skip":
                        return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                    page.wait_for_timeout(config.PAGE_WAIT_MS)
                    continue
                if not r:
                    return {"status": config.ST_NONE,
                            "evidence": f"第{pn}页无下一页按钮"}
                session.random_delay()
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现官网标识"}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)
