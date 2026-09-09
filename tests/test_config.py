"""config 常量测试"""

from core import config


def test_target_domain():
    assert config.TARGET_DOMAIN == "elotouch.com.cn"


def test_official_marks_cover_domain():
    assert "elotouch.com.cn" in config.BAIDU_OFFICIAL_MARKS


def test_status_constants_distinct():
    sts = {
        config.ST_PENDING,
        config.ST_HIT,
        config.ST_NONE,
        config.ST_ERROR,
        config.ST_RESTRICTED,
        config.ST_MALFUNCTION,
    }
    assert len(sts) == 6


def test_platform_dirs_cover_4_engines():
    assert len(config.SHOT_PLATFORM_DIR) == 4
    for eng in ("baidu", "bing", "baidu_m", "bing_m"):
        assert eng in config.SHOT_PLATFORM_DIR


def test_meltdown_threshold_reasonable():
    assert config.ZERO_RESULT_BREAK >= 2


def test_max_pages_positive():
    assert config.MAX_PAGES >= 1
