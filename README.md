# Official Website Search

**Language / 语言**: [English](README.md) | [中文](README.zh-CN.md)

A batch keyword visibility monitoring tool: searches the **top 10 pages** on **Baidu / Bing × PC / Mobile (4 platforms in total)**, automatically determines whether the target official website appears for each keyword, records the organic rank and page number, takes full-page screenshots of hit pages, and exports results to Excel with one click. Includes a local web control panel with resume-from-breakpoint, manual CAPTCHA handling, and per-platform retry.

> For **brand owners / SEO teams**: regularly audit your official website's visibility and rankings on major search engines, and quantify "which keywords, which page and position, whether visible".

## Use Cases

- Brand owners monitoring the visibility and organic ranking of their official website on Baidu/Bing
- SEO teams batch-verifying keyword indexing and ranking fluctuations for core keywords
- Agencies periodically delivering official-site visibility/ranking reports to clients (screenshots as evidence)

## Key Features

- **4-platform collection**: Baidu PC, Bing PC, Baidu Mobile, Bing Mobile — each with an isolated cookie profile (`profile/`, `profile_mobile/`)
- **Web panel** (http://127.0.0.1:27531): light-themed UI, keyword import (file/paste), one-click start/pause/skip/stop, task timer (elapsed time + estimated time remaining based on average speed), live logs, results table, Excel export, quit service
- **Platform selection**: choose which platforms to run for newly imported keywords (all selected by default); unselected platforms are skipped
- **Per-platform retry**: each engine cell in the table has a ↻ button to reset and re-run a single platform when it errors, without affecting already-hit platforms
- **On-demand browser windows**: mobile-only runs open only the mobile window, PC-only opens only the PC window
- **Baidu detection**: only the source-line official-site marks in organic results count (see `BAIDU_OFFICIAL_MARKS` in `core/config.py`; AI summary keeps its ranking slot, mentions of the site in body text do not count as hits; ads/promoted entries are skipped; interstitial pop-up ads are auto-closed)
- **Bing detection**: a result whose `cite` contains the target domain (`TARGET_DOMAIN`) is a hit; ads `li.b_ad` are skipped; page 1 goes through "homepage → type → Enter" (removes direct-URL bias and keeps the native pager in screenshots)
- **Restricted-page detection**: when Bing's compliance filter page ("部分搜索结果未予显示") has no organic results to judge, it is marked "restricted" and stops paging — distinguished from "not found"
- **Stop on hit**: any hit stops paging for that platform; no appearance in 10 pages is judged "none"; no screenshot for non-hits
- **Full-page screenshot on hit**: saved to `Elo官网检索截图/` on the desktop, organized by platform folders (`百度/PC`, `百度/MOB`, `必应/PC`, `必应/MOB`); the native pager is captured; waits for async rendering (Bing) and AI-stream output (Baidu) before shooting to avoid blank or half-generated captures
- **Custom export**: pick columns before export (status/rank/page/evidence/screenshot — one selection applies to all 4 platforms); each platform gets its own sheet (with keyword column) and a summary sheet first; auto column width, dark-blue header, status coloring (hit green / none gray / error red / restricted orange), frozen header row, auto-filter; screenshot column holds only filenames (clickable)
- **Resume from breakpoint**: results are written to `results.db` (SQLite); after interruption/restart, only unfinished platforms are re-run
- **CAPTCHA with human-in-the-loop**: when Baidu triggers CAPTCHA, the run pauses (up to 30 min) — complete it manually in the browser window and it resumes automatically; Baidu Mobile must page via the "next page" button (`pn=` direct URLs trigger CAPTCHA)
- **One-click health check in panel**: click "健康检查" in the top-right of the log area to verify the parsing/detection logic still works with a real browser; results stream into the log area (mutually exclusive with collection)

## Quick Start

**Windows**

```powershell
# 1. Install dependencies (UV preferred, pip fallback; creates .venv, downloads Chromium)
#    Double-click scripts\install.bat, or run manually:
#    With uv (recommended - versions locked by uv.lock):
uv sync
uv run playwright install chromium
#    Without uv:
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium

# 2. Start the panel (double-click the desktop "启动官网检索面板.bat", or run scripts\start_panel.bat)
# 3. Open http://127.0.0.1:27531 in a browser
#    Import keywords → choose platforms → Start
```

**Linux / macOS**

```bash
# 1. Install dependencies (creates .venv, installs from requirements.txt, downloads Chromium)
bash scripts/install.sh

# 2. Start the panel (opens browser if already running; otherwise starts in background and waits)
bash scripts/start.sh

# 3. Open http://127.0.0.1:27531 in a browser
```

> New machine: just install Python 3.10+, then run the platform's `scripts/install.*` — dependencies are installed automatically.

> Scripts live in `scripts/`: `install.bat` + `start_panel.bat` (Windows), `install.sh` + `start.sh` (Linux/macOS; macOS uses `open`, Linux uses `xdg-open`).

## Architecture

```
server.py          Web panel (pure-stdlib http.server): API + frontend page + Worker scheduling
scripts/           install.bat/install.sh (dependency setup) + start.sh/start_panel.bat (launch)
main.py            CLI entry (fallback, shares the same collection core)
healthcheck.py     Health check: verifies parsing/detection logic with a real browser (DOM unchanged)
pick_p1_shots.py   Picks "page-1 hit" screenshots: filters page=1 hits from results.db, copies them into `Elo官网检索截图/第一页命中/` (per-platform folders) and writes a manifest txt
tests/             Unit tests (pytest): config/detection/db
pyproject.toml     UV dependency entry (uv sync / uv.lock; requirements.txt kept as pip fallback)
uv.lock            Locked exact versions (generated by uv lock)
core/config.py     Target domain, official-site marks, page counts, paths, browser options
core/db.py         SQLite task table (with engines column) + resume + per-platform retry
core/engine.py     Playwright singleton + browser sessions (PC/mobile) + CAPTCHA detection/wait
core/baidu.py      Baidu PC collector
core/baidu_m.py    Baidu mobile collector (pop-up closing, AI-summary keeps ranking slot)
core/bing.py       Bing PC collector
core/bing_m.py     Bing mobile collector (page 1 via homepage input to avoid search bias)
docs/              Requirement sample images (detection-standard references)
dev/               Dev sampling/testing scripts (git-ignored)
```

Detection rules:

| Platform | Result container | Ad detection | Hit condition | Paging |
|---|---|---|---|---|
| Baidu PC | `#content_left div.c-container` | `.ec-tuiguang` in container | source line/title contains an official-site mark (`BAIDU_OFFICIAL_MARKS`) | click `#page` page numbers |
| Baidu Mobile | `div.c-result` | "广告/推广" marker at entry head | same as above (AI summary keeps its slot; body mentions don't count) | click `a.new-nextpage-only` "下一页" |
| Bing PC | `li.b_algo` | `li.b_ad` (fallback) | `cite` or link contains the target domain (`TARGET_DOMAIN`) | P1 homepage input+Enter; P2+ URL `&first=` |
| Bing Mobile | `li.b_algo` | `li.b_ad` (fallback) | same as above | URL param `&first=` |

Rank = organic result order after filtering ads and special modules (numbered from 1; AI summary occupies a slot); the absolute rank of a hit is recorded as `(page-1)×10 + position`. When Bing hits a compliance filter (restricted page), it is marked "restricted" without paging 10 pages or judging "not found".

## Usage Notes

- Must run in **headed mode**: the browser window is visible; Baidu downgrades/blocks headless; don't close the collection browser window while running (you may minimize it)
- Baidu may trigger CAPTCHA on first run: complete the slider/click in the browser window and the script resumes automatically; Baidu Mobile likely shows a CAPTCHA on the first 1–2 runs, then cookie accumulation (`profile_mobile/`) reduces frequency
- Re-running after data is stored resumes automatically (only unfinished platforms); use "清空" in the panel to clear and re-run everything
- "受限" (restricted) on Bing = the keyword triggered Bing's compliance filter (部分搜索结果未予显示) and the page has no organic results to judge — this is not "not found"; re-run later / on another network or check manually
- Screenshot directory: desktop `Elo官网检索截图/` (folders: `百度/PC`, `百度/MOB`, `必应/PC`, `必应/MOB`)
- When the client needs "hit on page 1" evidence: after a full run execute `python pick_p1_shots.py` — it collects page-1 hit screenshots into `Elo官网检索截图/第一页命中/` (per-platform folders + manifest txt, ready to deliver)
- `tasks` table columns: `baidu_*` / `bing_*` / `baidu_m_*` / `bing_m_*` (status/rank/page/evidence/shot path) + `engines` (platforms still pending for that keyword)

## Health Check

Search engines occasionally change their layout; if unnoticed, a broken parser can be misreported as "official site not ranked". Run a health check periodically to verify the parsing logic still works with a real browser:

- **Panel**: click the "健康检查" button in the toolbar (runs Baidu PC + Bing PC), results stream into the log area
- **CLI** (can be hooked into cron/CI):

```bash
python healthcheck.py                     # default: Baidu PC + Bing PC
python healthcheck.py --platforms all     # all 4 platforms
python healthcheck.py --captcha-wait 180  # CAPTCHA human-wait seconds (default 120)
```

- Sample words: 2 hit words + 1 negative sample, page 1 only
- Verdict: `PASS` parser OK (also shows whether the official site hit) | `WARN` Bing restricted page (platform filter; restricted detection itself works) | `FAIL` 0 results parsed on page 1 — possible redesign, inspect the parser under `core/`
- Exit code: 0 = no FAIL; 1 = at least one FAIL (usable in cron/CI)

## Testing

```bash
.\.venv\Scripts\python -m pytest tests -v    # Windows
./.venv/bin/python -m pytest tests -v        # Linux / macOS
```

Covers: config constants, Baidu PC/mobile official-site mark detection, Bing domain detection, db task table/resume/per-platform retry. No browser dependency; finishes in seconds.

## Adapting for Another Brand (Forking)

The detection rules are configured for an example brand. To monitor another brand, change 3 things:

1. `core/config.py`: `TARGET_DOMAIN` (target domain), `BAIDU_OFFICIAL_MARKS` (the source-line official-site marks, e.g. "XX品牌官网"), `SCREENSHOT_DIR` (screenshot directory)
2. `keywords.txt`: your own keyword list (one per line; UTF-8 or GBK, auto-detected)
3. `README.md`: title and use-case description

Then run `scripts/install.*` to install dependencies. The panel, resume, custom export, health check and unit tests are all brand-agnostic and work out of the box.

## Environment

- Python 3.10+ / Playwright / Chromium (Windows / Linux / macOS)
- Target: configured with an example brand by default (`TARGET_DOMAIN` / `BAIDU_OFFICIAL_MARKS` in `core/config.py`); replace with your own brand after forking

## Compliance

- This tool is intended only for monitoring the search visibility of **your own or authorized** websites — do not use it to scrape others' data, invade privacy, or violate target platform terms of service
- CAPTCHAs are completed **manually in the browser window**; no platform security mechanism is bypassed
- Browser login state / cookies stay local only (`profile/`, `profile_mobile/` are git-ignored) and are never uploaded; `results.db` and screenshots likewise stay out of the repository

## License

[MIT](LICENSE)
