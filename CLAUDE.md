# CLAUDE.md

## Project Overview

Storm Toolkit 是一个基于 zoom.earth 数据的台风追踪工具。它定时抓取全球活跃台风列表，通过 Web UI 让用户选择关注哪些台风，对已关注的台风每小时拉取完整路径详情（位置、风速、气压、等级），追加保存为 JSON 历史。Python 3.12+，使用 uv 管理依赖。

## Commands

```bash
# 安装依赖
uv sync

# 命令行工作流
python -m src.storm_toolkit.main                  # 默认启动 Web 服务（端口 8000）
python -m src.storm_toolkit.main --web             # 显式启动 Web
python -m src.storm_toolkit.main --schedule        # 启动定时同步循环（每 30 分钟）
python -m src.storm_toolkit.main --acquire         # 一次性抓取活跃列表 + 关注台风详情
python -m src.storm_toolkit.main --list            # 打印当前活跃台风
python -m src.storm_toolkit.main --port 9000       # 指定 Web 端口

# Docker 部署（同时启动 scheduler 和 web 两个容器）
docker compose build
docker compose up -d
docker compose logs -f web
```

## Architecture

### 数据流水线

1. **活跃列表抓取** (`data_acquisition.fetch_active_storms`): 调用 `https://zoom.earth/data/storms/?date=YYYY-MM-DDTHH:MMZ`（时间 UTC 截断到 6h），返回当前所有命名风暴 + 扰动 ID
2. **关注台风详情抓取** (`data_acquisition.fetch_storm_detail`): 对 watchlist 中每个 ID 调用 `?id={id}`，获取完整路径数组
3. **路径历史持久化** (`storage.append_storm_track`): 以 track point 的 `date` 为主键去重追加到 `data/tracks/{id}.json`，同时更新台风元信息（名称、类型、机构等）
4. **Web 展示** (`web/app.py`): FastAPI 读 `storms_active.json` 渲染活跃列表；读 `tracks/*.json` 渲染已关注台风路径表

### 进程模型

- `--web`: 常驻 FastAPI 服务，响应用户选择关注 / 取消关注、查看路径
- `--schedule`: 常驻循环进程，每 `SCHEDULE_INTERVAL_SECONDS` 秒做一轮完整同步
- 两个进程通过 `data/` 目录下的 JSON 文件通信（单写者，无锁但语义安全）
- `watchlist.json` 由 web 写、schedule 读；`storms_active.json` 与 `tracks/*.json` 由 schedule 写、web 读

### 核心模块

| 模块 | 职责 |
|------|------|
| `config.py` | 路径、zoom.earth URL、HTTP 头（必须含 UA + Referer，否则 403）、调度间隔、Web 端口 |
| `utils.py` | logger、UTC↔BJT 转换、6h 截断、zoom.earth 日期格式化 |
| `models.py` | TypedDict：StormSummary / StormTrackPoint / StormDetail / TrackHistoryEntry |
| `data_acquisition.py` | zoom.earth API 客户端：列表 + 详情，失败只 log 不抛 |
| `storage.py` | JSON 原子写入：watchlist / 活跃列表缓存 / 路径历史（date 去重） |
| `scheduler.py` | 定时同步循环：活跃列表 → 关注详情 → 追加路径 |
| `web/app.py` | FastAPI + REST API + 静态前端 |
| `web/static/` | 原生 HTML/CSS/JS 前端（暗色主题，无构建步骤） |
| `main.py` | argparse CLI 入口：--web / --schedule / --acquire / --list |

### 关键设计

- **数据源**: zoom.earth `/data/storms/` 内部 API，通过逆向 [sunshineplan/weather](https://github.com/sunshineplan/weather) Go 库确认。数据本身来自 NHC / JTWC / NRL / IBTrACS
- **必需 HTTP 头**: `User-Agent: Mozilla/5.0` + `Referer: https://zoom.earth/`，否则 403
- **时间规范**: 列表 API 要求 UTC 截断到 6h（00/06/12/18 UTC），格式 `YYYY-MM-DDTHH:MMZ`
- **路径去重**: `append_storm_track` 以路径点 `date` 为主键，zoom.earth 历史段不变，重复抓取只追加新点
- **关注即抓**: `POST /api/watchlist/{id}` 同步调用详情接口并落盘，用户立即看到路径，不等下一周期
- **schedule 容错**: 单次抓取失败仅 log error，不退出循环
- **原子写入**: 所有 JSON 写入先写 `.tmp` 再 `os.replace`，避免读写竞争产生半截文件
- **路径约定**: PROJECT_ROOT = `src/`，数据路径用 `PROJECT_ROOT.parent / "data"`

### 路径约定

- 源码在 `src/storm_toolkit/`
- `data/watchlist.json`: `{"storm_ids": [...], "updated_at": "..."}`
- `data/storms_active.json`: `{"fetched_at": "...", "storms": [...]}`
- `data/tracks/{id}.json`: `{"id":..., "info":..., "last_updated":..., "track_history":[...]}`
- 无需 `.env` 即可运行（所有配置有默认值），但可通过 `.env` 覆盖

## Code Style

- 使用中文注释
- 日志使用 `logging` 模块，logger 命名按模块区分（`storm_toolkit.xxx`）
- 类型注解使用 Python 3.12+ 语法（`str | None` 而非 `Optional[str]`）
- TypedDict 显式声明字段，不使用 dict 字面量传参
