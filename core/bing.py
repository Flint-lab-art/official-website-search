"""必应采集：搜索、过滤广告（兜底）、域名匹配、URL 参数翻页"""

import os
import re
import traceback
from urllib.parse import quote

from . import config, logger
from .engine import scroll_trigger, wait_render_ready

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
    """必应受限页检测，返回受限原因（str）或 None。
    注意：cn.bing.com 结果页 body 通常自带「未予显示」合规提示文本，
    即使页面有正常结果也会出现——所以必须 b_algo==0（真无结果）时才判受限，
    否则所有正常结果页都会被误判为受限。"""
    try:
        body = page.locator("body").inner_text(timeout=5000) or ""
    except Exception:
        return None
    n_algo = page.locator("li.b_algo").count()
    if n_algo == 0:
        if "部分搜索结果未予显示" in body or "未予显示" in body:
            return "b_algo=0 且 body含'未予显示'"
        if "深入了解" in body:
            return "b_algo=0 且 body含'深入了解'"
    return None


def _is_domain_hit(r):
    return config.TARGET_DOMAIN in r["cite"] or config.TARGET_DOMAIN in r["href"]


def run_bing(session, keyword, shot_dir, page=None, on_page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    P1 走「首页→输入→回车」（消除直达 URL 的搜索偏差，保证原生页码条渲染）；
    P2+ 用 first= 参数直达翻页。
    page 可传入复用的标签页（不传则新建/用完关闭）。
    on_page(kw, engine, pn) 每翻到一页时回调（用于面板日志显示页码进度）。"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        zero_pages = 0  # 熔断计数：连续解析出 0 条的页数
        for pn in range(1, config.MAX_PAGES + 1):
            if on_page:
                on_page(keyword, "bing", pn)
            if pn == 1:
                # P1：必应首页 → 输入关键词 → 回车（同用户手动搜索）
                page.goto("https://cn.bing.com/", timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)
                page.wait_for_selector("input[name='q']", timeout=20000)
                page.fill("input[name='q']", keyword)
                page.keyboard.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(config.PAGE_WAIT_MS)
                logger.debug("bing", f"P1 回车后 URL: {page.url[:120]}")
            else:
                first = (pn - 1) * 10 + 1
                url = SEARCH_URL.format(quote(keyword), first)
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)
                logger.debug("bing", f"P{pn} 直达 URL: {page.url[:120]}")

            # 等结果区渲染完成（异步渲染：DOM/网络加载慢时 b_algo 可能还没出现，
            # 不等就解析会得到 0 条，再被「深入了解」误判为受限页）
            try:
                page.wait_for_selector("li.b_algo", state="visible", timeout=8000)
                algo_ok = True
            except Exception:
                algo_ok = False
            page.wait_for_timeout(600)
            n_algo = page.locator("li.b_algo").count()
            logger.debug(
                "bing", f"P{pn} 等b_algo={'出现' if algo_ok else '超时'} 实际数量={n_algo}"
            )

            results = _parse_page(page)
            logger.debug("bing", f"P{pn} 解析 {len(results)} 条")
            for r in results:
                if _is_domain_hit(r):
                    logger.debug("bing", f"P{pn} 命中 自然第{r['rank']}位 cite:{r['cite'][:40]}")
                    shot = _screenshot(page, keyword, pn, "bing", shot_dir)
                    evidence = f"自然第{r['rank']}位｜cite:{r['cite'][:40]}"
                    return {
                        "status": config.ST_HIT,
                        "rank": r["rank"],
                        "page": pn,
                        "evidence": evidence,
                        "shot": shot,
                    }
            # 受限页：合规过滤/知识卡空结果，无自然结果可判定，立即停止翻页
            r_reason = _is_restricted(page)
            logger.debug("bing", f"P{pn} 受限判断: {r_reason}")
            if r_reason:
                return {
                    "status": config.ST_RESTRICTED,
                    "evidence": f"第{pn}页：必应受限页（{r_reason}），无法判定",
                }
            # 熔断：连续多页 0 条（页面结构失效/被拦截）→ 解析异常，避免误报未命中
            zero_pages = zero_pages + 1 if len(results) == 0 else 0
            if zero_pages >= config.ZERO_RESULT_BREAK:
                logger.debug("bing", f"P{pn} 熔断：连续{zero_pages}页0条")
                return {
                    "status": config.ST_MALFUNCTION,
                    "evidence": f"连续{zero_pages}页解析出0条结果，疑似页面结构变化或搜索被拦截",
                }
            # 未命中：页尾判断，防止无限翻
            if len(results) == 0 and pn > 1:
                return {"status": config.ST_NONE, "evidence": f"第{pn}页无结果"}
            if pn < config.MAX_PAGES:
                session.random_delay()
        return {
            "status": config.ST_NONE,
            "evidence": f"前{config.MAX_PAGES}页未出现 {config.TARGET_DOMAIN}",
        }
    except Exception as e:
        logger.error("bing", f"{keyword} 异常: {e}\n{traceback.format_exc()}")
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
        # 必应结果区为异步渲染：DOM 出现 ≠ 已绘制，截图前强制等渲染完成
        wait_render_ready(page)
        scroll_trigger(page)
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None
