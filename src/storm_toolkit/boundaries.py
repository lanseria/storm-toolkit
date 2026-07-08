"""边界线与城市点位叠加层。

数据源与样式严格照搬 D:\\code\\zoom-earth-map：
- 国内省界：阿里云 DataV 100000_full.json（含所有省份 Polygon）
  - 样式：双层描边（下层 #333333/3px/0.6 + 上层 #ffffff/1px/0.8）
- 国外海岸线：Natural Earth 1:50m（已剔除中国）
  - 样式：单层 #eeeeee/2px/0.5
- 城市点位：new_data.json（343 个城市）
  - 省会/直辖市 level 1：圆点半径 4 + 描边 1 #333333 + 标签字号 13
  - 地级市 level 2：圆点半径 3 + 描边 0.8 + 标签字号 12
  - 默认白色 #ffffff，hover 淡蓝（卫星图为静态图，仅用默认白）

渲染逻辑：把地理要素投影到图片像素坐标，只保留落在图框内的片段。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import requests
from PIL import Image, ImageDraw, ImageFont

from . import config
from .utils import setup_logger

logger = setup_logger("storm_toolkit.boundaries")

# ── 样式常量（照搬 zoom-earth-map useBoundaries.ts / useCityMarkers.ts） ──
# 国内边界：双层描边
CHINA_OUTLINE_COLOR = "#333333"   # 下层粗深灰
CHINA_OUTLINE_WIDTH = 3           # px（按 1080 图缩放）
CHINA_OUTLINE_ALPHA = 153         # 0.6 * 255
CHINA_LINE_COLOR = "#ffffff"      # 上层细白
CHINA_LINE_WIDTH = 1
CHINA_LINE_ALPHA = 204            # 0.8 * 255

# 国外海岸线：单层浅灰
COAST_COLOR = "#eeeeee"
COAST_WIDTH = 2
COAST_ALPHA = 128                 # 0.5 * 255

# 城市点（useCityMarkers.ts:64-137）
CITY_COLOR = "#ffffff"            # 默认白色（无 hover）
CITY_STROKE = "#333333"
CITY_LEVEL1_RADIUS = 4            # 省会/直辖市
CITY_LEVEL1_STROKE = 1
CITY_LEVEL1_FONTSIZE = 13
CITY_LEVEL2_RADIUS = 3            # 地级市
CITY_LEVEL2_STROKE = 0.8
CITY_LEVEL2_FONTSIZE = 12
CITY_LABEL_HALO = "#333333"       # 文字描边
CITY_LABEL_OFFSET_Y = -1.8        # 标签向上偏移（以字号倍数计）


def _hex_rgb(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


# ── 数据加载（带本地缓存） ───────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_global_coastline() -> list[list[tuple[float, float]]]:
    """加载全球海岸线（不含中国）。返回 LineString 坐标列表。

    每个元素是一条线 [（lng, lat）, ...]。
    """
    path = config.COASTLINE_PATH
    if not path.exists():
        logger.warning(f"海岸线数据缺失：{path}")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    lines: list[list[tuple[float, float]]] = []
    for feat in data.get("features", []):
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "LineString":
            lines.append([(float(x), float(y)) for x, y in coords])
        elif gtype == "MultiLineString":
            for part in coords:
                lines.append([(float(x), float(y)) for x, y in part])
    logger.info(f"加载海岸线：{len(lines)} 条")
    return lines


@lru_cache(maxsize=1)
def load_cities() -> list[dict]:
    """加载城市列表。"""
    path = config.CITIES_PATH
    if not path.exists():
        logger.warning(f"城市数据缺失：{path}")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    logger.info(f"加载城市：{len(data)} 个")
    return data


@lru_cache(maxsize=1)
def load_china_boundaries() -> list[list[tuple[float, float]]]:
    """加载国内行政边界。返回省界线段列表。

    首次调用从 CHINA_BOUNDARY_URL 下载并缓存到本地，后续读本地。
    把每个省份 Polygon 的外环 + 内环都转成线段集合。
    """
    path = config.CHINA_BOUNDARY_PATH
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = _download_china_boundary(path)
    if not data:
        return []

    rings: list[list[tuple[float, float]]] = []
    for feat in data.get("features", []):
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon":
            for ring in coords:
                rings.append([(float(x), float(y)) for x, y in ring])
        elif gtype == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    rings.append([(float(x), float(y)) for x, y in ring])
    logger.info(f"加载国内边界：{len(rings)} 个环")
    return rings


def _download_china_boundary(path: Path) -> dict:
    """从阿里云 DataV 下载全国边界并缓存。"""
    url = config.CHINA_BOUNDARY_URL
    logger.info(f"下载国内行政边界：{url}")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.error(f"下载国内边界失败：{e}")
        return {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info(f"国内边界已缓存：{path}")
    return data


# ── 像素投影与裁切 ───────────────────────────────────────────────────────
def _project_ring(
    ring: list[tuple[float, float]],
    center_lng: float,
    center_lat: float,
    zoom: int,
    half: float,
) -> list[tuple[float, float]]:
    """把一条经纬度环投影到以 (center_lng, center_lat) 为中心的图片像素坐标。"""
    # 复用 imagery 的坐标转换（避免循环导入，内联简单版）
    from .imagery import lng2pix, lat2pix
    cpx = lng2pix(center_lng, zoom)
    cpy = lat2pix(center_lat, zoom)
    return [
        (lng2pix(lng, zoom) - cpx + half, lat2pix(lat, zoom) - cpy + half)
        for lng, lat in ring
    ]


def _iter_visible_segments(
    pts: list[tuple[float, float]],
    size: int,
    margin: float = 50.0,
) -> Iterator[tuple[tuple[float, float], tuple[float, float]]]:
    """产出落在图框 [−margin, size+margin] 内的相邻点对。

    简单裁切：只要线段至少一端在缓冲区内就画整段（地图边界线用，过度裁切比断线美观）。
    """
    x_lo, x_hi = -margin, size + margin
    y_lo, y_hi = -margin, size + margin
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        # 两端都在缓冲区外且同侧 → 跳过
        if (x0 < x_lo and x1 < x_lo) or (x0 > x_hi and x1 > x_hi):
            continue
        if (y0 < y_lo and y1 < y_lo) or (y0 > y_hi and y1 > y_hi):
            continue
        yield (x0, y0), (x1, y1)


# ── 绘制 ─────────────────────────────────────────────────────────────────
def draw_overlays(
    overlay: Image.Image,
    center_lng: float,
    center_lat: float,
    zoom: int,
    size: int,
    font_fn,
) -> None:
    """在透明 overlay 上绘制边界线与城市点位。

    Args:
        overlay: RGBA 透明图层，直接在上面绘制
        center_lng/lat: 图片中心的经纬度（台风中心）
        zoom: 贴图 zoom 级别
        size: 图片边长 px
        font_fn: 字号 → ImageFont 的加载函数
    """
    draw = ImageDraw.Draw(overlay)
    half = size / 2.0
    scale = size / 1080.0  # 样式参数按 1080 基准缩放

    if config.OVERLAY_BOUNDARIES:
        _draw_china_boundaries(draw, center_lng, center_lat, zoom, size, half, scale)
        _draw_global_coastline(draw, center_lng, center_lat, zoom, size, half, scale)

    if config.OVERLAY_CITIES:
        _draw_cities(draw, center_lng, center_lat, zoom, size, half, scale, font_fn)


def _draw_china_boundaries(
    draw: ImageDraw.ImageDraw,
    center_lng: float,
    center_lat: float,
    zoom: int,
    size: int,
    half: float,
    scale: float,
) -> None:
    rings = load_china_boundaries()
    if not rings:
        return

    # 预投影所有环
    projected = [
        _project_ring(ring, center_lng, center_lat, zoom, half)
        for ring in rings
    ]

    # 下层粗深灰描边
    outline_w = max(1, round(CHINA_OUTLINE_WIDTH * scale))
    outline_fill = _hex_rgb(CHINA_OUTLINE_COLOR, CHINA_OUTLINE_ALPHA)
    for pts in projected:
        for (x0, y0), (x1, y1) in _iter_visible_segments(pts, size):
            draw.line([(x0, y0), (x1, y1)], fill=outline_fill, width=outline_w)

    # 上层细白线
    line_w = max(1, round(CHINA_LINE_WIDTH * scale))
    line_fill = _hex_rgb(CHINA_LINE_COLOR, CHINA_LINE_ALPHA)
    for pts in projected:
        for (x0, y0), (x1, y1) in _iter_visible_segments(pts, size):
            draw.line([(x0, y0), (x1, y1)], fill=line_fill, width=line_w)


def _draw_global_coastline(
    draw: ImageDraw.ImageDraw,
    center_lng: float,
    center_lat: float,
    zoom: int,
    size: int,
    half: float,
    scale: float,
) -> None:
    lines = load_global_coastline()
    if not lines:
        return

    w = max(1, round(COAST_WIDTH * scale))
    fill = _hex_rgb(COAST_COLOR, COAST_ALPHA)
    for line in lines:
        pts = _project_ring(line, center_lng, center_lat, zoom, half)
        for (x0, y0), (x1, y1) in _iter_visible_segments(pts, size):
            draw.line([(x0, y0), (x1, y1)], fill=fill, width=w)


def _draw_cities(
    draw: ImageDraw.ImageDraw,
    center_lng: float,
    center_lat: float,
    zoom: int,
    size: int,
    half: float,
    scale: float,
    font_fn,
) -> None:
    cities = load_cities()
    if not cities:
        return

    from .imagery import lng2pix, lat2pix
    cpx = lng2pix(center_lng, zoom)
    cpy = lat2pix(center_lat, zoom)

    # 预计算各等级样式
    styles = {
        1: {
            "r": max(2, round(CITY_LEVEL1_RADIUS * scale)),
            "sw": max(1, round(CITY_LEVEL1_STROKE * scale)),
            "fs": max(10, round(CITY_LEVEL1_FONTSIZE * scale)),
        },
        2: {
            "r": max(2, round(CITY_LEVEL2_RADIUS * scale)),
            "sw": max(1, round(CITY_LEVEL2_STROKE * scale)),
            "fs": max(9, round(CITY_LEVEL2_FONTSIZE * scale)),
        },
    }
    fill = _hex_rgb(CITY_COLOR, 255)
    stroke = _hex_rgb(CITY_STROKE, 255)
    halo = _hex_rgb(CITY_LABEL_HALO, 220)

    for city in cities:
        try:
            lng = float(city["lng"])
            lat = float(city["lat"])
            level = int(city.get("level", 2))
        except (KeyError, ValueError, TypeError):
            continue

        px = lng2pix(lng, zoom) - cpx + half
        py = lat2pix(lat, zoom) - cpy + half
        if px < -20 or px > size + 20 or py < -20 or py > size + 20:
            continue

        st = styles.get(level, styles[2])
        r, sw = st["r"], st["sw"]
        # 圆点：白填充 + 深灰描边
        draw.ellipse(
            [(px - r, py - r), (px + r, py + r)],
            fill=fill, outline=stroke, width=sw,
        )
        # 文字标签（向上偏移）
        name = city.get("name", "")
        if name:
            font = font_fn(st["fs"])
            offset_y = CITY_LABEL_OFFSET_Y * st["fs"]
            # 用 textbbox 计算居中，并先画描边再画填充实现 halo
            tx = px
            ty = py + offset_y - st["fs"]  # anchor top → 文字在点上方
            try:
                bbox = draw.textbbox((0, 0), name, font=font)
                tw = bbox[2] - bbox[0]
                tx -= tw / 2
            except Exception:
                pass
            # halo：八方向偏移画深灰底
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (1, -1), (-1, 1), (1, 1)):
                draw.text((tx + dx, ty + dy), name, font=font, fill=halo)
            draw.text((tx, ty), name, font=font, fill=fill)
