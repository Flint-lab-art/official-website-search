"""Cookie 体积管理。

持久化 profile 跑久了会积累大量第三方 Cookie（追踪域），HTTP header 接近
8KB 上限时搜索引擎可能拒绝请求/返回异常，只能靠重启清 Cookie 缓解。
本模块在任务开始前检查 Cookie 体积，超阈值才裁剪：丢弃追踪域 + 非核心身份，
保留核心身份 Cookie（MUID/SRCH/BAIDUID/BDUSS 等），避免影响登录态。

注：项目用 Playwright sync API，此处为同步实现。
"""

from . import logger

# 阈值：HTTP header 一般 8KB 上限，Cookie 只是其中一部分
COOKIE_WARN_BYTES = 6000  # 预警线，超过就打印日志
COOKIE_TRIM_BYTES = 7000  # 裁剪线，超过就动手

# 要保留的核心身份 Cookie 前缀（Bing 国内版常见）
KEEP_PREFIXES = (
    "MUID",
    "SRCH",
    "_EDGE",
    "SRCHHPGUSR",
    "SUID",
    "MR",
    "BAIDUID",
    "BDUSS",  # 百度常用
    "NID",
    "SID",  # 其他 Bing/Google 系
)

# 明确要丢弃的追踪域（第三方埋点，体积增长重灾区）
DROP_DOMAINS = (
    ".doubleclick.net",
    ".google-analytics.com",
    ".clarity.ms",
    ".c.bing.com",
    ".bat.bing.com",
    ".facebook.com",
)


def cookie_bytes(cookies):
    """估算一组 Cookie 序列化后的大小（name+value+基本属性）"""
    total = 0
    for c in cookies:
        total += len(c.get("name", "")) + len(c.get("value", ""))
        total += len(c.get("domain", "")) + len(c.get("path", ""))
        total += 32  # 属性字段的粗略开销
    return total


def get_cookie_size(context):
    """返回 (总字节数, cookie 个数)"""
    cookies = context.cookies()
    return cookie_bytes(cookies), len(cookies)


def trim_cookies(context, keep_prefixes=KEEP_PREFIXES, drop_domains=DROP_DOMAINS):
    """裁剪 Cookie：丢弃追踪域 + 非核心前缀，保留核心身份"""
    cookies = context.cookies()
    before_bytes = cookie_bytes(cookies)

    keep, drop = [], []
    for c in cookies:
        domain = c.get("domain", "")
        name = c.get("name", "")

        if any(domain.endswith(d) for d in drop_domains):
            drop.append(c)
            continue
        if any(name.startswith(p) for p in keep_prefixes):
            keep.append(c)
        else:
            drop.append(c)

    if drop:
        context.clear_cookies()
        context.add_cookies(keep)

    after_bytes = cookie_bytes(keep)
    logger.debug(
        "cookie",
        f"裁剪: {len(cookies)} -> {len(keep)} 个, {before_bytes} -> {after_bytes} 字节",
    )
    # 打印被丢的大块，方便找膨胀源头
    for c in sorted(drop, key=lambda x: len(x.get("value", "")), reverse=True)[:5]:
        logger.debug(
            "cookie",
            f"  drop: {c.get('domain')} {c.get('name')} len={len(c.get('value', ''))}",
        )
    return after_bytes


def ensure_cookie_size(context, trim_bytes=COOKIE_TRIM_BYTES):
    """任务开始前调用：只有超阈值才裁剪，正常情况什么都不做"""
    size, count = get_cookie_size(context)

    if size < COOKIE_WARN_BYTES:
        return size  # 正常，不动

    if size < trim_bytes:
        logger.debug("cookie", f"预警: {size} 字节 / {count} 个，接近上限")
        return size

    logger.debug("cookie", f"超限: {size} 字节 / {count} 个，开始裁剪")
    return trim_cookies(context)
