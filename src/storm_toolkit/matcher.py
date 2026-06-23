"""跨源 ID 映射与活跃列表合并。

zoom.earth id 形如 'mekkhala-2026'（小写英文名-年份），CMA tfid 形如 '202607'。
通过英文名大小写不敏感匹配建立关联，文件主键仍用 zoom id，cma_tfid 作为附属字段。
"""

from .models import StormSummary
from .utils import setup_logger

logger = setup_logger("storm_toolkit.matcher")


def _zoom_id_to_enname(zoom_id: str) -> str:
    """从 zoom id 提取英文名部分：'mekkhala-2026' → 'mekkhala'。"""
    return zoom_id.split("-", 1)[0].strip().lower()


def build_enname_map(cma_raw_list: list[dict]) -> dict[str, str]:
    """从 CMA 原始活跃列表构建 enname_lower → tfid 映射。

    跳过 enname 为空 / NONAME 的项。
    """
    out: dict[str, str] = {}
    for item in cma_raw_list:
        tfid = str(item.get("tfid", "")).strip()
        enname = str(item.get("enname", "")).strip()
        if not tfid or not enname or enname.upper() in ("NONAME", "NONE"):
            continue
        out.setdefault(enname.lower(), tfid)
    return out


def merge_active(
    zoom_list: list[StormSummary],
    cma_raw_list: list[dict],
) -> list[StormSummary]:
    """合并 zoom 与 CMA 活跃列表。

    - zoom 项优先；按英文名匹配到 CMA 时，补 cma_tfid 与 sources=["zoom-earth", "cma"]
    - CMA 中未被 zoom 匹配的项，作为独立条目（id=cma-{tfid}）追加

    Args:
        zoom_list: ZoomEarthProvider.fetch_active() 返回
        cma_raw_list: ZJCmaProvider.fetch_active_raw() 返回（含 enname）

    Returns:
        合并后的 StormSummary 列表。
    """
    name_map = build_enname_map(cma_raw_list)
    matched_tfid: set[str] = set()

    # zoom 项：尝试匹配 CMA tfid
    merged: list[StormSummary] = list(zoom_list)
    for s in merged:
        enname = _zoom_id_to_enname(s["id"])
        tfid = name_map.get(enname)
        if tfid:
            s["cma_tfid"] = tfid
            s["sources"] = ["zoom-earth", "cma"]
            matched_tfid.add(tfid)

    # 未匹配的 CMA 项作为独立条目
    from .storage import load_watchlist
    watched = load_watchlist()
    for item in cma_raw_list:
        tfid = str(item.get("tfid", "")).strip()
        if not tfid or tfid in matched_tfid:
            continue
        sid = f"cma-{tfid}"
        merged.append({
            "id": sid,
            "kind": "storm",
            "watched": sid in watched,
            "sources": ["cma"],
            "cma_tfid": tfid,
        })

    if matched_tfid:
        logger.info(f"跨源匹配命中: {len(matched_tfid)} 个（tfid: {sorted(matched_tfid)}）")
    return merged


def find_cma_tfid_by_name(enname: str, cma_raw_list: list[dict]) -> str | None:
    """在 CMA 原始活跃列表中按英文名匹配 tfid。

    用于详情抓取阶段，对 zoom 拿到的 name 做即时匹配。
    """
    if not enname or enname.strip().upper() in ("", "NONAME", "NONE"):
        return None
    target = enname.strip().lower()
    for item in cma_raw_list:
        cand = str(item.get("enname", "")).strip().lower()
        if cand == target:
            return str(item.get("tfid"))
    return None
