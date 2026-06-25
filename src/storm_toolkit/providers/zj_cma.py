"""浙江水利厅 CMA 数据源 provider。

接口（实测）：
- 活跃列表 GET /Api/TyhoonActivity
  → [{tfid, name(中), enname(英), lat, lng, speed(m/s), pressure, power, strong(中),
      time(ISO+00:00), movedirection, movespeed, radius7, radius10, warnlevel, ...}]
- 详情 GET /Api/TyphoonInfo/{tfid}
  → {tfid, name, enname, isactive, starttime, endtime, warnlevel,
     centerlng, centerlat, land, points: [...]}
  每个 points[i] 是一个实况点，内嵌 forecast[{tm, forecastpoints[]}] 按 tm（机构）分组。
  forecastpoints[0] 与外层对齐（跳过）；[1:] 才是预测点；每点带 ybsj（发布时间 ISO+00:00）。

编码 UTF-8（Windows 控制台显示乱码是终端问题，内存字符串正确）。
"""

from datetime import datetime

import requests

from .. import config
from ..models import (
    ForecastBatch,
    ForecastPoint,
    StormDetail,
    StormSummary,
    TrackPoint,
)
from ..utils import BEIJING_TZ, UTC, now_utc_iso, setup_logger
from .base import StormProvider

logger = setup_logger("storm_toolkit.providers.zj_cma")


# CMA 中文强度 → 统一 code 映射
CMA_STRONG_CODE: dict[str, str] = {
    "热带低压": "D",
    "热带风暴": "S",
    "强热带风暴": "1",
    "台风": "2",
    "强台风": "3",
    "超强台风": "4",
}

# CMA 机构名 tm → source 标签
CMA_TM_SOURCE: dict[str, str] = {
    "中国": "cma",
    "日本": "jma",
    "美国": "jtwc",
    "中国台湾": "cwa",   # 台湾中央气象署
    "中国香港": "hko",   # 香港天文台
    "韩国": "kma",
}

# m/s → kt 换算系数
MS_TO_KT = 1.943844


def _parse_cma_time(s: str) -> str:
    """CMA 时间 '2026-06-20 02:00:00' 按 BJT 解析，转 UTC ISO8601 + Z。

    解析失败返回空串（避免污染存储）。
    """
    if not s:
        return ""
    try:
        # 兼容两种格式：'2026-06-20 02:00:00' / '2026-06-20T02:00:00'
        normalized = s.replace("T", " ")
        dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING_TZ)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as e:
        logger.warning(f"解析 CMA time 失败: {s!r} ({e})")
        return ""


def _parse_iso_z(s: str) -> str:
    """CMA ybsj 已是 ISO8601+00:00，统一转 +Z 后缀。失败回退空串。"""
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s).astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _parse_cma_time(s)  # 退回 BJT 解析


def _ms_to_kt(speed) -> int:
    """m/s → kt（int），容错处理空值/异常。"""
    try:
        return int(round(float(speed) * MS_TO_KT))
    except (TypeError, ValueError):
        return 0


def _to_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _slugify_tm(tm: str) -> str:
    """tm 兜底转 source slug：非标准机构用 other-{lower ascii} 命名。"""
    if tm in CMA_TM_SOURCE:
        return CMA_TM_SOURCE[tm]
    # 非 ASCII 字符（如其他语言名）走 other 通道
    ascii_only = "".join(c for c in tm if c.isascii() and c.isalnum()).lower()
    return f"other-{ascii_only}" if ascii_only else "other"


class ZJCmaProvider(StormProvider):
    """浙江水利厅 CMA 数据源。"""

    name = "cma"

    def fetch_active_raw(self) -> list[dict]:
        """活跃列表原始数据（含 enname 等扩展字段），供 matcher 做跨源匹配。"""
        try:
            resp = requests.get(
                config.ZJ_CMA_ACTIVITY_API,
                headers=config.ZJ_CMA_HEADERS,
                timeout=config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"[cma] 获取活跃列表失败: {e}")
            return []
        except ValueError as e:
            logger.error(f"[cma] 解析活跃列表 JSON 失败: {e}")
            return []

        if not isinstance(data, list):
            logger.warning(f"[cma] 活跃列表非数组: {type(data).__name__}")
            return []

        logger.info(f"[cma] 活跃台风 {len(data)} 个")
        return data

    def fetch_active(self) -> list[StormSummary]:
        """活跃列表归一化为 StormSummary（id 形如 cma-202607）。"""
        from ..storage import load_watchlist
        watched = load_watchlist()

        out: list[StormSummary] = []
        for item in self.fetch_active_raw():
            tfid = str(item.get("tfid", "")).strip()
            if not tfid:
                continue
            sid = f"cma-{tfid}"
            out.append({
                "id": sid,
                "kind": "storm",
                "watched": sid in watched,
                "sources": ["cma"],
                "cma_tfid": tfid,
            })
        return out

    def fetch_detail(self, storm_id: str) -> StormDetail | None:
        """按 tfid（或 cma-{tfid}）抓取详情。

        CMA 外层 points 不入实况表（实况表只用 zoom-earth 避免重复），
        仅把 points[i].forecast[] 按 tm 拆批写入 forecasts。
        """
        tfid = storm_id.removeprefix("cma-")
        try:
            resp = requests.get(
                f"{config.ZJ_CMA_INFO_API}/{tfid}",
                headers=config.ZJ_CMA_HEADERS,
                timeout=config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as e:
            logger.error(f"[cma] 获取详情失败（tfid={tfid}）: {e}")
            return None
        except ValueError as e:
            logger.error(f"[cma] 解析详情 JSON 失败（tfid={tfid}）: {e}")
            return None

        fetched_at = now_utc_iso()
        track: list[TrackPoint] = []  # CMA 不贡献实况点
        batches_by_source: dict[str, ForecastBatch] = {}

        for point in payload.get("points", []):
            for group in point.get("forecast", []) or []:
                tm = str(group.get("tm", "")).strip()
                source = _slugify_tm(tm)
                # 跳过 JTWC（zoom.earth 已贡献 JTWC 预测，避免重复）
                if source == "jtwc":
                    continue
                fps = group.get("forecastpoints", []) or []
                if len(fps) < 2:
                    continue
                # 发布时间取第二条起的首个 ybsj（[0] 是对齐点通常无 ybsj）
                issued_at = ""
                for fp in fps[1:]:
                    issued_at = _parse_iso_z(str(fp.get("ybsj", "")))
                    if issued_at:
                        break
                if not issued_at:
                    issued_at = fetched_at  # 兜底用抓取时刻

                # 同一 source + 同一 issued_at 视为一批；同一批内追加所有未来点
                key = f"{source}@{issued_at}"
                batch = batches_by_source.get(key)
                if batch is None:
                    batch = {"source": source, "issued_at": issued_at, "points": []}
                    batches_by_source[key] = batch  # type: ignore[assignment]

                for fp in fps[1:]:
                    strong = str(fp.get("strong", "")).strip()
                    date = _parse_cma_time(str(fp.get("time", "")))
                    if not date:
                        continue
                    fpt: ForecastPoint = {
                        "date": date,
                        "lng": _to_float(fp.get("lng")),
                        "lat": _to_float(fp.get("lat")),
                        "wind": _ms_to_kt(fp.get("speed")),
                        "pressure": _to_int(fp.get("pressure")),
                        "code": CMA_STRONG_CODE.get(strong, ""),
                        "description": strong,
                    }
                    batch["points"].append(fpt)

        forecasts = list(batches_by_source.values())
        # 单点也没抓到的预测批丢弃
        forecasts = [b for b in forecasts if b["points"]]

        detail: StormDetail = {
            "id": f"cma-{tfid}",
            "name": str(payload.get("enname", "") or payload.get("name", "") or tfid),
            "name_cn": str(payload.get("name", "") or ""),
            "title": f"CMA Typhoon {payload.get('enname') or tfid} ({tfid})",
            "type": "Typhoon" if forecasts else "Unknown",
            "active": str(payload.get("isactive", "0")) == "1",
            "season": str(payload.get("starttime", "")[:4:]),
            "agencies": "CMA",
            "fetched_at": fetched_at,
            "track": track,
            "forecasts": forecasts,
        }
        logger.info(
            f"[cma] 详情成功: tfid={tfid}, "
            f"预测批 {len(forecasts)} 个（来源: {[b['source'] for b in forecasts]}）"
        )
        return detail

    def fetch_detail_by_name(self, enname: str) -> StormDetail | None:
        """按英文名跨源匹配抓取 CMA 详情。"""
        tfid = self.find_tfid_by_name(enname)
        if tfid is None:
            return None
        return self.fetch_detail(tfid)

    def find_tfid_by_name(self, enname: str) -> str | None:
        """通过活跃列表按英文名大小写不敏感匹配 tfid。"""
        if not enname or enname.strip().upper() in ("", "NONAME", "NONE"):
            return None
        target = enname.strip().lower()
        for item in self.fetch_active_raw():
            cand = str(item.get("enname", "")).strip().lower()
            if cand and cand == target:
                return str(item.get("tfid"))
        logger.info(f"[cma] 未匹配到英文名 {enname!r}")
        return None
