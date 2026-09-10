"""cookie_tools 单元测试：体积估算、裁剪策略（保留核心身份、丢弃追踪域）、阈值触发。"""

from core import cookie_tools as ct


def _ck(name, domain=".bing.com", value="v"):
    return {"name": name, "value": value, "domain": domain, "path": "/"}


class FakeContext:
    """模拟 Playwright sync API 的 BrowserContext 的 Cookie 接口"""

    def __init__(self, cookies):
        self._cookies = list(cookies)
        self.cleared = False
        self.added = None

    def cookies(self):
        return list(self._cookies)

    def clear_cookies(self):
        self.cleared = True
        self._cookies = []

    def add_cookies(self, cookies):
        self.added = list(cookies)
        self._cookies = list(cookies)


def test_cookie_bytes_counts_name_value_domain_path():
    ck = _ck("MUID", value="1234567890")
    total = ct.cookie_bytes([ck])
    assert total >= len("MUID") + 10 + len(".bing.com") + len("/") + 32
    assert total < len("MUID") + 10 + len(".bing.com") + len("/") + 33


def test_trim_keeps_core_and_drops_tracking():
    cookies = [
        _ck("MUID", value="a" * 50),               # 核心身份 → 保留
        _ck("SRCHHPGUSR", value="b" * 50),         # 核心身份 → 保留
        _ck("BAIDUID", domain=".baidu.com", value="c" * 50),  # 百度核心 → 保留
        _ck("_ga", domain=".google-analytics.com"),  # 追踪域 → 丢
        _ck("IDE", domain=".doubleclick.net"),       # 追踪域 → 丢
        _ck("random_track", value="d" * 50),         # 非核心 → 丢
    ]
    ctx = FakeContext(cookies)
    ct.trim_cookies(ctx)

    names = {c["name"] for c in ctx._cookies}
    assert names == {"MUID", "SRCHHPGUSR", "BAIDUID"}
    assert ctx.cleared is True
    assert ctx.added is not None


def test_trim_keeps_prefix_matches():
    # 前缀匹配：SRCH 开头的各类 cookie 都应保留
    cookies = [
        _ck("SRCHHPGUSR"),
        _ck("SRCHD"),
        _ck("SRCHUSR"),
        _ck("MUIDB"),
    ]
    ctx = FakeContext(cookies)
    ct.trim_cookies(ctx)
    assert {c["name"] for c in ctx._cookies} == {"SRCHHPGUSR", "SRCHD", "SRCHUSR", "MUIDB"}


def test_ensure_noop_when_small():
    cookies = [_ck("MUID", value="x")]  # 体积很小
    ctx = FakeContext(cookies)
    ct.ensure_cookie_size(ctx)
    assert ctx.cleared is False  # 未触发任何裁剪


def test_ensure_trims_when_over_limit(monkeypatch):
    # 把裁剪线压低，构造超限场景
    monkeypatch.setattr(ct, "COOKIE_WARN_BYTES", 10)
    monkeypatch.setattr(ct, "COOKIE_TRIM_BYTES", 20)
    cookies = [
        _ck("MUID", value="m" * 40),
        _ck("_ga", domain=".google-analytics.com", value="g" * 40),
    ]
    ctx = FakeContext(cookies)
    before = ct.cookie_bytes(ctx._cookies)
    after = ct.ensure_cookie_size(ctx, trim_bytes=20)  # trim_bytes 是默认参数，须显式传
    assert after < before  # 已裁剪，体积下降
    assert {c["name"] for c in ctx._cookies} == {"MUID"}
