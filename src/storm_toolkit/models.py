"""数据模型（TypedDict）定义。"""

from typing import TypedDict


class StormSummary(TypedDict):
    """活跃台风列表条目。"""

    id: str           # 如 "mekkhala-2026"
    kind: str         # "storm" | "disturbance"
    watched: bool     # 是否已在用户关注列表中


class StormTrackPoint(TypedDict):
    """台风路径上的一个观测/预报点。"""

    date: str         # ISO8601 UTC，如 "2026-06-19T00:00:00Z"
    lng: float
    lat: float
    wind: int         # 风速（kt）
    pressure: int     # 气压（hPa）
    basin: str        # "WP" / "EP" / "AT" 等
    code: str         # "D"/"S"/"1"/"2"/"3"...
    description: str  # "Typhoon" / "Tropical Storm"...
    forecast: bool    # 是否为预报未来点


class StormDetail(TypedDict):
    """单个台风完整详情。"""

    id: str
    name: str
    title: str
    type: str
    active: bool
    season: str
    agencies: str
    fetched_at: str   # 抓取时间（ISO8601 UTC）
    track: list[StormTrackPoint]


class TrackHistoryEntry(TypedDict):
    """持久化的路径历史中的一个点（含首次抓取时间）。"""

    date: str
    lng: float
    lat: float
    wind: int
    pressure: int
    basin: str
    code: str
    description: str
    forecast: bool
    first_seen: str   # 首次被本工具抓到的时间（ISO8601 UTC）
