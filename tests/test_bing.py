"""必应域名命中判定测试"""

from core.bing import _is_domain_hit


def test_hit_cite():
    assert _is_domain_hit({"cite": "www.elotouch.com.cn", "href": ""})


def test_hit_href():
    assert _is_domain_hit({"cite": "", "href": "https://elotouch.com.cn/products"})


def test_hit_cite_subpath():
    assert _is_domain_hit({"cite": "elotouch.com.cn/medi", "href": ""})


def test_miss():
    assert not _is_domain_hit({"cite": "www.example.com", "href": "https://example.com"})


def test_miss_empty():
    assert not _is_domain_hit({"cite": "", "href": ""})
