# -*- coding: utf-8 -*-
"""百度采集：搜索、过滤广告、识别 Elo 官网标识、翻页、验证码处理"""
import random, traceback
from urllib.parse import quote

from . import config
from . import logger
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


def run_baidu(session, keyword, shot_dir, skip_evt=None, notify=None, page=None, on_page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    page 可传入复用的标签页（不传则新建/用完关闭）。
    on_page(kw, engine, pn) 每翻到一页时回调（用于面板日志显示页码进度）。
    返回 dict: status / rank / page / evidence / screenshot"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        page.goto(SEARCH_URL.format(quote(keyword)), timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)
        logger.debug("baidu", f"P1 URL: {page.url[:120]}")

        zero_pages = 0  # 熔断计数：连续解析出 0 条的页数
        for pn in range(1, config.MAX_PAGES + 1):
            if on_page:
                on_page(keyword, "baidu", pn)
            # 验证码检测
            if is_captcha(page, "baidu"):
                logger.debug("baidu", f"P{pn} 检测到验证码")
                r = wait_for_captcha(page, "baidu", keyword, skip_evt=skip_evt, notify=notify)
                if r == "skip":
                    return {"status": config.ST_ERROR, "evidence": "验证码跳过"}
                # 完成后重新加载当前页
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            results = _parse_page(page)
            logger.debug("baidu", f"P{pn} 解析 {len(results)} 条")
            # 熔断：连续多页 0 条（页面结构失效/被拦截）→ 解析异常，避免误报未命中
            zero_pages = zero_pages + 1 if len(results) == 0 else 0
            if zero_pages >= config.ZERO_RESULT_BREAK:
                logger.debug("baidu", f"P{pn} 熔断：连续{zero_pages}页0条")
                return {"status": config.ST_MALFUNCTION,
                        "evidence": f"连续{zero_pages}页解析出0条结果，疑似页面结构变化或搜索被拦截"}
            for r in results:
                if _is_elo_official(r["title"], r["source"]):
                    logger.debug("baidu", f"P{pn} 命中 自然第{r['rank']}位 来源:{r['source'][:30]}")
                    shot = _screenshot(page, keyword, pn, "baidu", shot_dir)
                    evidence = f"自然第{r['rank']}位｜来源:{r['source'][:30]}｜标题:{r['title'][:40]}"
                    return {"status": config.ST_HIT, "rank": r["rank"],
                            "page": pn, "evidence": evidence, "shot": shot}
            # 未命中 → 翻页
            if pn < config.MAX_PAGES:
                session.random_delay()
                if not _next_page(page, pn + 1):
                    logger.debug("baidu", f"P{pn} 翻页失败：第{pn+1}页链接不存在")
                    return {"status": config.ST_NONE, "evidence": f"翻到第{pn}页无更多"}
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现官网标识"}
    except Exception as e:
        logger.error("baidu", f"{keyword} 异常: {e}\n{traceback.format_exc()}")
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)


def _wait_ai_output(page):
    """百度 PC AI 摘要/智能体为流式输出（逐字生成）：
    等 AI 容器内容连续 2 次采样不变（约 2 秒稳定）视为生成完成，最长 ~22s。
    不点「展开剩余」按钮——保持页面折叠原貌。"""
    try:
        ai = page.locator("[class*='wenda']").first
        has_ai = ai.count() > 0
    except Exception:
        has_ai = False
    if not has_ai:
        page.wait_for_timeout(800)
        return
    last_text, stable = None, 0
    for _ in range(20):
        try:
            txt = ai.inner_text(timeout=3000) or ""
        except Exception:
            break
        if "正在生成" in txt or "生成中" in txt:
            last_text, stable = None, 0
        elif txt == last_text:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last_text = txt
        page.wait_for_timeout(1000)
    page.wait_for_timeout(1500)


def _screenshot(page, keyword, pn, engine, shot_dir):
    import os, re
    safe = re.sub(r'[\\/:*?"<>|]', "_", keyword)
    sub = config.SHOT_PLATFORM_DIR.get(engine, "")
    folder = os.path.join(shot_dir, sub)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{safe}_{engine}_p{pn}.png")
    try:
        if engine == "baidu":
            _wait_ai_output(page)  # 等百度 AI 摘要流式输出完，避免截到一半
        scroll_trigger(page)
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None
