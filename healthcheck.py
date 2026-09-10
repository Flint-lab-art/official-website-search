"""健康检查：真实浏览器验证各平台解析/判定逻辑仍有效（DOM 未改版）

用法:
    python healthcheck.py                      # 默认 baidu,bing 两个 PC 平台
    python healthcheck.py --platforms all      # 全部 4 平台
    python healthcheck.py --platforms baidu    # 只看百度
    python healthcheck.py --captcha-wait 180   # 验证码人工等待秒数（默认 120）

结论含义:
    PASS  解析正常（选择器仍匹配，可看到结果条数/是否命中官网）
    WARN  必应受限页（平台合规过滤，结果不可见，无法判定——受限检测本身工作正常）
    FAIL  第 1 页解析出 0 条或异常 —— 疑似搜索引擎改版/选择器失效/被拦截，需要人工核查

退出码: 0 = 全部 PASS/WARN；1 = 存在 FAIL
"""

import argparse
import sys
import time
from urllib.parse import quote

from core import config
from core.engine import BrowserSession, is_captcha, stop_playwright, wait_for_captcha

# 命中样本：应能解析出结果（百度/必应均有 Elo 相关内容）
HIT_SAMPLES = ["医疗级触控显示器", "开架式触控显示器"]
# 负样本：应能解析出结果，但不命中官网
NEG_SAMPLES = ["今日新闻"]


def _wait_captcha(session, page, engine, kw, wait_s):
    """检测并等待人工完成验证码；完成返回 True"""
    if not is_captcha(page, engine):
        return True
    print(f"      [验证码] 检测到人机验证，请在浏览器窗口手动完成（最多等 {wait_s}s）…")
    r = wait_for_captcha(page, engine, kw, timeout_s=wait_s)
    if r != "ok":
        return False
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)
    except Exception:
        pass
    return True


def check_baidu(session, kw, wait_s):
    from core.baidu import SEARCH_URL, _is_elo_official, _parse_page

    page = session.new_page()
    try:
        page.goto(SEARCH_URL.format(quote(kw)), timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)
        if not _wait_captcha(session, page, "baidu", kw, wait_s):
            return "FAIL", "验证码未在限时内完成"
        results = _parse_page(page)
        if len(results) == 0:
            return "FAIL", "第1页解析出0条，疑似选择器失效/搜索被拦截"
        hit = any(_is_elo_official(r["title"], r["source"]) for r in results)
        return "PASS", f"解析{len(results)}条｜命中官网={hit}"
    except Exception as e:
        return "FAIL", f"异常:{e}"
    finally:
        session.close_page(page)


def check_baidu_m(session, kw, wait_s):
    from core.baidu_m import SEARCH_URL, _close_popups, _is_hit, _parse_page

    page = session.new_page()
    try:
        page.goto(SEARCH_URL.format(kw=quote(kw)), timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(config.PAGE_WAIT_MS)
        if not _wait_captcha(session, page, "baidu", kw, wait_s):
            return "FAIL", "验证码未在限时内完成"
        _close_popups(page)
        parsed = _parse_page(page)
        if len(parsed) == 0:
            return "FAIL", "第1页解析出0条，疑似选择器失效/搜索被拦截"
        hit = any(_is_hit(src, title) for _, src, title in parsed)
        return "PASS", f"解析{len(parsed)}条｜命中官网={hit}"
    except Exception as e:
        return "FAIL", f"异常:{e}"
    finally:
        session.close_page(page)


def _bing_search_p1(page, kw):
    """P1 走首页→输入→回车（同采集器，保证结果与手动搜索一致）"""
    page.goto("https://www.bing.com/?mkt=zh-CN", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(config.PAGE_WAIT_MS)
    page.wait_for_selector("input[name='q']", timeout=20000)
    page.fill("input[name='q']", kw)
    page.keyboard.press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(config.PAGE_WAIT_MS)


def check_bing(session, kw, wait_s):
    from core.bing import _is_domain_hit, _is_restricted, _parse_page

    page = session.new_page()
    try:
        _bing_search_p1(page, kw)
        if _is_restricted(page):
            return "WARN", "受限页（结果被过滤），无法判定——受限检测正常"
        results = _parse_page(page)
        if len(results) == 0:
            return "FAIL", "第1页解析出0条，疑似选择器失效/搜索被拦截"
        hit = any(_is_domain_hit(r) for r in results)
        return "PASS", f"解析{len(results)}条｜命中官网={hit}"
    except Exception as e:
        return "FAIL", f"异常:{e}"
    finally:
        session.close_page(page)


def check_bing_m(session, kw, wait_s):
    from core.bing_m import _is_restricted, _parse_page

    page = session.new_page()
    try:
        _bing_search_p1(page, kw)
        if _is_restricted(page):
            return "WARN", "受限页（结果被过滤），无法判定——受限检测正常"
        parsed = _parse_page(page)
        if len(parsed) == 0:
            return "FAIL", "第1页解析出0条，疑似选择器失效/搜索被拦截"
        hit = any(config.TARGET_DOMAIN in cite for _, cite in parsed)
        return "PASS", f"解析{len(parsed)}条｜命中官网={hit}"
    except Exception as e:
        return "FAIL", f"异常:{e}"
    finally:
        session.close_page(page)


CHECKS = {
    "baidu": check_baidu,
    "bing": check_bing,
    "baidu_m": check_baidu_m,
    "bing_m": check_bing_m,
}
PLATFORM_LABEL = {"baidu": "百度PC", "bing": "必应PC", "baidu_m": "百度移动", "bing_m": "必应移动"}


def run_healthcheck(platforms, captcha_wait=120, log=print):
    """执行健康检查（真实浏览器）。返回 [(平台, 词, 结论, 说明), ...]
    platforms: ['baidu','bing','baidu_m','bing_m'] 的子集
    log: 进度回调（默认 print，面板可传 STATE.log）"""
    samples = HIT_SAMPLES + NEG_SAMPLES
    log(f"健康检查开始｜平台: {', '.join(PLATFORM_LABEL[p] for p in platforms)}")
    log(f"样本词: {', '.join(samples)}（验证码可人工在窗口完成）")

    all_results = []
    try:
        for eng in platforms:
            log(f"===== {PLATFORM_LABEL[eng]} =====")
            session = BrowserSession(mobile=eng.endswith("_m"))
            try:
                for kw in samples:
                    st, msg = CHECKS[eng](session, kw, captcha_wait)
                    all_results.append((eng, kw, st, msg))
                    mark = {"PASS": "  OK", "WARN": " WARN", "FAIL": " FAIL"}[st]
                    log(f"  {mark}  {kw:<12} {msg}")
                    time.sleep(1)  # 词与词之间缓一下
            finally:
                session.close()
    finally:
        # 必须清理共享 playwright 实例：它是绑定健康检查线程创建的，
        # 不清理会导致下次任务复用已退出线程的实例而崩溃
        stop_playwright()

    fails = [r for r in all_results if r[2] == "FAIL"]
    warns = [r for r in all_results if r[2] == "WARN"]
    log("===== 汇总 =====")
    log(
        f"  通过 {len(all_results) - len(fails) - len(warns)} ｜ "
        f"受限 {len(warns)} ｜ 失败 {len(fails)}"
    )
    for eng, kw, st in fails:
        log(f"  FAIL {PLATFORM_LABEL[eng]} / {kw}")
    if fails:
        log("→ 疑似搜索引擎改版或选择器失效，请人工打开页面核查，必要时更新 core/ 下的解析器")
    elif warns:
        log("→ 有受限页（平台过滤所致，非解析器问题），可忽略")
    else:
        log("→ 全部正常，判定逻辑工作有效")
    return all_results


def main():
    ap = argparse.ArgumentParser(description="官网检索器健康检查")
    ap.add_argument(
        "--platforms", default="baidu,bing", help="平台: baidu,bing,baidu_m,bing_m，逗号分隔或 all"
    )
    ap.add_argument("--captcha-wait", type=int, default=120, help="验证码人工等待秒数（默认 120）")
    args = ap.parse_args()

    platforms = (
        ["baidu", "bing", "baidu_m", "bing_m"]
        if args.platforms == "all"
        else [p.strip() for p in args.platforms.split(",") if p.strip()]
    )
    unknown = [p for p in platforms if p not in CHECKS]
    if unknown:
        print(f"[ERROR] 未知平台: {unknown}")
        sys.exit(2)

    results = run_healthcheck(platforms, captcha_wait=args.captcha_wait)
    sys.exit(1 if any(r[2] == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    main()
