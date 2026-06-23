"""zoom.earth 数据抓取兼容层。

历史接口（fetch_active_storms / fetch_storm_detail）保留以兼容
main.py / scheduler.py / web/app.py 的现有 import，内部委托给
providers.ZoomEarthProvider。

新代码请直接使用 providers.get_provider("zoom-earth")。
"""

from datetime import datetime

from .models import StormDetail, StormSummary
from .providers.zoom_earth import ZoomEarthProvider

_provider = ZoomEarthProvider()


def fetch_active_storms(at: datetime | None = None) -> list[StormSummary]:
    """获取活跃台风列表（委托 ZoomEarthProvider）。"""
    return _provider.fetch_active(at=at) if at is not None else _provider.fetch_active()


def fetch_storm_detail(storm_id: str) -> StormDetail | None:
    """获取台风详情（委托 ZoomEarthProvider）。"""
    return _provider.fetch_detail(storm_id)


__all__ = ["fetch_active_storms", "fetch_storm_detail"]
