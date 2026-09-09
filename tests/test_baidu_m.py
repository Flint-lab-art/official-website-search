"""百度移动端官网标识判定测试"""

from core.baidu_m import _is_hit


def test_hit_source():
    assert _is_hit("Elo®中国官网", "医疗级触控显示器")


def test_hit_title():
    assert _is_hit("", "Elo®官方网站 | 触控屏")


def test_hit_domain():
    assert _is_hit("elotouch.com.cn", "")


def test_miss():
    assert not _is_hit("某品牌官网", "开架式触控显示器")


def test_miss_empty():
    assert not _is_hit("", "")
