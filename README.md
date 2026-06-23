# Storm Toolkit

基于 [zoom.earth](https://zoom.earth/) 数据的台风追踪工具。定时抓取全球活跃台风列表，通过 Web UI 选择关注的台风，对已关注台风每小时记录完整路径（位置、风速、气压、等级）并追加保存为 JSON。

## 数据集

| 属性 | 值 |
|------|------|
| 数据源 | zoom.earth（聚合自 NHC / JTWC / NRL / IBTrACS） |
| 接口 | `GET /data/storms/?date=...` 列表、`GET /data/storms/?id=...` 详情 |
| 更新频率 | zoom.earth 每 6 小时刷新（00/06/12/18 UTC） |
| 默认抓取间隔 | 30 分钟（可通过 `SCHEDULE_INTERVAL_SECONDS` 配置） |
| 路径点字段 | 时间、经纬度、风速（kt）、气压（hPa）、海盆、等级代码、描述、是否预报 |
| 许可 | zoom.earth 数据仅供个人/研究使用，请遵守其 [Terms of Service](https://zoom.earth/terms/) |

## 工作流程

```
zoom.earth /data/storms/?date=... → 活跃台风 ID 列表 → storms_active.json
                                            ↓
                                  Web UI 选择关注
                                            ↓
zoom.earth /data/storms/?id=... → 完整路径详情 → tracks/{id}.json（去重追加）
```

1. **活跃列表抓取**：调用列表接口，写入 `data/storms_active.json`
2. **Web 交互**：用户在前端勾选关注/取消关注，写入 `data/watchlist.json`
3. **关注路径抓取**：对 watchlist 中每个台风调用详情接口，以 `date` 为主键去重追加到 `data/tracks/{id}.json`
4. **Web 展示**：前端读取 JSON 渲染活跃列表卡片 + 关注台风路径表格

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- 无需 API 密钥

## 安装

```bash
git clone <repo-url>
cd storm-toolkit
uv sync
```

## 配置

复制 `.env.example` 为 `.env` 按需修改（所有配置均有默认值，可不配置直接运行）：

```bash
cp .env.example .env
```

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `SCHEDULE_INTERVAL_SECONDS` | 定时同步间隔（秒） | `1800` |
| `ACTIVE_LIST_REFRESH_SECONDS` | 活跃列表刷新间隔（秒） | `1800` |
| `WEB_HOST` | Web 服务监听地址 | `0.0.0.0` |
| `WEB_PORT` | Web 服务端口 | `19995` |
| `HTTP_TIMEOUT` | HTTP 请求超时（秒） | `15` |

## 使用

```bash
# 启动 Web 服务（默认模式）
python -m src.storm_toolkit.main

# 启动定时同步循环（独立进程，建议与 Web 同时运行）
python -m src.storm_toolkit.main --schedule

# 一次性抓取活跃列表 + 所有关注台风详情
python -m src.storm_toolkit.main --acquire

# 打印当前活跃台风
python -m src.storm_toolkit.main --list

# 指定 Web 端口
python -m src.storm_toolkit.main --port 9000
```

打开浏览器访问 http://localhost:19995：

- 顶部显示当前活跃台风卡片，每张卡有「关注」/「取消关注」按钮
- 关注后，对应台风会立即抓取详情并出现在下方「已关注台风路径」区
- 关注区的表格展示该台风的全部历史路径点（时间倒序，最新点高亮）

## Docker 部署

```bash
docker compose build
docker compose up -d
docker compose logs -f web
```

`docker-compose.yml` 同时启动两个服务：

- `storm-scheduler`：后台运行 `--schedule`
- `storm-web`：前台运行 `--web`，映射 `${WEB_PORT:-19995}:19995`

两者通过共享的 `storm-data` 卷交换 JSON 数据。

## 输出结构

```
data/
  watchlist.json              # 用户关注的台风 ID 集合
  storms_active.json          # 最近一次抓取的活跃列表
  tracks/                     # 每个关注台风的完整路径历史
    mekkhala-2026.json
    94w-2026.json
```

### tracks/{id}.json 示例

```json
{
  "id": "mekkhala-2026",
  "info": {
    "name": "Mekkhala (Francisco)",
    "title": "Typhoon Mekkhala (Francisco)",
    "type": "Typhoon",
    "active": true,
    "season": "2026",
    "agencies": "JTWC"
  },
  "last_updated": "2026-06-22T08:30:00Z",
  "track_history": [
    {
      "date": "2026-06-18T18:00:00Z",
      "lng": 144.6, "lat": 12.4,
      "wind": 25, "pressure": 1004,
      "basin": "WP", "code": "D",
      "description": "Tropical Depression",
      "forecast": false,
      "first_seen": "2026-06-22T08:30:00Z"
    }
  ]
}
```

## 项目结构

```
src/storm_toolkit/
  config.py              # 全局配置：路径、API URL、HTTP 头、调度间隔
  utils.py               # logger、UTC↔BJT、6h 截断、日期格式化
  models.py              # TypedDict 数据模型
  data_acquisition.py    # zoom.earth API 客户端（列表 + 详情）
  storage.py             # JSON 原子读写：watchlist / 活跃列表 / 路径历史
  scheduler.py           # 定时同步循环
  web/
    app.py               # FastAPI + REST API
    static/
      index.html         # 前端入口
      style.css          # 暗色主题样式
      app.js             # 轮询、关注切换、路径表渲染
  main.py                # argparse CLI 入口
```

## License

MIT
