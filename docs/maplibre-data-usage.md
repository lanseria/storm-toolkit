# Storm Toolkit 数据 → MapLibre 地图渲染指南

本文档说明 Storm Toolkit 输出的 4 类 JSON 数据的字段契约与 MapLibre 渲染思路。**只讲用法，不含完整代码**。

---

## 数据来源（Web API）

| 端点 | 用途 | 更新频率 |
|------|------|---------|
| `GET /api/storms/active` | 当前活跃台风列表（卡片 / 列表） | scheduler 每小时 |
| `GET /api/watchlist` | 已关注台风的完整实况 + 多源预测 | scheduler 每小时 |
| `GET /api/tracks/{id}` | 单个台风的实况 + 多源预测 | scheduler 每小时 |
| `GET /api/history` | 已消亡台风列表（仅实况） | 归档时 |
| `GET /api/history/{id}` | 单个已消亡台风的完整实况 | 归档时 |

所有时间字段均为 ISO 8601 UTC（如 `2026-06-23T00:00:00Z`），在 MapLibre popup 里建议转 BJT 展示。

---

## 字段语义（跨文件通用）

| 字段 | 类型 | 单位/格式 | 说明 |
|------|------|----------|------|
| `lng` | number | 度 | 经度，MapLibre 直接用作 `[lng, lat]` 第一个值 |
| `lat` | number | 度 | 纬度，MapLibre 直接用作 `[lng, lat]` 第二个值 |
| `date` | string | ISO 8601 UTC | 该点的时刻（实况为观测时刻，预测为目标时刻） |
| `wind` | int | kt（节） | 最大持续风速。展示时可 ×1.852 转 m/s 或 ×1.151 转 mph |
| `pressure` | int | hPa | 中心最低气压 |
| `code` | string | 枚举 | 强度等级代码（见下表） |
| `description` | string | 原文 | 强度文本，zoom-earth 为英文，CMA 为中文 |
| `source` | string | 枚举 | 数据源标签（见下表） |
| `first_seen` | string | ISO 8601 UTC | 该点首次被本工具抓到的时间（仅实况） |
| `issued_at` | string | ISO 8601 UTC | 该批预测的发布时间（仅预测） |

### `code` 强度等级

| code | 含义 | 推荐颜色 | 推荐点大小（直径 px） |
|------|------|---------|---------------------|
| `D` | Tropical Depression（热带低压） | `#94a3b8` 灰 | 5 |
| `S` | Tropical Storm（热带风暴） | `#22d3ee` 青 | 6 |
| `1` | Cat 1 / 强热带风暴 | `#84cc16` 黄绿 | 7 |
| `2` | Cat 2 / 台风 | `#eab308` 黄 | 8 |
| `3` | Cat 3 / 强台风 | `#f97316` 橙 | 9 |
| `4` | Cat 4 / 超强台风 | `#ef4444` 红 | 10 |
| `5` | Cat 5（最高等级） | `#a855f7` 紫 | 12 |
| `ST` | Very Strong Typhoon（zoom-earth 特殊代码） | `#dc2626` 深红 | 11 |

> 其他未匹配的 code 用灰色兜底。

### `source` 数据源标签

| source | 含义 | 推荐线色 |
|--------|------|---------|
| `zoom-earth` | zoom.earth（聚合 NHC/JTWC/NRL/IBTrACS） | `#00b4d8` 蓝 |
| `cma` | 中国气象局 | `#f87171` 红 |
| `jma` | 日本气象厅 | `#f4845f` 橙 |
| `jtwc` | 美国联合台风警报中心 | `#a3e635` 绿 |
| `cwa` | 台湾中央气象署 | `#60a5fa` 浅蓝 |
| `hko` | 香港天文台 | `#fbbf24` 金 |
| `kma` | 韩国气象厅 | `#a78bfa` 紫 |
| `other*` | 未识别机构 | `#94a3b8` 灰 |

---

## 4 类 JSON 结构

### 1. `storms_active.json` / `GET /api/storms/active`

**用途**：渲染台风卡片、活跃标记。**不含坐标**，需要拿 `id` 调 `/api/tracks/{id}` 才能画图。

```jsonc
{
  "fetched_at": "2026-06-23T02:57:14Z",
  "storms": [
    {
      "id": "mekkhala-2026",          // 主键，zoom.earth id
      "kind": "storm",                // "storm" | "disturbance"
      "watched": true,                // 是否在用户关注列表
      "sources": ["zoom-earth", "cma"],
      "cma_tfid": "202607"            // 可能为 null
    }
  ]
}
```

**MapLibre 用法**：本身不含坐标，不直接画。先用它列出活跃台风，点击某项再调 `/api/tracks/{id}` 拿路径。

---

### 2. `tracks/{id}.json` / `GET /api/tracks/{id}` / `GET /api/watchlist`

**用途**：核心渲染数据。一个台风的实况路径 + 多源预测。

```jsonc
{
  "id": "mekkhala-2026",
  "info": {
    "name": "Mekkhala (Francisco)",
    "title": "Typhoon Mekkhala (Francisco)",
    "type": "Typhoon",
    "active": true,
    "season": "2026",
    "agencies": "JTWC",
    "cma_tfid": "202607"
  },
  "last_updated": "2026-06-23T02:57:14Z",
  "track_history": [                  // 实况点（仅 zoom-earth，3h 间隔）
    {
      "date": "2026-06-23T00:00:00Z",
      "lng": 125.2, "lat": 18.9,
      "wind": 115, "pressure": 943,
      "code": "ST", "description": "Very Strong Typhoon",
      "source": "zoom-earth",
      "first_seen": "2026-06-23T02:57:14Z"
    }
  ],
  "forecasts": [                      // 预测批，按 source 各自独立
    {
      "source": "zoom-earth",
      "issued_at": "2026-06-23T02:57:14Z",
      "points": [
        { "date": "...", "lng": 124.8, "lat": 19.5, "wind": 110, "pressure": 940,
          "code": "4", "description": "Typhoon" }
      ]
    },
    {
      "source": "cma",
      "issued_at": "2026-06-23T00:00:00Z",
      "points": [ /* ... */ ]
    }
    // 可能还有 jma / jtwc / cwa / hko / kma 批次
  ]
}
```

**关键点**：
- `track_history` 是已发生的实况，**按 `date` 升序**。
- `forecasts[]` 是多源预测的数组，每个 batch 自带 `source` 和 `issued_at`。同一 `source` 可能有多个 `issued_at`（不同时次发布），用 `issued_at` 排序取最新一批即可。
- 实况点带 `source` 字段（目前固定 `zoom-earth`），预测点**不带** `source`（继承所在 batch 的）。
- `forecasts[].points[]` 内的 `date` 是预测的目标时刻，未来时刻。

---

### 3. `watchlist.json` / `GET /api/watchlist`（含 tracks）

```jsonc
{
  "watchlist": ["mekkhala-2026"],
  "tracks": [ /* Track 对象数组，结构同上节 */ ]
}
```

**MapLibre 用法**：一次性拿到所有关注台风的路径，用于在地图上同时绘制多个台风。

---

### 4. `history/{id}.json` / `GET /api/history`

**用途**：已消亡台风的历史实况路径（**无预测**）。

```jsonc
// /api/history 返回列表摘要
{ "history": [
  { "id": "...", "info": {...}, "archived_at": "...", "track_count": 27 }
]}

// /api/history/{id} 返回完整结构
{
  "id": "mekkhala-2026",
  "info": { /* 同 tracks.info */ },
  "archived_at": "2026-06-30T08:00:00Z",
  "track_history": [ /* 同 tracks.track_history，仅实况 */ ]
  // 注意：没有 forecasts 字段
}
```

---

## MapLibre 渲染思路

### 数据预处理：转 GeoJSON

MapLibre 原生消费 GeoJSON（`FeatureCollection`）。建议在前端把上述 JSON 转换为以下分层 GeoJSON：

```
┌─────────────────────────────────────────────────────┐
│ 1. track-history          （实况线，1 条 LineString）│
│ 2. track-history-points   （实况点，N 个 Point）     │
│ 3. forecast-line-zoom     （zoom-earth 预测线）      │
│ 4. forecast-line-cma      （CMA 预测线）             │
│ 5. forecast-line-jma      （JMA 预测线）             │
│ 6. forecast-line-jtwc     （JTWC 预测线）            │
│ 7. ... 其他 source 各一层                            │
│ 8. forecast-points       （所有预测点，1 层）        │
│ 9. active-marker          （当前实况位置大圆）       │
└─────────────────────────────────────────────────────┘
```

**转换示例**（仅说明，不写实现）：

实况线：
```
FeatureCollection → 1 个 Feature
  geometry: LineString, coordinates = track_history.map(p => [p.lng, p.lat])
  properties: { storm_id, storm_name, source: "zoom-earth", kind: "actual" }
```

实况点：
```
FeatureCollection → N 个 Feature
  geometry: Point, coordinates = [p.lng, p.lat]
  properties: { storm_id, date, wind, pressure, code, description, kind: "actual" }
```

预测线（每个 source × 每个台风一条）：
```
对每个 batch（取 issued_at 最新的那批）：
  Feature
    geometry: LineString, coordinates = batch.points.map(p => [p.lng, p.lat])
    properties: { storm_id, source: batch.source, issued_at: batch.issued_at, kind: "forecast" }
```

预测点：同上但 `geometry: Point`。

### 图层（`map.addLayer`）建议

| 图层 ID | type | 数据 | 样式要点 |
|---------|------|------|---------|
| `track-history-line` | `line` | 实况线 | 实色粗线，按台风区分颜色，宽 3px |
| `track-history-points` | `circle` | 实况点 | 按 `code` 用 `match` 表达式着色（见前表），半径 4–6px |
| `forecast-line-{source}` | `line` | 各源预测线 | 按 source 配色（见前表），`dasharray: [2, 2]` 虚线表示"预测"，宽 2px |
| `forecast-points` | `circle` | 预测点 | 同 source 色，半径 3px，半透明 |
| `active-position` | `circle` | 最新实况点 | 大圆 12px，外环 pulse 动画，醒目色 |

**虚线表达式（区分实况 vs 预测）**：
```
layout: { "line-cap": "round" }
paint: {
  "line-color": ["match", ["get", "source"], "cma", "#f87171", "jma", "#f4845f", ...],
  "line-width": 2,
  "line-dasharray": [2, 2]   // 预测用虚线；实况层去掉此行
}
```

**点大小表达式（按 code）**：
```
paint: {
  "circle-radius": ["match", ["get", "code"], "5", 6, "4", 5, "3", 4, 3],
  "circle-color": ["match", ["get", "code"], "D", "#94a3b8", "S", "#22d3ee",
                   "1", "#84cc16", "2", "#eab308", "3", "#f97316",
                   "4", "#ef4444", "5", "#a855f7", "#666666"],
  "circle-stroke-color": "#ffffff",
  "circle-stroke-width": 1
}
```

### Popup（hover/click 显示完整属性）

点图层加 `mouseenter` / `click` 事件，popup 内容：
```
台风名：{info.title}
时刻：{date 转 BJT}
位置：{lng.toFixed(1)}°E, {lat.toFixed(1)}°N
风速：{wind} kt ({wind*1.852} m/s)
气压：{pressure} hPa
等级：{code} - {description}
来源：{source 或 batch.source}
```

### 时间动画（可选）

实况点和预测点都带 `date`，可用 MapLibre 的 `setFilter` 做时间轴动画：
```
按时间轴 t 过滤：
  track-history-points: ["<=", ["get", "date"], t]
  forecast-points: 同上
```

配合 `requestAnimationFrame` 即可做"台风路径回放"效果。

### 多台风场景

`/api/watchlist` 一次返回多个台风。建议：
- 每个台风用不同基础色（可选 8 色 palette）
- 实况线粗、预测线细 + 虚线
- 当前活跃台风（`info.active=true`）保持显示，已消亡的可半透明

### 边界数据

- **风圈半径**：当前数据**不包含** `radius7`/`radius10` 风圈字段，无法画风圈圆。如需要展示风圈，需要修改后端 `providers/zj_cma.py` 保留 CMA 原始字段（plan agent 早期设计曾考虑，第一刀简化掉了）。
- **登陆点**：CMA 接口有 `land[]` 字段但当前未持久化，如需要同样要扩展后端。

### 视图初始范围

实况线通常跨度 10–30°，建议初始：
```
map.fitBounds([
  [minLng - 5, minLat - 5],
  [maxLng + 5, maxLat + 5]
], { padding: 50 })
```
以台风路径为中心自动定位。

---

## 数据生命周期

```
活跃（storms_active.json） ──关注──> tracks/{id}.json（实况+预测持续累积）
                                          │
                                  active=false
                                          ↓
                                history/{id}.json（仅实况，永久归档）
```

- **实时模式**：定时拉 `/api/storms/active`（30–60 秒一次刷新 UI 标记）
- **关注模式**：拉 `/api/watchlist` 渲染实况 + 预测
- **历史回看**：拉 `/api/history` 列表，点开后拉 `/api/history/{id}` 渲染纯实况
- **去重**：实况点主键 `(date, source)`，预测批主键 `(source, issued_at)`，重复数据后端已去重，前端无需处理

---

## 典型渲染流程（伪码）

```
1. 启动 map = new maplibregl.Map({...})
2. fetch('/api/storms/active') → 在侧边栏列出台风
3. 用户点击某台风 → fetch('/api/tracks/{id}')
4. 把 tracks 转成 5+ 个 GeoJSON source：
     - 实况线（1 条）
     - 实况点（N 个）
     - 各 source 预测线（按 source 拆批，取最新 issued_at）
     - 预测点（合并所有 source）
5. 依次 addSource + addLayer，按前表配色
6. map.fitBounds(实况线 bbox) 自动聚焦
7. 给所有点图层挂 popup
8. 可选：时间轴滑块，setFilter 做回放
```

---

## 常见坑

| 坑 | 解释 |
|----|------|
| 把预测点和实况点画在同一层 | 视觉混乱，建议分两层（实况粗、预测细+虚线） |
| 没按 `code` 区分点大小/颜色 | 所有点同色，看不出强度变化 |
| 把多个 `issued_at` 的预测都画出来 | 线条堆叠如麻；按 source 取最新 issued_at 一批即可 |
| 时间不转 BJT | 数据是 UTC，给中文用户展示需 ×+8h |
| 把 `cma_tfid` 当主键 | 主键永远是 zoom id（如 `mekkhala-2026`），`cma_tfid` 只是附属字段 |
| `forecasts[].points[].source` 不存在 | 预测点的 source 在 batch 上，子点没有，继承读 |
| 画风圈圆 | 当前数据没风圈，画不了；如需，先扩展后端 |
