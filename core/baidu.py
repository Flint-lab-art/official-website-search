# -*- coding: utf-8 -*-
"""百度采集：搜索、过滤广告、识别 Elo 官网标识、翻页、验证码处理"""
import random
from urllib.parse import quote

from . import config
from .engine import is_captcha, wait_for_captcha, scroll_trigger

SEARCH_URL = "https://www.baidu.com/s?wd={}"


def _parse_page(page):
    """解析当前页，返回过滤后的自然结果列表。
    每条: {'title','source','href','ad'}，按自然排名从 1 编号。"""
    results = []
    rank = 0
    containers = page.locator("#content_left div.c-container")
    n = containers.count()
    for i in range(n):
        c = containers.nth(i)
        cls = c.get_attribute("class") or ""
        # 1) 广告：容器内推广角标
        if c.locator(".ec-tuiguang").count() > 0:
            continue
        # 2) 特殊模块：AI 文答卡片、相关搜索
        if "wenda" in cls:
            continue
        title_el = c.locator("h3").first
        if title_el.count() == 0:
            continue
        title = (title_el.inner_text() or "").strip()
        if "大家还在搜" in title:
            continue
        # 3) 来源行（站点名/域名）
        src_el = c.locator(".cosc-source-text").first
        source = (src_el.inner_text() or "").strip() if src_el.count() else ""
        href_el = c.locator("a[href]").first
        href = href_el.get_attribute("href") or "" if href_el.count() else ""
        rank += 1
        results.append({"rank": rank, "title": title, "source": source, "href": href})
    return results


def _is_elo_official(title, source):
    text = (title or "") + "|" + (source or "")
    return any(m in text for m in config.BAIDU_OFFICIAL_MARKS)


def _next_page(page, page_no):
    """翻到第 page_no 页（page_no>=2 时点击 #page 页码）"""
    nav = page.locator("#page")
    link = nav.locator("a", has_text=str(page_no)).first
    if link.count() == 0:
        return False
    try:
        link.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        link.click()
    except Exception:
        return False
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    page.wait_for_timeout(config.PAGE_WAIT_MS + random.randint(0, 1000))
    return True


def run_baidu(session, keyword, shot_dir, skip_evt=None, notify=None, page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    page 可传入复用的标签页（不传则新建/用完关闭）。
    返回 dict: status / rank / page / evidence / screenshot"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        page.goto(SEARCH_URL.format(quote(keyword)), timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)

        for pn in range(1, config.MAX_PAGES + 1):
            # 验证码检测
            if is_captcha(page, "baidu"):
                r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                if r == "skip":
                    return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                # 完成后重新加载当前页
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            results = _parse_page(page)
            for r in results:
                if _is_elo_official(r["title"], r["source"]):
                    shot = _screenshot(page, keyword, pn, "baidu", shot_dir)
                    evidence = f"自然第{r['rank']}位｜来源:{r['source'][:30]}｜标题:{r['title'][:40]}"
                    return {"status": config.ST_HIT, "rank": r["rank"],
                            "page": pn, "evidence": evidence, "shot": shot}
            # 未命中 → 翻页
            if pn < config.MAX_PAGES:
                session.random_delay()
                if not _next_page(page, pn + 1):
                    return {"status": config.ST_NONE, "evidence": f"翻到第{pn}页无更多"}
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现官网标识"}
    except Exception as e:
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)


def _screenshot(page, keyword, pn, engine, shot_dir):
    import os, re
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
