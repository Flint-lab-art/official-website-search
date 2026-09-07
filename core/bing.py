# -*- coding: utf-8 -*-
"""必应采集：搜索、过滤广告（兜底）、域名匹配、URL 参数翻页"""
import os, re, random
from urllib.parse import quote

from . import config
from .engine import scroll_trigger

SEARCH_URL = "https://cn.bing.com/search?q={}&ensearch=0&first={}"


def _parse_page(page):
    """返回过滤后的自然结果列表（从 1 编号）"""
    results = []
    rank = 0
    lis = page.locator("li.b_algo")
    n = lis.count()
    for i in range(n):
        li = lis.nth(i)
        cls = li.get_attribute("class") or ""
        # 广告兜底过滤
        if "b_ad" in cls:
            continue
        a = li.locator("h2 a").first
        if a.count() == 0:
            continue
        title = (a.inner_text() or "").strip()
        href = a.get_attribute("href") or ""
        cite = li.locator("cite").first
        cite_txt = (cite.inner_text() or "").strip() if cite.count() else ""
        rank += 1
        results.append({"rank": rank, "title": title, "href": href, "cite": cite_txt})
    return results


def _is_restricted(page):
    """必应受限页检测：合规过滤（部分搜索结果未予显示）或知识卡空结果页"""
    try:
        body = page.locator("body").inner_text(timeout=5000) or ""
    except Exception:
        return False
    if "部分搜索结果未予显示" in body or "未予显示" in body:
        return True
    if "深入了解" in body and page.locator("li.b_algo").count() == 0:
        return True
    return False


def _is_domain_hit(r):
    return config.TARGET_DOMAIN in r["cite"] or config.TARGET_DOMAIN in r["href"]


def run_bing(session, keyword, shot_dir, page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    P1 走「首页→输入→回车」（消除直达 URL 的搜索偏差，保证原生页码条渲染）；
    P2+ 用 first= 参数直达翻页。
    page 可传入复用的标签页（不传则新建/用完关闭）。"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        for pn in range(1, config.MAX_PAGES + 1):
            if pn == 1:
                # P1：必应首页 → 输入关键词 → 回车（同用户手动搜索）
                page.goto("https://www.bing.com/?mkt=zh-CN", timeout=60000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)
                page.wait_for_selector("input[name='q']", timeout=20000)
                page.fill("input[name='q']", keyword)
                page.keyboard.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(config.PAGE_WAIT_MS)
            else:
                first = (pn - 1) * 10 + 1
                url = SEARCH_URL.format(quote(keyword), first)
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            results = _parse_page(page)
            for r in results:
                if _is_domain_hit(r):
                    shot = _screenshot(page, keyword, pn, "bing", shot_dir)
                    evidence = f"自然第{r['rank']}位｜cite:{r['cite'][:40]}"
                    return {"status": config.ST_HIT, "rank": r["rank"],
                            "page": pn, "evidence": evidence, "shot": shot}
            # 受限页：合规过滤/知识卡空结果，无自然结果可判定，立即停止翻页
            if _is_restricted(page):
                return {"status": config.ST_RESTRICTED,
                        "evidence": f"第{pn}页：必应受限页（结果被过滤），无法判定"}
            # 未命中：页尾判断，防止无限翻
            if len(results) == 0 and pn > 1:
                return {"status": config.ST_NONE, "evidence": f"第{pn}页无结果"}
            if pn < config.MAX_PAGES:
                session.random_delay()
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现 {config.TARGET_DOMAIN}"}
    except Exception as e:
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)


def _screenshot(page, keyword, pn, engine, shot_dir):
    safe = re.sub(r'[\\/:*?"<>|]', "_", keyword)
    sub = config.SHOT_PLATFORM_DIR.get(engine, "")
    folder = os.path.join(shot_dir, sub)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{safe}_{engine}_p{pn}.png")
    try:
        scroll_trigger(page)
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None
