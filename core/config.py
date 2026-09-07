# -*- coding: utf-8 -*-
"""全局配置：目标域名、官网标识、页数、路径等"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 目标 ----
TARGET_DOMAIN = "elotouch.com.cn"
# 百度「官网标识」：来源行/标题出现的官方字样
BAIDU_OFFICIAL_MARKS = ["Elo®中国官网", "Elo®官方网站", "elotouch.com.cn"]

# ---- 抓取参数 ----
MAX_PAGES = 10              # 前 10 页
DELAY_MIN_MS = 2500         # 翻页间随机延迟下限
DELAY_MAX_MS = 5000         # 上限（防风控）
PAGE_WAIT_MS = 3000         # 页面加载后额外等待

# ---- 路径 ----
PROFILE_DIR = os.path.join(BASE_DIR, "profile")          # 浏览器用户目录（cookie 持久化）
# 截图放桌面独立目录（不放代码文件夹），按平台分子文件夹
SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "Elo官网检索截图")
# 引擎 → 平台子目录
SHOT_PLATFORM_DIR = {"baidu": "百度/PC", "baidu_m": "百度/MOB",
                     "bing": "必应/PC", "bing_m": "必应/MOB"}
DB_PATH = os.path.join(BASE_DIR, "results.db")
KEYWORDS_FILE = os.path.join(BASE_DIR, "keywords.txt")

# ---- 浏览器 ----
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1366, "height": 900}

# ---- 移动端浏览器（iPhone 14 Pro Max 参数，参考用户家里脚本）----
MOBILE_USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                     "Version/17.0 Mobile/15E148 Safari/604.1")
MOBILE_VIEWPORT = {"width": 430, "height": 932}
MOBILE_DSF = 3
MOBILE_PROFILE_DIR = os.path.join(BASE_DIR, "profile_mobile")  # 移动端独立 cookie 目录

# ---- 状态 ----
ST_PENDING = "pending"
ST_HIT = "hit"          # 命中
ST_NONE = "none"        # 10 页未命中
ST_ERROR = "error"
ST_RESTRICTED = "restricted"  # 搜索引擎合规过滤/受限页，无法判定
