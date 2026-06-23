"""数据源 provider 注册表。

新增数据源只需：
1. 实现 StormProvider 抽象基类
2. 在此处注册到 PROVIDERS
"""

from .base import StormProvider
from .zoom_earth import ZoomEarthProvider
from .zj_cma import ZJCmaProvider

PROVIDERS: dict[str, type[StormProvider]] = {
    "zoom-earth": ZoomEarthProvider,
    "cma": ZJCmaProvider,
}


def get_provider(name: str) -> StormProvider:
    """按名称获取 provider 实例。"""
    cls = PROVIDERS.get(name)
    if cls is None:
        raise KeyError(f"未知 provider: {name}（可用：{list(PROVIDERS)}）")
    return cls()


__all__ = ["StormProvider", "PROVIDERS", "get_provider", "ZoomEarthProvider", "ZJCmaProvider"]
