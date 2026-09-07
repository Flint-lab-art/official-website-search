# Official Website Search（官网检索器）

批量关键词监控工具：用 **百度 + 必应** 各搜索前 10 页，判定每个关键词是否出现目标官网（Elo / elotouch.com.cn），记录命中排名并截取命中页全图。

## 功能

- 输入一批关键词（`keywords.txt`，每行一个），自动逐词执行百度、必应搜索
- **百度**：过滤广告、AI 卡片、相关搜索后，检测自然结果中的「Elo®中国官网 / Elo®官方网站」标识，记录自然排名
- **必应**：过滤广告后，在结果 `cite` 中匹配 `elotouch.com.cn` 域名，记录自然排名
- **命中即停**：任一引擎找到即停止翻页；10 页均未出现判定为「无」
- **命中页全图截图**：保存到 `screenshots/`（未命中不截图）
- **断点续跑**：结果写入 `results.db`（SQLite），中断/重启后自动跳过已完成关键词
- **验证码人工介入**：百度触发人机验证时自动暂停，在浏览器窗口手动完成后自动继续（cookie 持久化于 `profile/`，后续触发频率降低）

## 架构

```
main.py            CLI 入口：加载关键词 → 逐词跑双引擎 → 入库
core/config.py     目标域名、官网标识、页数、延迟等配置
core/db.py         SQLite 任务表 + 断点续跑
core/engine.py     浏览器会话（持久化 profile）+ 验证码检测/等待
core/baidu.py      百度采集：过滤 → 标识检测 → 翻页 → 截图
core/bing.py       必应采集：过滤 → 域名匹配 → 翻页 → 截图
docs/              需求样例图（判定标准参考）
```

判定口径：

| 引擎 | 结果容器 | 广告识别 | 命中条件 | 翻页 |
|---|---|---|---|---|
| 百度 | `#content_left div.c-container` | 容器内 `.ec-tuiguang` | 来源行/标题含「Elo®中国官网」「Elo®官方网站」 | 点击 `#page` 页码 |
| 必应 | `li.b_algo` | `li.b_ad`（兜底） | `cite` 或链接含 `elotouch.com.cn` | URL 参数 `&first=` |

排名 = 过滤广告及特殊模块后的自然结果顺序（从 1 编号），命中的绝对排名记录为 `(页-1)×10 + 条序`。

## 快速开始

```powershell
# 1. 创建虚拟环境并安装依赖
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install playwright
.\.venv\Scripts\python -m playwright install chromium

# 2. 准备关键词
#    编辑 keywords.txt，每行一个关键词

# 3. 运行
.\.venv\Scripts\python main.py
```

## 使用说明

- 必须**有头模式**运行：浏览器窗口会显示，百度对 headless 会降级/拦截
- 首次运行百度可能触发人机验证：在浏览器窗口完成滑块/点选，脚本自动检测到后继续
- 数据落库后再次运行自动续跑；清空重跑删除 `results.db` 即可
- 结果表结构：`tasks` 表，字段含 `baidu_status/baidu_rank/baidu_page/baidu_evidence`、`bing_*` 对应列、`baidu_shot/bing_shot` 截图路径

## 环境

- Python 3.13 + Playwright + Chromium（Windows）
- 目标：`elotouch.com.cn`（可在 `core/config.py` 中修改 `TARGET_DOMAIN` / `BAIDU_OFFICIAL_MARKS` 适配其他品牌）
