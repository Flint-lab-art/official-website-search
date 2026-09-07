# Official Website Search（官网检索器）

**语言 / Language**：[中文](README.zh-CN.md) | [English](README.md)

批量关键词可见性监控工具：在 **百度 / 必应 × PC / 移动端（共 4 个平台）** 各搜索前 10 页，自动判定每个关键词是否出现目标官网，记录自然排名与页码，命中页整页截图，结果一键导出 Excel。带本地 Web 控制面板，支持断点续跑、验证码人工介入、按平台单独补跑。

> 面向 **品牌方 / SEO 团队**：定期巡检自家官网在主流搜索引擎的曝光与排名，量化「搜哪些词、排第几页第几位、是否可见」。

## 适用场景

- 品牌方监测自家官网在百度/必应的可见性与自然排名
- SEO 团队批量核对核心关键词的收录与排名波动
- 代理商为客户定期输出官网曝光/排名报告（截图即证据）

## 核心特性

- **4 平台采集**：百度PC、必应PC、百度移动、必应移动，各自独立 cookie 目录（`profile/`、`profile_mobile/`）
- **Web 面板**（http://127.0.0.1:27531）：浅色界面，导入关键词（文件/粘贴）、一键开始/暂停/跳过/停止、任务计时（已用时间 + 按平均速度推算的预计剩余）、实时日志、结果表格、导出 Excel、退出服务
- **平台选择**：导入关键词时勾选本次查哪些平台（默认全选），未勾选的平台不跑
- **单平台补跑**：表格每个引擎格有 ↻ 按钮，某平台出错时单独重置重跑，已命中的平台不受影响
- **按需开窗口**：只跑移动端只开移动窗口，只跑 PC 只开 PC 窗口，不浪费
- **百度判定**：只看自然结果来源行的官网标识（见 `core/config.py` 的 `BAIDU_OFFICIAL_MARKS`；AI 摘要保留排名位、正文提到官网不计命中；广告/推广条目跳过；插屏弹窗广告自动关闭）
- **必应判定**：结果 `cite` 含目标官网域名（`TARGET_DOMAIN`）即命中，广告 `li.b_ad` 跳过；P1 走「首页→输入→回车」（消除直达 URL 偏差，原生页码条正常渲染入截图）
- **受限页检测**：必应合规过滤页（「部分搜索结果未予显示」）无自然结果可判定时，标记「受限」状态并停止翻页，与「未命中」区分
- **命中即停**：任一平台命中即停止该平台翻页；10 页未出现判定为「无」，未命中不截图
- **命中页整页截图**：保存到桌面 `Elo官网检索截图/`，按平台分文件夹（`百度/PC`、`百度/MOB`、`必应/PC`、`必应/MOB`），页面原生页码条会入镜；截图前等渲染完成（必应异步渲染、百度 AI 摘要流式输出完，避免白屏/截到一半）
- **自定义导出**：导出前选择字段（状态/排名/页码/依据/截图，一次勾选四平台生效）；每个平台一个 sheet（含关键词列），汇总 sheet 置首；自动列宽、表头深蓝、状态着色（命中绿/未命中灰/错误红/受限橙）、冻结首行、自动筛选；截图列只放文件名（可点击打开）
- **断点续跑**：结果写入 `results.db`（SQLite），中断/重启后自动只跑未完成的平台
- **验证码人工介入**：百度触发人机验证时暂停等待（最长 30 分钟），在浏览器窗口手动完成即自动继续；百度移动翻页必须点「下一页」按钮（`pn=` 直达会触发验证码）
- **面板一键健康检查**：点日志区右上角「健康检查」，真实浏览器跑样本词验证解析/判定逻辑仍有效，结果实时输出到日志区（与采集互斥，正在跑任务时不可用）

## 快速开始

**Windows**

```powershell
# 1. 安装依赖（自动建 .venv + 按 requirements.txt 装包 + Chromium）
#    双击 scripts\install.bat，或命令行执行：
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium

# 2. 启动面板（双击桌面「启动官网检索面板.bat」，或运行 scripts\start_panel.bat）
# 3. 浏览器打开 http://127.0.0.1:27531
#    导入关键词 → 勾选平台 → 开始
```

**Linux / macOS**

```bash
# 1. 安装依赖（自动建 .venv + 按 requirements.txt 装包 + Chromium）
bash scripts/install.sh

# 2. 启动面板（已在跑则直接开浏览器；否则后台启动并等待就绪）
bash scripts/start.sh

# 3. 浏览器打开 http://127.0.0.1:27531
```

> 换新机器：只需装好 Python 3.10+，然后运行对应平台的 `scripts/install.*`，依赖自动装齐。

> 脚本位于 `scripts/`：`install.bat` + `start_panel.bat`（Windows）、`install.sh` + `start.sh`（Linux/macOS 通用，macOS 用 `open`、Linux 用 `xdg-open` 开浏览器）。

## 架构

```
server.py          Web 面板（http.server 纯标准库）：API + 前端页面 + Worker 调度
scripts/           install.bat/install.sh（依赖安装）+ start.sh/start_panel.bat（启动）
main.py            CLI 入口（备用，与面板同一套采集核心）
healthcheck.py     健康检查：真实浏览器验证解析/判定逻辑仍有效（DOM 未改版）
pick_p1_shots.py   挑取「第一页命中」截图：从 results.db 筛 page=1 且命中的记录，把截图归集到 `Elo官网检索截图/第一页命中/`（按平台分子文件夹）并生成清单 txt
tests/             单元测试（pytest）：配置/判定/db
requirements.txt   依赖清单（playwright / openpyxl / pytest）
core/config.py     目标域名、官网标识、页数、路径、浏览器参数
core/db.py         SQLite 任务表（含 engines 列）+ 断点续跑 + 单平台重跑
core/engine.py     Playwright 实例单例 + 浏览器会话（PC/移动）+ 验证码检测/等待
core/baidu.py      百度 PC 采集
core/baidu_m.py    百度移动采集（弹窗关闭、AI 摘要保留排名位）
core/bing.py       必应 PC 采集
core/bing_m.py     必应移动采集（P1 走首页输入防搜索偏差）
docs/              需求样例图（判定标准参考）
dev/               开发采样/测试脚本（git 忽略）
```

判定口径：

| 平台 | 结果容器 | 广告识别 | 命中条件 | 翻页 |
|---|---|---|---|---|
| 百度 PC | `#content_left div.c-container` | 容器内 `.ec-tuiguang` | 来源行/标题含官网标识（`BAIDU_OFFICIAL_MARKS`） | 点击 `#page` 页码 |
| 百度移动 | `div.c-result` | 条目头部「广告/推广」标记 | 同上（AI 摘要保留排名位，正文不计命中） | 点击 `a.new-nextpage-only`「下一页」 |
| 必应 PC | `li.b_algo` | `li.b_ad`（兜底） | `cite` 或链接含目标官网域名（`TARGET_DOMAIN`） | P1 首页输入回车；P2+ URL `&first=` |
| 必应移动 | `li.b_algo` | `li.b_ad`（兜底） | 同上 | URL 参数 `&first=` |

排名 = 过滤广告及特殊模块后的自然结果顺序（从 1 编号，AI 摘要占位）；命中的绝对排名记录为 `(页-1)×10 + 条序`。必应触发合规过滤（受限页）时标记「受限」，不翻 10 页、不判定为未命中。

## 使用说明

- 必须**有头模式**运行：浏览器窗口会显示，百度对 headless 会降级/拦截；跑任务时**不要关采集浏览器窗口**（可最小化）
- 首次运行百度可能触发人机验证：在浏览器窗口完成滑块/点选，脚本检测到后自动继续；百度移动首次大概率弹验证码，过 1-2 次后 cookie 积累（`profile_mobile/`）触发频率降低
- 数据落库后再次运行自动续跑（只跑未完成的平台）；清空重跑在面板点「清空」
- 必应显示「受限」= 该词触发必应合规过滤（部分搜索结果未予显示），页面无自然结果可判定——不是未命中，可换时间/网络重跑或人工核对
- 截图目录：桌面 `Elo官网检索截图/`（按 `百度/PC`、`百度/MOB`、`必应/PC`、`必应/MOB` 分文件夹）
- 客户要「第一页就命中」的证据时：跑完全量后执行 `python pick_p1_shots.py`，自动把第 1 页命中的截图归集到 `Elo官网检索截图/第一页命中/`（按平台分文件夹 + 清单 txt，可直接交付）
- 结果表 `tasks` 字段：`baidu_*` / `bing_*` / `baidu_m_*` / `bing_m_*`（status/rank/page/evidence/shot 截图路径）+ `engines`（该词待跑平台）

## 健康检查

搜索引擎改版可能导致选择器失效，若不察觉会把「解析器坏了」误判成「官网没上榜」。定期跑一次健康检查，用真实浏览器验证解析逻辑仍有效：

- **面板**：工具栏点「健康检查」按钮（跑 百度PC + 必应PC），结果实时输出到日志区
- **命令行**（可接定时任务/CI）：

```bash
python healthcheck.py                     # 默认检查 百度PC + 必应PC
python healthcheck.py --platforms all     # 全部 4 平台
python healthcheck.py --captcha-wait 180  # 验证码人工等待秒数（默认 120）
```

- 样本词：2 个命中词 + 1 个负样本，只查第 1 页
- 结论：`PASS` 解析正常（顺带显示是否命中官网）｜`WARN` 必应受限页（平台过滤，受限检测本身正常）｜`FAIL` 第 1 页解析出 0 条——疑似改版，需人工核查 `core/` 下的解析器
- 退出码：0 = 无 FAIL；1 = 存在 FAIL（可用于定时任务/CI 判断）

## 测试

```bash
.\.venv\Scripts\python -m pytest tests -v    # Windows
./.venv/bin/python -m pytest tests -v        # Linux / macOS
```

覆盖：配置常量、百度 PC/移动官网标识判定、必应域名判定、db 任务表/断点续跑/单平台重跑。无浏览器依赖，秒级跑完。

## 适配其他品牌（Fork 使用）

本工具默认按示例品牌配置判定口径，监控其他品牌只需改 3 处：

1. `core/config.py`：`TARGET_DOMAIN`（目标域名）、`BAIDU_OFFICIAL_MARKS`（百度来源行的官网标识，如「XX品牌官网」）、`SCREENSHOT_DIR`（截图保存目录）
2. `keywords.txt`：换成自己的关键词清单（每行一个，UTF-8/GBK 均可，程序自动识别）
3. `README.md`：标题与适用场景描述

改完运行 `scripts/install.*` 安装依赖即可使用。面板、断点续跑、自定义导出、健康检查、单元测试均与具体品牌无关，开箱即用。

## 环境

- Python 3.10+ / Playwright / Chromium（Windows / Linux / macOS）
- 目标：默认配置为示例品牌（`core/config.py` 的 `TARGET_DOMAIN` / `BAIDU_OFFICIAL_MARKS`），fork 后替换为自有品牌

## 合规声明

- 本工具仅用于监测**自有或已授权**网站的搜索可见性，请勿用于爬取他人数据、侵犯隐私或违反目标平台服务条款
- 遇到人机验证时由**人工在浏览器窗口完成**，不绕过任何平台的安全机制
- 浏览器登录态 / cookie 仅保存在本地（`profile/`、`profile_mobile/` 已 git 忽略），不会上传；`results.db`、`screenshots/` 等运行数据同样不进入仓库

## License

[MIT](LICENSE)
