"""数据源抽象基类。

每个 provider 用自己的 ID 体系返回数据：
- zoom-earth: id = "mekkhala-2026"（小写英文名-年份）
- cma: id = "202607" 或 "cma-202607"（tfid）

跨源合并由 matcher / aggregator 负责。
"""

from abc import ABC, abstractmethod

from ..models import StormDetail, StormSummary


class StormProvider(ABC):
    """台风数据源抽象。"""

    name: str  # "zoom-earth" | "cma"

    @abstractmethod
    def fetch_active(self) -> list[StormSummary]:
        """获取本源当前活跃台风列表。

        Returns:
            StormSummary 列表，失败时返回空列表。
        """

    @abstractmethod
    def fetch_detail(self, storm_id: str) -> StormDetail | None:
        """按本源 ID 抓取单个台风完整详情（含实况与预测）。

        Args:
            storm_id: 本源 ID 体系下的台风标识。

        Returns:
            StormDetail 或 None（抓取失败）。
        """

    @abstractmethod
    def fetch_detail_by_name(self, enname: str) -> StormDetail | None:
        """按英文名跨源匹配抓取详情。

        用于跨源 ID 映射：zoom.earth 的 'mekkhala-2026' 对应 CMA 的 '202607'，
        通过英文名大小写不敏感匹配。

        Returns:
            匹配到的台风详情；零匹配返回 None。
        """
