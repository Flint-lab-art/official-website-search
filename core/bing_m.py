# -*- coding: utf-8 -*-
"""必应移动端采集：cn.bing.com（iPhone UA）
P1 用「首页→输入→回车」模拟真人（消除直达 URL 搜索偏差，参考用户家里脚本）；
翻页用 first= 参数 goto（实测无验证码）。
判定：cite 域名含 elotouch.com.cn
"""
from urllib.parse import quote
from core import config
from core.engine import scroll_trigger, wait_render_ready

HOME_URL = "https://www.bing.com/?mkt=zh-CN"
SEARCH_URL = "https://cn.bing.com/search?q={kw}&first={first}&mkt=zh-CN"


def _screenshot(page, keyword, pn, engine, shot_dir):
    import os, re
    safe = re.sub(r'[\\/:*?"<>|]+', "_", keyword)[:60]
    sub = config.SHOT_PLATFORM_DIR.get(engine, "")
    folder = os.path.join(shot_dir, sub)
    os.makedirs(folder, exist_ok=True)
    shot = os.path.join(folder, f"{safe}_{engine}_p{pn}.png")
    try:
        wait_render_ready(page)  # 必应异步渲染：DOM 出现 ≠ 已绘制，截图前等渲染完成
        scroll_trigger(page)
        page.screenshot(path=shot, full_page=True)
        return shot
    except Exception:
        return None


def _parse_page(page):
    """返回 [(rank, cite), ...] 自然结果（跳过广告 b_ad）"""
    results = []
    for it in page.query_selector_all("li.b_algo"):
        cls = it.get_attribute("class") or ""
        if "b_ad" in cls:
            continue
        cite_el = it.query_selector("cite")
        cite = (cite_el.inner_text() or "").strip() if cite_el else ""
        results.append((len(results) + 1, cite))
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


def run_bing_m(session, keyword, shot_dir, page=None, on_page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。
    on_page(kw, engine, pn) 每翻到一页时回调（用于面板日志显示页码进度）。"""
    own_page = page is None
    if page is None:
        page = session.new_page()
    try:
        # P1：首页 → 输入 → 回车
        page.goto(HOME_URL, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)
        page.wait_for_selector("input[name='q']", timeout=20000)
        page.fill("input[name='q']", keyword)
        page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(config.PAGE_WAIT_MS)

        zero_pages = 0  # 熔断计数：连续解析出 0 条的页数
        for pn in range(1, config.MAX_PAGES + 1):
            if on_page:
                on_page(keyword, "bing_m", pn)
            if pn > 1:
                first = (pn - 1) * 10 + 1
                page.goto(SEARCH_URL.format(kw=quote(keyword), first=first), timeout=60000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            parsed = _parse_page(page)
            for rank, cite in parsed:
                if config.TARGET_DOMAIN in cite:
                    shot = _screenshot(page, keyword, pn, "bing_m", shot_dir)
                    evidence = f"自然第{rank}位｜cite:{cite[:40]}"
                    return {"status": config.ST_HIT, "rank": rank, "page": pn,
                            "evidence": evidence, "shot": shot}
            if _is_restricted(page):
                return {"status": config.ST_RESTRICTED,
                        "evidence": f"第{pn}页：必应受限页（结果被过滤），无法判定"}
            # 熔断：连续多页 0 条（页面结构失效/被拦截）→ 解析异常，避免误报未命中
            zero_pages = zero_pages + 1 if len(parsed) == 0 else 0
            if zero_pages >= config.ZERO_RESULT_BREAK:
                return {"status": config.ST_MALFUNCTION,
                        "evidence": f"连续{zero_pages}页解析出0条结果，疑似页面结构变化或搜索被拦截"}
            if pn < config.MAX_PAGES:
                session.random_delay()
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现 {config.TARGET_DOMAIN}"}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)
