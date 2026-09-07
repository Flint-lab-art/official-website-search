# Official Website Search（官网检索器）

批量关键词可见性监控工具：在 **百度 / 必应 × PC / 移动端（共 4 个平台）** 各搜索前 10 页，判定每个关键词是否出现目标官网（Elo / elotouch.com.cn），记录自然排名+页码，命中页整页截图，结果导出 Excel。带本地 Web 控制面板（浅色），支持断点续跑、验证码人工介入、按平台单独补跑。

## 功能

- **4 平台采集**：百度PC、必应PC、百度移动、必应移动，各自独立 cookie 目录（`profile/`、`profile_mobile/`）
- **Web 面板**（http://127.0.0.1:27531）：浅色界面，导入关键词（文件/粘贴）、一键开始/暂停/跳过/停止、实时日志、结果表格、导出 Excel、退出服务
- **平台选择**：导入关键词时勾选本次查哪些平台（默认全选），未勾选的平台不跑
- **单平台补跑**：表格每个引擎格有 ↻ 按钮，某平台出错时单独重置重跑，已命中的平台不受影响
- **按需开窗口**：只跑移动端只开移动窗口，只跑 PC 只开 PC 窗口，不浪费
- **百度判定**：只看自然结果来源行「Elo®中国官网 / Elo®官方网站」标识（AI 摘要保留排名位、正文提到官网不计命中；广告/推广条目跳过；插屏弹窗广告自动关闭）
- **必应判定**：结果 `cite` 含 `elotouch.com.cn` 域名即命中，广告 `li.b_ad` 跳过
- **命中即停**：任一平台命中即停止该平台翻页；10 页未出现判定为「无」，未命中不截图
- **命中页整页截图**：保存到桌面 `Elo官网检索截图/`，按平台分文件夹（`百度/PC`、`百度/MOB`、`必应/PC`、`必应/MOB`）
- **断点续跑**：结果写入 `results.db`（SQLite），中断/重启后自动只跑未完成的平台
- **验证码人工介入**：百度触发人机验证时暂停等待（最长 30 分钟），在浏览器窗口手动完成即自动继续；百度移动翻页必须点「下一页」按钮（`pn=` 直达会触发验证码）

## 快速开始

```powershell
# 1. 创建虚拟环境并安装依赖
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install playwright openpyxl
.\.venv\Scripts\python -m playwright install chromium

# 2. 启动面板（双击 start_panel.bat 或执行下面命令）
Start-Process .\.venv\Scripts\pythonw.exe -ArgumentList "server.py","--no-browser"

# 3. 浏览器打开 http://127.0.0.1:27531
#    导入关键词 → 勾选平台 → 开始
```

## 架构

```
server.py          Web 面板（http.server 纯标准库）：API + 前端页面 + Worker 调度
start_panel.bat    桌面双击启动器（已在跑则只开页面）
main.py            CLI 入口（备用，与面板同一套采集核心）
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
| 百度 PC | `#content_left div.c-container` | 容器内 `.ec-tuiguang` | 来源行/标题含「Elo®中国官网」「Elo®官方网站」 | 点击 `#page` 页码 |
| 百度移动 | `div.c-result` | 条目头部「广告/推广」标记 | 同上（AI 摘要保留排名位，正文不计命中） | 点击 `a.new-nextpage-only`「下一页」 |
| 必应 PC | `li.b_algo` | `li.b_ad`（兜底） | `cite` 或链接含 `elotouch.com.cn` | URL 参数 `&first=` |
| 必应移动 | `li.b_algo` | `li.b_ad`（兜底） | 同上 | URL 参数 `&first=` |

排名 = 过滤广告及特殊模块后的自然结果顺序（从 1 编号，AI 摘要占位）；命中的绝对排名记录为 `(页-1)×10 + 条序`。

## 使用说明

- 必须**有头模式**运行：浏览器窗口会显示，百度对 headless 会降级/拦截；跑任务时**不要关采集浏览器窗口**（可最小化）
- 首次运行百度可能触发人机验证：在浏览器窗口完成滑块/点选，脚本检测到后自动继续；百度移动首次大概率弹验证码，过 1-2 次后 cookie 积累（`profile_mobile/`）触发频率降低
- 数据落库后再次运行自动续跑（只跑未完成的平台）；清空重跑在面板点「清空」
- 截图目录：`C:\Users\EDY\Desktop\Elo官网检索截图\`（按 `百度/PC`、`百度/MOB`、`必应/PC`、`必应/MOB` 分文件夹）
- 结果表 `tasks` 字段：`baidu_*` / `bing_*` / `baidu_m_*` / `bing_m_*`（status/rank/page/evidence/shot 截图路径）+ `engines`（该词待跑平台）

## 环境

- Python 3.13 + Playwright + Chromium（Windows）
- 目标：`elotouch.com.cn`（可在 `core/config.py` 中修改 `TARGET_DOMAIN` / `BAIDU_OFFICIAL_MARKS` 适配其他品牌）
