"""zoom.earth 台风数据抓取模块。

接口通过逆向 github.com/sunshineplan/weather 库并实测确认：
- 列表：GET /data/storms/?date=YYYY-MM-DDTHH:MMZ（时间需 UTC 截断到 6h）
  返回 {"storms": [...], "disturbances": [...], "error": ""}
- 详情：GET /data/storms/?id={storm_id}
  返回 {id, name, title, type, active, season, agencies, track: [...]}
"""

from datetime import datetime, timezone

import requests

from . import config
from .models import StormDetail, StormSummary, StormTrackPoint
from .utils import (
    format_zoom_date,
    setup_logger,
    truncate_to_six_hours,
    utc_to_beijing,
    now_utc_iso,
)

logger = setup_logger("storm_toolkit.data_acquisition")


def fetch_active_storms(at: datetime | None = None) -> list[StormSummary]:
    """获取某时刻（默认当前）的活跃台风与扰动列表。

    Args:
        at: 查询时刻（UTC 或带时区均可）。None 表示当前时刻。

    Returns:
        StormSummary 列表（storms 在前，disturbances 在后）。抓取失败返回空列表。
    """
    if at is None:
        at = datetime.now(timezone.utc)
    truncated = truncate_to_six_hours(at)
    params = {"date": format_zoom_date(truncated)}

    try:
        resp = requests.get(
            config.STORMS_API,
            params=params,
            headers=config.HTTP_HEADERS,
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.error(f"获取活跃台风列表失败（date={params['date']}）: {e}")
        return []
    except ValueError as e:
        logger.error(f"解析活跃台风列表 JSON 失败: {e}")
        return []

    if payload.get("error"):
        logger.warning(f"zoom.earth 返回错误: {payload['error']}")

    from .storage import load_watchlist
    watched = load_watchlist()

    summaries: list[StormSummary] = []
    for sid in payload.get("storms", []):
        summaries.append({"id": sid, "kind": "storm", "watched": sid in watched})
    for sid in payload.get("disturbances", []):
        summaries.append({"id": sid, "kind": "disturbance", "watched": sid in watched})

    logger.info(
        f"活跃台风 {len(payload.get('storms', []))} 个，扰动 {len(payload.get('disturbances', []))} 个"
        f"（基准时刻 {utc_to_beijing(truncated).strftime('%Y-%m-%d %H:%M')} BJT）"
    )
    return summaries


def fetch_storm_detail(storm_id: str) -> StormDetail | None:
    """获取单个台风的完整详情（含路径）。

    Args:
        storm_id: 台风 ID，如 "mekkhala-2026"。

    Returns:
        StormDetail 或 None（抓取失败）。
    """
    try:
        resp = requests.get(
            config.STORMS_API,
            params={"id": storm_id},
            headers=config.HTTP_HEADERS,
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.error(f"获取台风详情失败（id={storm_id}）: {e}")
        return None
    except ValueError as e:
        logger.error(f"解析台风详情 JSON 失败（id={storm_id}）: {e}")
        return None

    track: list[StormTrackPoint] = []
    for raw in payload.get("track", []):
        coords = raw.get("coordinates") or [None, None]
        if len(coords) != 2 or coords[0] is None or coords[1] is None:
            continue
        track.append({
            "date": raw.get("date", ""),
            "lng": float(coords[0]),
            "lat": float(coords[1]),
            "wind": int(raw.get("wind", 0) or 0),
            "pressure": int(raw.get("pressure", 0) or 0),
            "basin": str(raw.get("basin", "")),
            "code": str(raw.get("code", "")),
            "description": str(raw.get("description", "")),
            "forecast": bool(raw.get("forecast", False)),
        })

    detail: StormDetail = {
        "id": payload.get("id", storm_id),
        "name": payload.get("name", ""),
        "title": payload.get("title", ""),
        "type": payload.get("type", ""),
        "active": bool(payload.get("active", False)),
        "season": payload.get("season", ""),
        "agencies": payload.get("agencies", ""),
        "fetched_at": now_utc_iso(),
        "track": track,
    }
    logger.info(
        f"获取台风详情成功: {detail['name'] or detail['id']}，"
        f"路径点 {len(track)} 个（其中预报 {sum(1 for p in track if p['forecast'])} 个）"
    )
    return detail
