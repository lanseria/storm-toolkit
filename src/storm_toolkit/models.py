"""数据模型（TypedDict）定义。

设计要点：
- 实况（track）与预测（forecasts）物理分离，避免同表混存
- 预测按 ForecastBatch 组织，每批含 source（数据源标签）+ issued_at（发布时间）+ points
- 多家机构对同一时刻的预测各自独立成批共存
"""

from typing import TypedDict


class StormSummary(TypedDict):
    """活跃台风列表条目（跨源合并后）。"""

    id: str                   # zoom.earth id 为主键（如 "mekkhala-2026"），CMA 独有项用 "cma-{tfid}"
    kind: str                 # "storm" | "disturbance"
    watched: bool             # 是否已在用户关注列表
    sources: list[str]        # 数据源标签，如 ["zoom-earth"] 或 ["zoom-earth", "cma"]
    cma_tfid: str | None      # 匹配到的 CMA 台风编号（如 "202607"），未匹配为 None


class TrackPoint(TypedDict):
    """台风路径上的一个实况观测点。"""

    date: str                 # ISO8601 UTC，如 "2026-06-19T00:00:00Z"
    lng: float
    lat: float
    wind: int                 # 风速（kt）
    pressure: int             # 气压（hPa）
    code: str                 # "D"/"S"/"1"/"2"/"3"/"4"/"5"
    description: str          # 原文描述（zoom 英文 / cma 中文）
    source: str               # 实况点来源（zoom.earth 数据本身来自 JTWC，当前仅 "jtwc"）


class TrackHistoryEntry(TrackPoint):
    """持久化的实况条目（含首次抓取时间）。"""

    first_seen: str           # 首次被本工具抓到的时间（ISO8601 UTC）


class ForecastPoint(TypedDict):
    """预测路径上的一个未来点。"""

    date: str                 # 预测目标时刻（ISO8601 UTC）
    lng: float
    lat: float
    wind: int                 # 风速（kt，CMA m/s 已换算）
    pressure: int             # 气压（hPa）
    code: str                 # "D"/"S"/"1".."5"
    description: str          # 原文描述


class ForecastBatch(TypedDict):
    """一次发布的预测批次（同 source 同 issued_at 视为一批）。"""

    source: str               # "zoom-earth" | "cma" | "jma" | "jtwc"
    issued_at: str            # 这批预测的发布时间（ISO8601 UTC，CMA 取 ybsj）
    points: list[ForecastPoint]


class StormDetail(TypedDict):
    """单个台风完整详情（实况 + 多源预测）。"""

    id: str
    name: str
    name_cn: str              # 中文名（来自 CMA payload.name），无则空串
    title: str
    type: str
    active: bool
    season: str
    agencies: str
    fetched_at: str           # 抓取时间（ISO8601 UTC）
    track: list[TrackPoint]            # 实况点（仅 zoom.earth）
    forecasts: list[ForecastBatch]     # 预测批次（多源混合）
