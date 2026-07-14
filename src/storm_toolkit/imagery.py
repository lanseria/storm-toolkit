"""卫星图生成模块。

从 GIS 服务器（同 zoom-earth-map）拉取卫星 xyz 贴图，结合本项目的台风路径数据，
为每个卫星时间戳生成以「插值后的台风中心」为图中心的卫星图，叠加台风信息，
最终打包为 zip。

坐标转换与卫星配置移植自 zoom-earth-map 的 useTileGrid.ts / useSatelliteTiles.ts：
- 卫星贴图 URL：{gis}/zoom-earth-tiles/{satId}/{z}/{y}/{x}/{timestamp}.jpg
- 卫星按经度非重叠划分覆盖全球，西北太平洋（90~180E）由 himawari 覆盖。
"""

from __future__ import annotations

import io
import math
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from . import config
from .utils import BEIJING_TZ, UTC, setup_logger

logger = setup_logger("storm_toolkit.imagery")


@dataclass(frozen=True)
class GenConfig:
    """卫星图生成参数（贯穿整个调用链）。

    所有尺寸均为像素。城市字号 None 表示用 boundaries.py 的默认值（按图缩放）。
    """

    width: int = 1080
    height: int = 1080
    show_boundaries: bool = True
    show_cities: bool = True
    city_font_scale: float = 1.0  # 城市标签字号缩放系数（1.0 = 默认）

    @property
    def size_sig(self) -> str:
        """用于缓存文件名的尺寸签名，如 '1080x1080'。"""
        return f"{self.width}x{self.height}"

TILE_SIZE = 256

# 卫星按经度非重叠划分（与 zoom-earth-map/constants/map.ts 一致）。
# bounds = [west, south, east, north]
SATELLITES: list[dict[str, Any]] = [
    {"id": "goes-west", "name": "GOES-West", "bounds": [-180, -60, -135, 60]},
    {"id": "goes-east", "name": "GOES-East", "bounds": [-135, -60, -22.5, 60]},
    {"id": "mtg-zero", "name": "MTG", "bounds": [-22.5, -60, 45, 60]},
    {"id": "msg-iodc", "name": "MSG-IODC", "bounds": [45, -60, 90, 60]},
    {"id": "himawari", "name": "Himawari", "bounds": [90, -60, 180, 60]},
]

# ── 台风等级 / 机构 配色（照搬 D:\code\zoom-earth-map） ─────────────────
# 来源：app/composables/useStormOverlay.ts STORM_COLOR_BY_CODE（第 37-50 行）
#       app/components/TyphoonPanel.vue STORM_COLOR_BY_CODE（第 8-21 行）
# 强度 code → 颜色 hex（与 zoom-earth-map 地图 overlay 完全一致）
STORM_COLOR_BY_CODE: dict[str, str] = {
    "D": "#0a84ff",   # 热带低压（蓝），同时是兜底默认色
    "S": "#00f060",   # 热带风暴（亮绿）
    "1": "#ffcc00",   # 强热带风暴（黄）
    "SS": "#ffcc00",  # Severe Tropical Storm（强热带风暴）
    "2": "#ff9400",   # 台风（橙）
    "T": "#ff9400",   # Typhoon（台风）
    "3": "#ff5900",   # 强台风（深橙红）
    "ST": "#ff5900",  # Very Strong Typhoon（强台风）
    "4": "#ff0022",   # 超强台风 / 暴力台风（红）
    "VT": "#ff0022",  # Violent Typhoon（暴力台风）
    "5": "#FF55BB",   # Cat 5（粉红）
    "SU": "#FF55BB",  # 超级台风
    "E": "#0a84ff",   # 温带气旋（与 D 同色，回退到默认蓝）
}

# 来源：useStormOverlay.ts STORM_COLOR_BY_SOURCE（第 51-59 行）
# 预测机构 source → 颜色 hex（预测线按机构着色）
STORM_COLOR_BY_SOURCE: dict[str, str] = {
    "zoom-earth": "#00b4d8",
    "cma": "#f87171",
    "jma": "#f4845f",
    "jtwc": "#a3e635",
    "cwa": "#60a5fa",
    "hko": "#fbbf24",
    "kma": "#a78bfa",
}
STORM_SOURCE_FALLBACK = "#94a3b8"  # 未知机构兜底色

# 来源：useStormOverlay.ts 第 60-66 行（实况线 / 半径 等样式常量）
STORM_ACTUAL_LINE_COLOR = "#fbbf24"  # 实况路径线统一琥珀色（不分等级）
STORM_POINT_RADIUS = 5               # 实况圆点半径（地图像素，按图边长缩放后使用）


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """#RRGGBB → (r, g, b)。兼容大小写与前缀。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _storm_color(code: str | None, alpha: int = 255) -> tuple[int, int, int, int]:
    """按强度 code 取 RGBA 颜色（与 zoom-earth-map 地图 overlay 一致）。"""
    c = STORM_COLOR_BY_CODE.get((code or "").upper(), STORM_COLOR_BY_CODE["D"])
    return _hex_to_rgb(c) + (alpha,)


# 保留向后兼容别名（其他模块或外部脚本可能引用）
CODE_COLOR = {k: _hex_to_rgb(v) for k, v in STORM_COLOR_BY_CODE.items()}
DEFAULT_CODE_COLOR = _hex_to_rgb(STORM_COLOR_BY_CODE["D"])

_FONT_PATH_CANDIDATES = [
    # 1. 项目运行时下载的 smiley-sans
    Path(__file__).resolve().parent / "assets" / "fonts" / "SmileySans-Oblique.ttf",
    # 2. Windows 系统字体回退
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    # 3. Linux 常见 CJK 字体
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc"),
]


# ── 坐标转换（Web Mercator） ────────────────────────────────────────────
def _world_size(zoom: int) -> int:
    return TILE_SIZE * (2 ** zoom)


def lng2pix(lng: float, zoom: int) -> float:
    """经度 → 全局像素 x（浮点，精确）。"""
    return (lng + 180.0) / 360.0 * _world_size(zoom)


def lat2pix(lat: float, zoom: int) -> float:
    """纬度 → 全局像素 y（浮点，精确）。"""
    lat_rad = math.radians(lat)
    return (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * _world_size(zoom)


def pix2lng(x: float, zoom: int) -> float:
    return x / _world_size(zoom) * 360.0 - 180.0


def pix2lat(y: float, zoom: int) -> float:
    n = math.pi - 2.0 * math.pi * y / _world_size(zoom)
    return math.degrees(math.atan(math.sinh(n)))


def lng2tile(lng: float, zoom: int) -> int:
    """经度 → 贴图列号（取整，clamp 到合法范围）。"""
    max_tile = 2 ** zoom - 1
    return max(0, min(int(math.floor(lng2pix(lng, zoom) / TILE_SIZE)), max_tile))


def lat2tile(lat: float, zoom: int) -> int:
    """纬度 → 贴图行号（取整，clamp）。"""
    max_tile = 2 ** zoom - 1
    return max(0, min(int(math.floor(lat2pix(lat, zoom) / TILE_SIZE)), max_tile))


def get_satellite_for_lng(lng: float) -> dict[str, Any] | None:
    """按经度返回覆盖该经度的卫星配置。"""
    for sat in SATELLITES:
        w, _, e, _ = sat["bounds"]
        if w <= lng < e:
            return sat
    # 经度 180 落在 himawari 右边界，单独兜底
    if lng >= 180:
        return SATELLITES[-1]
    return None


def find_closest_timestamp(timestamps: list[int], target: int) -> int:
    """从升序时间戳数组中找最接近 target 的那个。移植自 timeline.ts。"""
    if not timestamps:
        return target
    closest = timestamps[0]
    min_diff = float("inf")
    for t in timestamps:
        diff = abs(t - target)
        if diff < min_diff:
            min_diff = diff
            closest = t
    return closest


# ── 时间 / 路径插值 ────────────────────────────────────────────────────
def _parse_iso_z(dt_str: str) -> datetime:
    """解析 ISO8601 字符串（兼容带/不带 Z、带毫秒等情况）为 aware UTC datetime。"""
    s = dt_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _interp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def interpolate_track(
    track_history: list[dict],
    target_dt: datetime,
) -> dict[str, Any] | None:
    """对 target_dt 时刻的台风中心做线性插值。

    Returns:
        {lng, lat, wind, pressure, code, description} 或 None（target 超出路径范围）。
    """
    if not track_history:
        return None

    # 用 key 排序而非元组比较：当多个点时间相同时，元组 (datetime, dict) 会
    # 退化到比较 dict（不支持 <）而抛 TypeError。key 函数只比 datetime。
    valid = [p for p in track_history if p.get("date")]
    valid.sort(key=lambda p: _parse_iso_z(p["date"]))
    if not valid:
        return None

    pts = [(_parse_iso_z(p["date"]), p) for p in valid]

    target = target_dt.astimezone(UTC)
    if target < pts[0][0] or target > pts[-1][0]:
        return None

    # 二分定位相邻两点
    lo, hi = 0, len(pts) - 1
    if target == pts[lo][0]:
        return _point_payload(pts[lo][1])
    if target == pts[hi][0]:
        return _point_payload(pts[hi][1])

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if pts[mid][0] <= target:
            lo = mid
        else:
            hi = mid

    t0, p0 = pts[lo]
    t1, p1 = pts[hi]
    span = (t1 - t0).total_seconds()
    frac = ((target - t0).total_seconds() / span) if span > 0 else 0.0

    return {
        "lng": _interp(p0["lng"], p1["lng"], frac),
        "lat": _interp(p0["lat"], p1["lat"], frac),
        "wind": round(_interp(p0.get("wind", 0), p1.get("wind", 0), frac)),
        "pressure": round(_interp(p0.get("pressure", 0), p1.get("pressure", 0), frac)),
        "code": p1.get("code", "") or p0.get("code", ""),
        "description": p1.get("description", "") or p0.get("description", ""),
    }


def _point_payload(p: dict) -> dict[str, Any]:
    return {
        "lng": float(p["lng"]),
        "lat": float(p["lat"]),
        "wind": int(p.get("wind", 0) or 0),
        "pressure": int(p.get("pressure", 0) or 0),
        "code": p.get("code", "") or "",
        "description": p.get("description", "") or "",
    }


# ── 贴图下载（进程内缓存） ─────────────────────────────────────────────
class TileFetcher:
    """带内存缓存的贴图下载器：同一 (sat, z, y, x, ts) 只下载一次。"""

    def __init__(self, session: requests.Session, timeout: int = 20):
        self.session = session
        self.timeout = timeout
        self._cache: dict[tuple[str, int, int, int, int], Image.Image | None] = {}

    def tile_url(self, sat_id: str, z: int, y: int, x: int, timestamp: int) -> str:
        # 注意 zoom-earth-map 用 {z}/{y}/{x} 顺序
        base = config.GIS_SERVER_URL.rstrip("/")
        return (
            f"{base}/zoom-earth-tiles/{sat_id}/{z}/{y}/{x}/{timestamp}.jpg"
        )

    def fetch(
        self, sat_id: str, z: int, y: int, x: int, timestamp: int
    ) -> Image.Image | None:
        key = (sat_id, z, y, x, timestamp)
        if key in self._cache:
            return self._cache[key]
        url = self.tile_url(sat_id, z, y, x, timestamp)
        img = self._do_fetch(url)
        self._cache[key] = img
        return img

    def _do_fetch(self, url: str) -> Image.Image | None:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200 or not resp.content:
                return None
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except (requests.RequestException, OSError) as e:
            logger.debug(f"贴图下载失败 {url}: {e}")
            return None


# ── 单帧构建 ───────────────────────────────────────────────────────────
def build_frame(
    center_lng: float,
    center_lat: float,
    timestamp: int,
    zoom: int,
    width: int,
    height: int,
    fetcher: TileFetcher,
    session: requests.Session,
) -> Image.Image:
    """以 (center_lng, center_lat) 为中心，拼接贴图并裁出 width×height 图片。"""
    cx = lng2pix(center_lng, zoom)
    cy = lat2pix(center_lat, zoom)

    half_w = width / 2.0
    half_h = height / 2.0
    x_min_pix = cx - half_w
    y_min_pix = cy - half_h
    x_max_pix = cx + half_w
    y_max_pix = cy + half_h

    tx_min = max(0, int(math.floor(x_min_pix / TILE_SIZE)))
    tx_max = min(2 ** zoom - 1, int(math.floor(x_max_pix / TILE_SIZE)))
    ty_min = max(0, int(math.floor(y_min_pix / TILE_SIZE)))
    ty_max = min(2 ** zoom - 1, int(math.floor(y_max_pix / TILE_SIZE)))

    sat = get_satellite_for_lng(center_lng) or SATELLITES[-1]
    sat_id = sat["id"]

    # 并发下载本帧所需贴图
    coords = [
        (ty, tx)
        for ty in range(ty_min, ty_max + 1)
        for tx in range(tx_min, tx_max + 1)
    ]

    def _load(coord):
        ty, tx = coord
        return coord, fetcher.fetch(sat_id, zoom, ty, tx, timestamp)

    tiles: dict[tuple[int, int], Image.Image | None] = {}
    workers = max(1, config.SATELLITE_TILE_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for coord, img in pool.map(_load, coords):
            tiles[coord] = img

    # 拼接大图
    cols = tx_max - tx_min + 1
    rows = ty_max - ty_min + 1
    mosaic_w = cols * TILE_SIZE
    mosaic_h = rows * TILE_SIZE
    mosaic = Image.new("RGB", (mosaic_w, mosaic_h), (10, 10, 14))

    for (ty, tx), img in tiles.items():
        if img is None:
            continue
        px = (tx - tx_min) * TILE_SIZE
        py = (ty - ty_min) * TILE_SIZE
        mosaic.paste(img, (px, py))

    # 在大图坐标系中裁切 width×height
    offset_x = int(round(x_min_pix - tx_min * TILE_SIZE))
    offset_y = int(round(y_min_pix - ty_min * TILE_SIZE))

    # 边界保护：mosaic 可能比裁切框小（极端经纬度），用裁切框与 mosaic 求交
    crop_x0 = max(0, offset_x)
    crop_y0 = max(0, offset_y)
    crop_x1 = min(mosaic_w, offset_x + width)
    crop_y1 = min(mosaic_h, offset_y + height)

    cropped = mosaic.crop((crop_x0, crop_y0, crop_x1, crop_y1))

    # 若裁出尺寸不足（贴图范围到边界），补黑边到 width×height
    if cropped.size != (width, height):
        canvas = Image.new("RGB", (width, height), (10, 10, 14))
        paste_x = crop_x0 - offset_x
        paste_y = crop_y0 - offset_y
        canvas.paste(cropped, (paste_x, paste_y))
        cropped = canvas

    return cropped


# ── 字体 ───────────────────────────────────────────────────────────────
def _font_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "fonts"


def ensure_font() -> Path | None:
    """确保字体可用：优先项目内 smiley-sans（首次下载），否则回退系统字体。

    下载失败不抛异常，自动回退到系统 CJK 字体，保证功能可用。
    """
    # 1. 优先使用项目内已下载的 smiley-sans
    target = _font_dir() / "SmileySans-Oblique.ttf"
    if target.exists():
        return target

    # 2. 首次使用时下载 smiley-sans
    try:
        _download_smiley_sans(target)
        if target.exists():
            return target
    except Exception as e:
        logger.warning(f"下载 smiley-sans 字体失败，将回退系统字体: {e}")

    # 3. 回退到系统字体
    for candidate in _FONT_PATH_CANDIDATES[1:]:
        if candidate.exists():
            return candidate
    return None


def _download_smiley_sans(target: Path) -> None:
    """从 FONT_DOWNLOAD_URL 下载 zip 并解压 SmileySans-Oblique.ttf。"""
    import tempfile
    import urllib.request

    target.parent.mkdir(parents=True, exist_ok=True)
    url = config.FONT_DOWNLOAD_URL
    logger.info(f"下载 smiley-sans 字体: {url}")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # 找到 SmileySans-Oblique.ttf（可能在子目录）
        name = next(
            (n for n in zf.namelist() if n.endswith("SmileySans-Oblique.ttf")),
            None,
        )
        if name is None:
            raise RuntimeError("zip 内未找到 SmileySans-Oblique.ttf")
        target.write_bytes(zf.read(name))
    logger.info(f"字体已保存: {target}")


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = ensure_font()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return ImageFont.load_default()


# ── 注释叠加 ───────────────────────────────────────────────────────────
def annotate_image(
    img: Image.Image,
    info: dict,
    point: dict,
    timestamp: int,
    track_history: list[dict],
    zoom: int,
    gc: GenConfig,
    generated_at: str,
) -> Image.Image:
    """在卫星图上叠加边界/城市、台风信息、中心十字标与历史轨迹线。"""
    width, height = gc.width, gc.height
    img = img.convert("RGBA")

    # 边界与城市画在卫星图本体上（底层），让台风轨迹浮在其上方
    if gc.show_boundaries or gc.show_cities:
        try:
            from . import boundaries
            boundaries.draw_overlays(
                img, point["lng"], point["lat"], zoom, width, height,
                _load_font, gc.show_boundaries, gc.show_cities, gc.city_font_scale,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"边界/城市叠加失败（忽略继续）：{e}")

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 用较长边作为字号基准（与正方形时视觉接近）
    long_side = max(width, height)
    title_font = _load_font(max(22, long_side // 40))
    body_font = _load_font(max(18, long_side // 54))

    # 中心十字标（台风中心）—— 按当前时刻强度着色（与 zoom-earth-map overlay 一致）
    cx, cy = width // 2, height // 2
    mark_color = _storm_color(point.get("code"), alpha=240)
    arm = max(14, long_side // 60)
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=mark_color, width=3)
    draw.line([(cx, cy - arm), (cx, cy + arm)], fill=mark_color, width=3)
    # 中心圆点：按 zoom-earth-map 样式 —— 白色描边 + 强度色填充
    center_r = max(6, long_side // 90)
    draw.ellipse(
        [(cx - center_r, cy - center_r), (cx + center_r, cy + center_r)],
        outline=(255, 255, 255, 230), width=2,
    )

    # 历史轨迹线（当前时刻之前的点）—— 实况线琥珀色 + 圆点按强度着色
    _draw_track_line(draw, img.size, point, track_history, timestamp, zoom)

    # 信息面板（底部半透明黑底）
    panel_h = max(110, height // 7)
    panel_y0 = height - panel_h
    draw.rectangle(
        [(0, panel_y0), (width, height)], fill=(0, 0, 0, 170)
    )

    name_cn = info.get("name_cn") or ""
    title = info.get("title") or ""
    title_text = f"{name_cn} · {title}" if name_cn else title

    # 北京时间
    dt_bj = datetime.fromtimestamp(timestamp, tz=BEIJING_TZ)
    time_str = dt_bj.strftime("%Y-%m-%d %H:%M (BJT)")

    code = point.get("code") or "-"
    desc = point.get("description") or ""
    wind = point.get("wind", 0)
    pressure = point.get("pressure", 0)
    lng = point.get("lng", 0)
    lat = point.get("lat", 0)

    line1 = title_text
    line2 = (
        f"{time_str}  ·  {desc}({code})  ·  风速 {wind} kt  ·  气压 {pressure} hPa"
    )
    line3 = f"中心 ({lng:.1f}°E, {lat:.1f}°N)  ·  数据源 {info.get('agencies', '')}  ·  生成 {generated_at}"

    pad = max(12, long_side // 80)
    y = panel_y0 + pad
    draw.text((pad, y), line1, font=title_font, fill=(255, 255, 255, 255))
    y += int(title_font.size * 1.4)
    draw.text((pad, y), line2, font=body_font, fill=(220, 240, 255, 240))
    y += int(body_font.size * 1.4)
    draw.text((pad, y), line3, font=body_font, fill=(180, 200, 220, 220))

    return Image.alpha_composite(img, overlay).convert("RGB")


def _draw_track_line(
    draw: ImageDraw.ImageDraw,
    img_size: tuple[int, int],
    current_point: dict,
    track_history: list[dict],
    current_ts: int,
    zoom: int,
) -> None:
    """在图上绘制当前时刻之前的历史轨迹折线（样式照搬 zoom-earth-map）。

    - 实况线：统一琥珀色 #fbbf24，宽 3，不透明度 0.9（与 useStormOverlay.ts actualLines 一致）
    - 圆点：按强度 code 着色（STORM_COLOR_BY_CODE），白色描边 1.5px（与 .storm-hit CSS 一致）
    """
    width, height = img_size
    half_x = width / 2.0
    half_y = height / 2.0
    center_lng = current_point["lng"]
    center_lat = current_point["lat"]
    center_pix_x = lng2pix(center_lng, zoom)
    center_pix_y = lat2pix(center_lat, zoom)

    # 按较长边缩放样式参数（zoom-earth-map 在地图上是固定像素，卫星图按比例放大）
    scale = max(width, height) / 1080.0
    point_r = max(3, round(STORM_POINT_RADIUS * scale))  # 地图 5px → 1080 图约 5px
    line_width = max(2, round(3 * scale))
    stroke_width = max(1, round(1.5 * scale))

    pts: list[tuple[float, float, str | None]] = []
    for p in sorted(track_history, key=lambda x: x.get("date", "")):
        try:
            dt = _parse_iso_z(p["date"])
        except (ValueError, TypeError):
            continue
        if int(dt.timestamp()) > current_ts:
            break
        px = lng2pix(p["lng"], zoom) - center_pix_x + half_x
        py = lat2pix(p["lat"], zoom) - center_pix_y + half_y
        pts.append((px, py, p.get("code")))

    if len(pts) < 2:
        return

    # 实况线：统一琥珀色（zoom-earth-map useStormOverlay.ts: STORM_ACTUAL_LINE_COLOR）
    actual_line_color = _hex_to_rgb(STORM_ACTUAL_LINE_COLOR) + (230,)
    for i in range(1, len(pts)):
        x0, y0, _ = pts[i - 1]
        x1, y1, _ = pts[i]
        draw.line([(x0, y0), (x1, y1)], fill=actual_line_color, width=line_width)

    # 历史圆点：按强度着色 + 白色描边（.storm-hit { stroke: rgba(255,255,255,0.9) }）
    white_stroke = (255, 255, 255, 230)
    for px, py, code in pts:
        fill = _storm_color(code, alpha=255)
        draw.ellipse(
            [(px - point_r, py - point_r), (px + point_r, py + point_r)],
            fill=fill, outline=white_stroke, width=stroke_width,
        )


# ── 主流程：拉时间戳 → 逐帧 → zip ─────────────────────────────────────
def _fetch_timestamps(session: requests.Session, sat_id: str = "himawari") -> list[int]:
    """从 GIS 服务器拉取可用卫星时间戳（Unix 秒，升序）。"""
    base = config.GIS_SERVER_URL.rstrip("/")
    url = f"{base}/zoom-earth-tiles/{sat_id}/timestamps.json"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        ts = [int(t) for t in data]
    elif isinstance(data, dict) and "timestamps" in data:
        ts = [int(t) for t in data["timestamps"]]
    else:
        ts = []
    return sorted(set(ts))


def _filter_timestamps(
    all_ts: list[int], track_history: list[dict]
) -> list[int]:
    """筛出落在台风路径 [起点, 终点] 时间范围内、且贴图实际存在的卫星时间戳。

    返回所有匹配时间戳（不做采样），保证视频剪辑的时间连续性。
    """
    if not track_history or not all_ts:
        return []
    times = sorted(_parse_iso_z(p["date"]) for p in track_history if p.get("date"))
    if not times:
        return []
    t0 = int(times[0].timestamp())
    t1 = int(times[-1].timestamp())
    return [t for t in all_ts if t0 <= t <= t1]


def generate_storm_satellite_zip(
    track_data: dict,
    output_zip: Path,
    gc: GenConfig | None = None,
    progress_cb: Any = None,
) -> int:
    """为单个台风生成卫星图 zip。

    Args:
        track_data: tracks/{id}.json 或 history/{id}.json 解析后的 dict
        output_zip: 输出 zip 路径
        gc: 生成参数（宽高、叠加层开关、城市字号缩放），None 用默认 1080×1080
        progress_cb: 可选回调 cb(current, total)，用于上报进度

    Returns:
        实际生成的图片数。
    """
    gc = gc or GenConfig()
    zoom = config.SATELLITE_TILE_ZOOM
    info = track_data.get("info") or {}
    track_history = track_data.get("track_history") or []
    storm_id = track_data.get("id", "storm")

    if not track_history:
        raise RuntimeError(f"台风 {storm_id} 无路径数据，无法生成卫星图")

    session = requests.Session()
    fetcher = TileFetcher(session)

    logger.info(f"[{storm_id}] 拉取卫星时间戳...")
    try:
        all_ts = _fetch_timestamps(session)
    except requests.RequestException as e:
        raise RuntimeError(
            f"无法连接卫星贴图服务器 ({config.GIS_SERVER_URL})：{e}"
        ) from e

    if not all_ts:
        raise RuntimeError("卫星服务器未返回任何时间戳")

    timestamps = _filter_timestamps(all_ts, track_history)
    if not timestamps:
        # 贴图可能已过期：尝试用最接近路径的可用时间戳
        times = sorted(_parse_iso_z(p["date"]) for p in track_history if p.get("date"))
        if times:
            target = int(times[len(times) // 2].timestamp())
            nearest = find_closest_timestamp(all_ts, target)
            # 仅当偏离不大（<= 2 天）才使用
            if abs(nearest - target) <= 2 * 86400:
                timestamps = [nearest]
        if not timestamps:
            raise RuntimeError(
                "卫星服务器无覆盖该台风时间段的贴图（可能已过期被清理）"
            )

    total = len(timestamps)
    logger.info(f"[{storm_id}] 计划生成 {total} 帧卫星图（{gc.size_sig}）")
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = output_zip.with_suffix(".zip.tmp")

    # 序号位数：按总帧数决定，保证字典序与时间序一致（视频剪辑按序导入）
    index_width = max(4, len(str(total)))

    count = 0
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, ts in enumerate(timestamps, 1):
            target_dt = datetime.fromtimestamp(ts, tz=UTC)
            point = interpolate_track(track_history, target_dt)
            if point is None:
                # 时间戳落在路径范围外（不应发生，过滤过），跳过
                if progress_cb:
                    progress_cb(i, total)
                continue

            frame = build_frame(
                point["lng"], point["lat"], ts, zoom, gc.width, gc.height, fetcher, session
            )
            frame = annotate_image(
                frame, info, point, ts, track_history, zoom, gc, generated_at
            )

            # 文件名：序号_台风ID_北京时间_Unix时间戳.jpg
            # 序号保证剪辑工具按序导入即为时间顺序；双时间戳便于人眼与脚本识别
            dt_bj = datetime.fromtimestamp(ts, tz=BEIJING_TZ)
            name = (
                f"{i:0{index_width}d}_{storm_id}_"
                f"{dt_bj.strftime('%Y%m%d_%H%M%S')}_{ts}.jpg"
            )
            buf = io.BytesIO()
            frame.save(buf, format="JPEG", quality=88)
            zf.writestr(name, buf.getvalue())
            count += 1

            if progress_cb:
                progress_cb(i, total)
            if i % 10 == 0:
                logger.info(f"[{storm_id}] 进度 {i}/{total}")

    os.replace(tmp_zip, output_zip)
    logger.info(f"[{storm_id}] 完成：{count} 帧已写入 {output_zip}")
    return count


# ── 任务表（进程内，供 web 层轮询） ────────────────────────────────────
class TaskState:
    """卫星图生成任务状态。"""

    def __init__(self, task_id: str, storm_id: str, size_sig: str = "1080x1080"):
        self.task_id = task_id
        self.storm_id = storm_id
        self.size_sig = size_sig  # 输出 zip 的尺寸签名，供下载端点定位
        self.status: str = "running"  # running | done | error
        self.current: int = 0
        self.total: int = 0
        self.error: str | None = None
        self.started_at: float = time.time()
        self.finished_at: float | None = None


_TASKS: dict[str, TaskState] = {}


def get_task(task_id: str) -> TaskState | None:
    return _TASKS.get(task_id)


def prune_tasks(max_keep: int = 50) -> None:
    """清理过旧的已完成任务，避免内存无限增长。"""
    if len(_TASKS) <= max_keep:
        return
    finished = sorted(
        (t for t in _TASKS.values() if t.status != "running"),
        key=lambda t: t.finished_at or 0,
    )
    for t in finished[: len(finished) - max_keep // 2]:
        _TASKS.pop(t.task_id, None)


def run_generation_task(
    task_id: str,
    storm_id: str,
    track_data: dict,
    output_zip: Path,
    gc: GenConfig | None = None,
) -> None:
    """线程入口：在后台执行生成并更新任务状态。"""
    gc = gc or GenConfig()
    state = TaskState(task_id, storm_id, gc.size_sig)
    _TASKS[task_id] = state

    def cb(current: int, total: int) -> None:
        state.current = current
        state.total = total

    try:
        count = generate_storm_satellite_zip(
            track_data, output_zip, gc=gc, progress_cb=cb
        )
        state.status = "done"
        state.total = state.total or count
        state.current = state.current or count
        logger.info(f"任务 {task_id} 完成")
    except Exception as e:  # noqa: BLE001
        state.status = "error"
        state.error = str(e)
        logger.exception(f"任务 {task_id} 失败: {e}")
    finally:
        state.finished_at = time.time()
        prune_tasks()
