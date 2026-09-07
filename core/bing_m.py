# -*- coding: utf-8 -*-
"""必应移动端采集：cn.bing.com（iPhone UA）
P1 用「首页→输入→回车」模拟真人（消除直达 URL 搜索偏差，参考用户家里脚本）；
翻页用 first= 参数 goto（实测无验证码）。
判定：cite 域名含 elotouch.com.cn
"""
from urllib.parse import quote
from core import config

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


def run_bing_m(session, keyword, shot_dir, page=None):
    """搜索 keyword，翻前 MAX_PAGES 页，命中即停。"""
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

        for pn in range(1, config.MAX_PAGES + 1):
            if pn > 1:
                first = (pn - 1) * 10 + 1
                page.goto(SEARCH_URL.format(kw=quote(keyword), first=first), timeout=60000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_WAIT_MS)

            for rank, cite in _parse_page(page):
                if config.TARGET_DOMAIN in cite:
                    shot = _screenshot(page, keyword, pn, "bing_m", shot_dir)
                    evidence = f"自然第{rank}位｜cite:{cite[:40]}"
                    return {"status": config.ST_HIT, "rank": rank, "page": pn,
                            "evidence": evidence, "shot": shot}
            if pn < config.MAX_PAGES:
                session.random_delay()
        return {"status": config.ST_NONE, "evidence": f"前{config.MAX_PAGES}页未出现 {config.TARGET_DOMAIN}"}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": config.ST_ERROR, "evidence": f"异常:{e}"}
    finally:
        if own_page:
            session.close_page(page)
