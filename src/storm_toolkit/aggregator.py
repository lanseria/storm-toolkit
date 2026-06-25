"""多源详情合并层。

对 watchlist 中的 storm_id（可能是 zoom id 或 cma-{tfid}）：
- zoom id：先抓 zoom 详情；按英文名匹配 cma tfid（优先复用已持久化的 cma_tfid）；
  抓 cma 详情；合并两源的 forecasts
- cma-{tfid}：仅抓 cma

单源失败不阻塞另一源。
"""

from . import config
from .models import StormDetail
from .providers.zoom_earth import ZoomEarthProvider
from .providers.zj_cma import ZJCmaProvider
from .storage import get_storm_cma_tfid
from .utils import setup_logger

logger = setup_logger("storm_toolkit.aggregator")


def fetch_combined_detail(storm_id: str) -> tuple[StormDetail | None, str | None]:
    """抓取并合并多源详情。

    Returns:
        (合并后的 StormDetail | None, cma_tfid | None)
        - detail 为 None 表示两源都失败
        - cma_tfid 为 None 表示未匹配到 CMA
    """
    if storm_id.startswith("cma-"):
        return _fetch_cma_only(storm_id)

    return _fetch_zoom_with_cma(storm_id)


def _clean_zoom_name(name: str) -> str:
    """剥离 zoom.earth name 中的括号注释，如 'Mekkhala (Francisco)' → 'Mekkhala'。"""
    if not name:
        return ""
    return name.split("(", 1)[0].strip()


def _fetch_zoom_with_cma(storm_id: str) -> tuple[StormDetail | None, str | None]:
    """对 zoom id 抓 zoom 详情，并尝试匹配 CMA 后合并预测。"""
    zoom = ZoomEarthProvider().fetch_detail(storm_id)
    if zoom is None:
        logger.warning(f"[aggregator] zoom 详情抓取失败: {storm_id}")
        # 也尝试匹配 CMA 单独抓
        return _fetch_cma_only_by_zoom_name(storm_id, zoom_name_hint=storm_id.split("-", 1)[0])

    # 1. 优先复用已持久化的 cma_tfid
    cma_tfid = get_storm_cma_tfid(storm_id)

    # 2. 没有则按英文名即时匹配（剥离括号注释后再比对）
    if cma_tfid is None and config.CMA_ENABLED and zoom["name"]:
        cma = ZJCmaProvider()
        clean_name = _clean_zoom_name(zoom["name"])
        cma_tfid = cma.find_tfid_by_name(clean_name)

    # 3. 抓 CMA 详情并合并 forecasts
    if cma_tfid and config.CMA_ENABLED:
        cma_detail = ZJCmaProvider().fetch_detail(cma_tfid)
        if cma_detail is not None:
            zoom["forecasts"].extend(cma_detail["forecasts"])
            # 用 CMA 的中文名补全 zoom detail（zoom.earth 无中文名）
            if cma_detail.get("name_cn"):
                zoom["name_cn"] = cma_detail["name_cn"]
            logger.info(
                f"[aggregator] 合并成功: zoom + cma({cma_tfid}) "
                f"共 {len(zoom['forecasts'])} 批预测"
            )
        else:
            logger.warning(f"[aggregator] CMA 详情抓取失败: tfid={cma_tfid}")

    return zoom, cma_tfid


def _fetch_cma_only_by_zoom_name(
    storm_id: str, zoom_name_hint: str
) -> tuple[StormDetail | None, str | None]:
    """zoom 失败时，用英文名 hint 在 CMA 找匹配后单独抓。"""
    if not config.CMA_ENABLED or not zoom_name_hint:
        return None, None
    cma = ZJCmaProvider()
    tfid = cma.find_tfid_by_name(zoom_name_hint)
    if tfid is None:
        return None, None
    return cma.fetch_detail(tfid), tfid


def _fetch_cma_only(storm_id: str) -> tuple[StormDetail | None, str | None]:
    """对 cma-{tfid} id 仅抓 CMA 详情。"""
    tfid = storm_id.removeprefix("cma-")
    detail = ZJCmaProvider().fetch_detail(tfid)
    return detail, tfid
