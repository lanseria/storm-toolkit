"""Storm Toolkit 全局配置。"""

import os
from datetime import timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

# ── 路径 ───────────────────────────────────────────────────────────────
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent  # src/

dotenv_path = PROJECT_ROOT.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)

DATA_DIR: Path = PROJECT_ROOT.parent / "data"
TRACKS_DIR: Path = DATA_DIR / "tracks"
HISTORY_DIR: Path = DATA_DIR / "history"
SATELLITE_DIR: Path = DATA_DIR / "satellite"
WATCHLIST_PATH: Path = DATA_DIR / "watchlist.json"
ACTIVE_STORMS_PATH: Path = DATA_DIR / "storms_active.json"


def track_file_for_storm(storm_id: str) -> Path:
    """根据台风 ID 生成对应的历史路径 JSON 文件路径。"""
    safe = storm_id.replace("/", "_")
    return TRACKS_DIR / f"{safe}.json"


for _d in (DATA_DIR, TRACKS_DIR, HISTORY_DIR, SATELLITE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── zoom.earth 数据源 ────────────────────────────────────────────────────
ZOOM_EARTH_BASE: str = "https://zoom.earth"
STORMS_API: str = f"{ZOOM_EARTH_BASE}/data/storms/"
HTTP_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": f"{ZOOM_EARTH_BASE}/",
    "Accept": "application/json",
}
HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "15"))

# ── 浙江水利厅 CMA 数据源 ────────────────────────────────────────────────
ZJ_CMA_BASE: str = os.getenv("ZJ_CMA_BASE", "https://typhoon.slt.zj.gov.cn")
ZJ_CMA_ACTIVITY_API: str = f"{ZJ_CMA_BASE}/Api/TyhoonActivity"
ZJ_CMA_INFO_API: str = f"{ZJ_CMA_BASE}/Api/TyphoonInfo"  # 拼接 /{tfid}
ZJ_CMA_HEADERS: dict[str, str] = {
    "User-Agent": HTTP_HEADERS["User-Agent"],
    "Referer": f"{ZJ_CMA_BASE}/",
    "Accept": "application/json",
}
# CMA 接口偶发不稳定，env 可快速回退到纯 zoom 模式
CMA_ENABLED: bool = os.getenv("CMA_ENABLED", "1") == "1"
# 每个 source 保留的最近预测批数（防止文件膨胀，每 3h 一发，4 批≈12h）
FORECAST_BATCHES_KEEP: int = int(os.getenv("FORECAST_BATCHES_KEEP", "4"))

# ── 调度 ───────────────────────────────────────────────────────────────
SCHEDULE_INTERVAL_SECONDS: int = int(os.getenv("SCHEDULE_INTERVAL_SECONDS", "3600"))
ACTIVE_LIST_REFRESH_SECONDS: int = int(os.getenv("ACTIVE_LIST_REFRESH_SECONDS", "3600"))

# ── Web ────────────────────────────────────────────────────────────────
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("WEB_PORT", "19995"))

# ── 卫星图生成 ──────────────────────────────────────────────────────────
# GIS 服务器地址（同 zoom-earth-map 的 NUXT_PUBLIC_GIS_SERVER_URL）
GIS_SERVER_URL: str = os.getenv("GIS_SERVER_URL", "http://bmcr1-wtr-r1:8080")
# 贴图 zoom 级别（zoom-earth-map 卫星贴图 maxzoom=7）
SATELLITE_TILE_ZOOM: int = int(os.getenv("SATELLITE_TILE_ZOOM", "7"))
# 单张卫星图边长（px），正方形输出；后续可自定义手机/电脑比例
SATELLITE_IMAGE_SIZE: int = int(os.getenv("SATELLITE_IMAGE_SIZE", "1080"))
# 贴图并发下载数
SATELLITE_TILE_WORKERS: int = int(os.getenv("SATELLITE_TILE_WORKERS", "8"))
# smiley-sans 字体下载地址（国内经 ghfast.top 加速）
FONT_DOWNLOAD_URL: str = os.getenv(
    "FONT_DOWNLOAD_URL",
    "https://ghfast.top/https://github.com/atelier-anchor/smiley-sans/"
    "releases/download/v2.0.1/smiley-sans-v2.0.1.zip",
)

# ── 边界与城市叠加层（照搬 zoom-earth-map 样式） ───────────────────────
ASSETS_DIR: Path = Path(__file__).resolve().parent / "assets"
COASTLINE_PATH: Path = ASSETS_DIR / "ne_50m_coastline.geojson"
CITIES_PATH: Path = ASSETS_DIR / "cities.json"
BOUNDARIES_DIR: Path = ASSETS_DIR / "boundaries"
# 阿里云 DataV 全国行政边界（含省界 Polygon）
CHINA_BOUNDARY_URL: str = os.getenv(
    "CHINA_BOUNDARY_URL",
    "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json",
)
CHINA_BOUNDARY_PATH: Path = BOUNDARIES_DIR / "100000_full.json"
# 是否叠加边界与城市（1/0），默认开启
OVERLAY_BOUNDARIES: bool = os.getenv("OVERLAY_BOUNDARIES", "1") == "1"
OVERLAY_CITIES: bool = os.getenv("OVERLAY_CITIES", "1") == "1"

# ── 时区 ───────────────────────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
UTC = timezone.utc
