"""百度 PC 官网标识判定测试"""

from core.baidu import _is_elo_official


def test_hit_source_mark():
    assert _is_elo_official("医疗级触控显示器", "Elo®中国官网")


def test_hit_official_name_in_title():
    assert _is_elo_official("Elo®官方网站", "")


def test_hit_domain():
    assert _is_elo_official("触摸屏", "www.elotouch.com.cn")


def test_miss_unrelated():
    assert not _is_elo_official("触控一体机", "某某品牌官网")


def test_miss_empty():
    assert not _is_elo_official("", "")


def test_miss_partial_word():
    # 不能因包含 "elo" 子串误判（标识是完整商标词）
    assert not _is_elo_official("eloqua 营销软件", "某官网")
