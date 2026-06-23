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
WATCHLIST_PATH: Path = DATA_DIR / "watchlist.json"
ACTIVE_STORMS_PATH: Path = DATA_DIR / "storms_active.json"


def track_file_for_storm(storm_id: str) -> Path:
    """根据台风 ID 生成对应的历史路径 JSON 文件路径。"""
    safe = storm_id.replace("/", "_")
    return TRACKS_DIR / f"{safe}.json"


for _d in (DATA_DIR, TRACKS_DIR, HISTORY_DIR):
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

# ── 时区 ───────────────────────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
UTC = timezone.utc
