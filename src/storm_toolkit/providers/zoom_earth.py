"""zoom.earth 数据源 provider。

接口通过逆向 github.com/sunshineplan/weather 库并实测确认：
- 列表：GET /data/storms/?date=YYYY-MM-DDTHH:MMZ（时间需 UTC 截断到 6h）
  返回 {"storms": [...], "disturbances": [...], "error": ""}
- 详情：GET /data/storms/?id={storm_id}
  返回 {id, name, title, type, active, season, agencies, track: [...]}
"""

from datetime import datetime, timezone

import requests

from .. import config
from ..models import (
    ForecastBatch,
    ForecastPoint,
    StormDetail,
    StormSummary,
    TrackPoint,
)
from ..utils import (
    format_zoom_date,
    now_utc_iso,
    setup_logger,
    truncate_to_six_hours,
    utc_to_beijing,
)
from .base import StormProvider

logger = setup_logger("storm_toolkit.providers.zoom_earth")


class ZoomEarthProvider(StormProvider):
    """zoom.earth 数据源。"""

    name = "zoom-earth"

    def fetch_active(self, at: datetime | None = None) -> list[StormSummary]:
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

        # 延迟 import 避免循环依赖
        from ..storage import load_watchlist
        watched = load_watchlist()

        summaries: list[StormSummary] = []
        for sid in payload.get("storms", []):
            summaries.append({
                "id": sid,
                "kind": "storm",
                "watched": sid in watched,
                "sources": ["zoom-earth"],
                "cma_tfid": None,
            })
        for sid in payload.get("disturbances", []):
            summaries.append({
                "id": sid,
                "kind": "disturbance",
                "watched": sid in watched,
                "sources": ["zoom-earth"],
                "cma_tfid": None,
            })

        logger.info(
            f"[zoom-earth] 活跃台风 {len(payload.get('storms', []))} 个，"
            f"扰动 {len(payload.get('disturbances', []))} 个"
            f"（基准时刻 {utc_to_beijing(truncated).strftime('%Y-%m-%d %H:%M')} BJT）"
        )
        return summaries

    def fetch_detail(self, storm_id: str) -> StormDetail | None:
        """获取单个台风的完整详情（实况点 + zoom-earth 自己的预测批）。"""
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

        fetched_at = now_utc_iso()
        track: list[TrackPoint] = []
        forecast_points: list[ForecastPoint] = []

        for raw in payload.get("track", []):
            coords = raw.get("coordinates") or [None, None]
            if len(coords) != 2 or coords[0] is None or coords[1] is None:
                continue
            point = {
                "date": raw.get("date", ""),
                "lng": float(coords[0]),
                "lat": float(coords[1]),
                "wind": int(raw.get("wind", 0) or 0),
                "pressure": int(raw.get("pressure", 0) or 0),
                "code": str(raw.get("code", "")),
                "description": str(raw.get("description", "")),
            }
            if bool(raw.get("forecast", False)):
                forecast_points.append(point)  # type: ignore[arg-type]
            else:
                track.append({**point, "source": "jtwc"})  # type: ignore[typeddict-item]

        # zoom.earth 没有显式的"发布时间"，用抓取时刻当作本批 issued_at
        # source 标记为 jtwc：zoom.earth 的预测数据本身来自 JTWC
        forecasts: list[ForecastBatch] = []
        if forecast_points:
            forecasts.append({
                "source": "jtwc",
                "issued_at": fetched_at,
                "points": forecast_points,
            })

        detail: StormDetail = {
            "id": payload.get("id", storm_id),
            "name": payload.get("name", ""),
            "name_cn": "",
            "title": payload.get("title", ""),
            "type": payload.get("type", ""),
            "active": bool(payload.get("active", False)),
            "season": payload.get("season", ""),
            "agencies": payload.get("agencies", ""),
            "fetched_at": fetched_at,
            "track": track,
            "forecasts": forecasts,
        }
        logger.info(
            f"[zoom-earth] 详情成功: {detail['name'] or detail['id']}，"
            f"实况 {len(track)} 点 + 预测 {len(forecast_points)} 点"
        )
        return detail

    def fetch_detail_by_name(self, enname: str) -> StormDetail | None:
        """zoom.earth 的 id 本身就含英文名，直接转小写拼年份尝试。

        由于 zoom.earth 没有按名搜索的接口，这里直接返回 None 让 aggregator
        走 cma 通道。zoom 的 detail 通常已通过 storm_id 拿到。
        """
        return None
