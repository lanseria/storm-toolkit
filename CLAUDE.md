# CLAUDE.md

## Project Overview

Storm Toolkit 是一个**多源**台风追踪工具。每小时合并 zoom.earth + 浙江水利厅（CMA）数据，通过 Web UI 让用户选择关注哪些台风，对已关注台风持续记录**真实路径**与**多机构预测**，台风消亡后自动归档（仅保留真实路径）。Python 3.12+，使用 uv 管理依赖。

## Commands

```bash
# 安装依赖
uv sync

# 命令行工作流（脚本优先用 uv run python）
uv run python -m src.storm_toolkit.main                  # 默认启动 Web 服务（端口 19995）
uv run python -m src.storm_toolkit.main --web             # 显式启动 Web
uv run python -m src.storm_toolkit.main --schedule        # 启动定时同步循环（默认每 1 小时）
uv run python -m src.storm_toolkit.main --acquire         # 一次性抓取活跃列表 + 关注台风多源详情
uv run python -m src.storm_toolkit.main --list            # 打印当前活跃台风
uv run python -m src.storm_toolkit.main --reset-tracks    # 清空 data/tracks/*.json（保留 watchlist 与归档）
uv run python -m src.storm_toolkit.main --port 9000       # 指定 Web 端口

# 临时关闭 CMA 数据源（接口偶发不稳定时用）
CMA_ENABLED=0 uv run python -m src.storm_toolkit.main --acquire

# Docker 部署（同时启动 scheduler 和 web 两个容器）
docker compose build
docker compose up -d
docker compose logs -f web

# 自定义数据持久化路径（默认 ./data 相对 docker-compose.yml）
# 在 .env 中设置 DATA_HOST_DIR=/abs/path 后，所有数据落到该宿主机目录
# Windows: DATA_HOST_DIR=D:/storm-toolkit/data
# Linux:   DATA_HOST_DIR=/var/lib/storm-toolkit/data
```

## Architecture

### 数据流水线（一轮 `scheduler.run_once`）

1. **双源活跃列表抓取**:
   - `providers.ZoomEarthProvider.fetch_active()` → zoom.earth `/data/storms/?date=...`（UTC 截断到 6h）
   - `providers.ZJCmaProvider.fetch_active_raw()` → `https://typhoon.slt.zj.gov.cn/Api/TyhoonActivity`
2. **跨源合并** (`matcher.merge_active`): 按 zoom id 前缀（英文名）与 CMA `enname` 大小写不敏感匹配，给每个 zoom 项附加 `cma_tfid`；未匹配的 CMA 项作为 `cma-{tfid}` 独立条目追加
3. **多源详情抓取与合并** (`aggregator.fetch_combined_detail`): 对 watchlist 中每个 id：
   - 先抓 zoom 详情（实况 + zoom-earth 预测）
   - 按 zoom name（剥离括号注释，如 `Mekkhala (Francisco)` → `Mekkhala`）匹配 CMA tfid
   - 抓 CMA 详情，把其预测批（按机构 `tm` 拆批：cma/jma/jtwc/cwa/hko/kma）合并到 zoom 详情的 forecasts
4. **持久化** (`storage.save_storm_detail`):
   - `track_history[]` 按 `(date, source)` 去重追加实况
   - `forecasts[]` 按 `(source, issued_at)` 整批替换；每 source 仅保留最近 `FORECAST_BATCHES_KEEP` 批（默认 4）
5. **消亡归档**: 若 detail 的 `active=false`，调 `storage.archive_storm` 把 `tracks/{id}.json` 转为 `history/{id}.json`（**仅留 track_history，丢弃 forecasts**），从 watchlist 移除
6. **Web 展示** (`web/app.py`): FastAPI 提供 / 静态首页 + REST API；前端渲染活跃列表、关注台风（实况表 + 多源预测表）、历史归档

### 进程模型

- `--web`: 常驻 FastAPI 服务，响应用户选择关注 / 取消关注、查看路径与历史
- `--schedule`: 常驻循环进程，每 `SCHEDULE_INTERVAL_SECONDS` 秒（默认 3600）做一轮双源同步与归档判定
- 两个进程通过 `data/` 目录下的 JSON 文件通信（单写者，无锁但语义安全）
- `watchlist.json` 由 web 写、schedule 读；`storms_active.json` / `tracks/*.json` / `history/*.json` 由 schedule 写、web 读

### 核心模块

| 模块 | 职责 |
|------|------|
| `config.py` | 路径、zoom.earth/CMA URL、各源 HTTP 头（必须含 UA + Referer，否则 403）、调度间隔、Web 端口、CMA_ENABLED、FORECAST_BATCHES_KEEP |
| `utils.py` | logger、UTC↔BJT 转换、6h 截断、zoom.earth 日期格式化 |
| `models.py` | TypedDict：StormSummary / TrackPoint / TrackHistoryEntry / ForecastPoint / ForecastBatch / StormDetail |
| `providers/base.py` | `StormProvider` 抽象基类（fetch_active / fetch_detail / fetch_detail_by_name） |
| `providers/zoom_earth.py` | zoom.earth API 客户端，从 track 中拆出实况 vs 预测 |
| `providers/zj_cma.py` | 浙江水利厅 CMA API 客户端，按 `tm` 拆批，含 m/s→kt、strong→code、BJT→UTC 转换 |
| `matcher.py` | 跨源 ID 映射（英文名大小写不敏感匹配 zoom id 与 CMA enname） |
| `aggregator.py` | 多源详情合并（zoom 实况 + CMA 多机构预测），含括号注释清理 |
| `data_acquisition.py` | 兼容层：旧函数名委托给 ZoomEarthProvider（保留是为了不破坏现有 import） |
| `storage.py` | JSON 原子写入：watchlist / 活跃列表缓存 / 路径+预测 / 归档（仅实况） |
| `scheduler.py` | 定时同步循环：双源 active → 关注 detail → 实况/预测写入 / 消亡归档 |
| `web/app.py` | FastAPI + REST API + 静态前端 |
| `web/static/` | 原生 HTML/CSS/JS 前端（暗色主题，无构建步骤） |
| `main.py` | argparse CLI 入口：--web / --schedule / --acquire / --list / --reset-tracks |

### 关键设计

- **多源 Provider 抽象**: `StormProvider` 抽象基类 + 注册表 `PROVIDERS`，新增数据源只需新增一个 provider 模块并注册
- **数据源**: zoom.earth `/data/storms/` 内部 API（逆向 [sunshineplan/weather](https://github.com/sunshineplan/weather) Go 库）；CMA 浙江水利厅 `https://typhoon.slt.zj.gov.cn/Api/...`
- **必需 HTTP 头**: 两源都需 `User-Agent: Mozilla/5.0` + 各自的 `Referer`（zoom.earth / typhoon.slt.zj.gov.cn），否则 403
- **实况 vs 预测分离**: `tracks/{id}.json` 同时含 `track_history[]`（实况，仅 zoom.earth，3h 间隔）与 `forecasts[]`（多源批次）；CMA 的外层 points 不入实况表避免同时刻重复
- **预测批次**: `ForecastBatch = {source, issued_at, points[]}`，同 `(source, issued_at)` 整批替换；每 source 仅保留最近 4 批防膨胀
- **CMA 数据转换**: time 字符串按 BJT (+08:00) 解析后转 UTC；speed m/s × 1.943844 → kt；strong 中文 → code（D/S/1..5）；tm → source 标签（cma/jma/jtwc/cwa/hko/kma，其他走 other 通道）
- **跨源匹配**: zoom id `mekkhala-2026` 的前缀 `mekkhala` 与 CMA `enname` 大小写不敏感相等；剥离 zoom name 的括号注释（`Mekkhala (Francisco)` → `Mekkhala`）后再匹配
- **消亡判定与归档**: detail 的 `active=false` 即归档。`archive_storm` 把 `tracks/{id}.json` 转为 `history/{id}.json`（**仅保留 track_history**），从 watchlist 移除。两源都抓取失败时不归档（避免误删）
- **关注即抓**: `POST /api/watchlist/{id}` 同步调用 aggregator 多源合并落盘，用户立即看到实况与预测
- **schedule 容错**: 单源失败仅 log，不阻塞另一源；单周期异常不退出循环
- **原子写入**: 所有 JSON 写入先写 `.tmp` 再 `os.replace`，避免读写竞争产生半截文件
- **路径约定**: PROJECT_ROOT = `src/`，数据路径用 `PROJECT_ROOT.parent / "data"`

### 路径约定

- 源码在 `src/storm_toolkit/`，provider 在 `src/storm_toolkit/providers/`
- `data/watchlist.json`: `{"storm_ids": [...], "updated_at": "..."}`
- `data/storms_active.json`: `{"fetched_at": "...", "storms": [...]}`（含 sources/cma_tfid）
- `data/tracks/{id}.json`: `{"id":..., "info":{..., "cma_tfid":...}, "last_updated":..., "track_history":[...], "forecasts":[...]}`
- `data/history/{id}.json`: `{"id":..., "info":..., "archived_at":..., "track_history":[...]}`（**仅实况**，无 forecasts）
- 无需 `.env` 即可运行（所有配置有默认值），但可通过 `.env` 覆盖

## Code Style

- 使用中文注释
- 日志使用 `logging` 模块，logger 命名按模块区分（`storm_toolkit.xxx`，含子模块如 `storm_toolkit.providers.zoom_earth`）
- 类型注解使用 Python 3.12+ 语法（`str | None` 而非 `Optional[str]`）
- TypedDict 显式声明字段，不使用 dict 字面量传参
