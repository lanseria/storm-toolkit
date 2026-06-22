"""JSON 持久化模块。

文件分工：
- watchlist.json: 用户关注的台风 ID 集合（web 写、schedule 读）
- storms_active.json: 最近一次抓取的活跃台风列表（schedule 写、web 读）
- tracks/{id}.json: 关注台风的完整路径历史（schedule/web 写、web 读）

进程间通信通过这些文件完成，无锁但语义安全（每个文件单一写者）。
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import StormDetail, StormSummary, TrackHistoryEntry
from .utils import now_utc_iso, setup_logger, utc_to_beijing

logger = setup_logger("storm_toolkit.storage")


# ── watchlist ──────────────────────────────────────────────────────────
def load_watchlist() -> set[str]:
    """读取用户关注的台风 ID 集合。文件不存在时返回空集合。"""
    if not config.WATCHLIST_PATH.exists():
        return set()
    try:
        data = json.loads(config.WATCHLIST_PATH.read_text(encoding="utf-8"))
        return set(data.get("storm_ids", []))
    except (OSError, ValueError) as e:
        logger.error(f"读取 watchlist 失败: {e}")
        return set()


def save_watchlist(ids: set[str]) -> None:
    """原子写入 watchlist.json（先 tmp 后 rename）。"""
    payload = {
        "storm_ids": sorted(ids),
        "updated_at": now_utc_iso(),
    }
    _atomic_write_json(config.WATCHLIST_PATH, payload)


def add_to_watchlist(storm_id: str) -> None:
    """加入关注列表（幂等）。"""
    ids = load_watchlist()
    if storm_id in ids:
        return
    ids.add(storm_id)
    save_watchlist(ids)
    logger.info(f"已关注: {storm_id}")


def remove_from_watchlist(storm_id: str) -> None:
    """从关注列表移除（幂等）。"""
    ids = load_watchlist()
    if storm_id not in ids:
        return
    ids.discard(storm_id)
    save_watchlist(ids)
    logger.info(f"已取消关注: {storm_id}")


# ── active storms cache ────────────────────────────────────────────────
def save_active_storms(summaries: list[StormSummary]) -> None:
    """写入活跃台风列表缓存。"""
    payload = {
        "fetched_at": now_utc_iso(),
        "storms": summaries,
    }
    _atomic_write_json(config.ACTIVE_STORMS_PATH, payload)


def load_active_storms() -> dict:
    """读取活跃台风列表缓存。文件不存在时返回空结构。"""
    if not config.ACTIVE_STORMS_PATH.exists():
        return {"fetched_at": "", "storms": []}
    try:
        return json.loads(config.ACTIVE_STORMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"读取活跃列表缓存失败: {e}")
        return {"fetched_at": "", "storms": []}


# ── track history ──────────────────────────────────────────────────────
def append_storm_track(detail: StormDetail) -> int:
    """将 detail.track 中尚未记录的点追加到 tracks/{id}.json。

    以 path point 的 date 为主键去重；info 字段每次用最新详情覆盖。

    Returns:
        新增的轨迹点数量。
    """
    storm_id = detail["id"]
    path = config.track_file_for_storm(storm_id)

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"读取 {path.name} 失败，将覆盖: {e}")
            existing = {}

    history: list[TrackHistoryEntry] = existing.get("track_history", [])
    seen_dates: set[str] = {p["date"] for p in history}

    new_count = 0
    for point in detail["track"]:
        if point["date"] in seen_dates:
            continue
        history.append({
            "date": point["date"],
            "lng": point["lng"],
            "lat": point["lat"],
            "wind": point["wind"],
            "pressure": point["pressure"],
            "basin": point["basin"],
            "code": point["code"],
            "description": point["description"],
            "forecast": point["forecast"],
            "first_seen": detail["fetched_at"],
        })
        seen_dates.add(point["date"])
        new_count += 1

    history.sort(key=lambda p: p["date"])

    payload = {
        "id": storm_id,
        "info": {
            "name": detail["name"],
            "title": detail["title"],
            "type": detail["type"],
            "active": detail["active"],
            "season": detail["season"],
            "agencies": detail["agencies"],
        },
        "last_updated": now_utc_iso(),
        "track_history": history,
    }
    _atomic_write_json(path, payload)
    return new_count


def load_storm_track(storm_id: str) -> dict | None:
    """读取某个台风的完整路径历史。文件不存在时返回 None。"""
    path = config.track_file_for_storm(storm_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"读取 {path.name} 失败: {e}")
        return None


def list_watched_tracks() -> list[dict]:
    """读取所有关注台风的完整路径历史。"""
    watched = load_watchlist()
    results: list[dict] = []
    for storm_id in sorted(watched):
        track = load_storm_track(storm_id)
        if track is not None:
            results.append(track)
    return results


# ── helpers ────────────────────────────────────────────────────────────
def _atomic_write_json(path: Path, payload: dict) -> None:
    """原子写入 JSON：先写到临时文件再 rename，避免读写竞争产生半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
