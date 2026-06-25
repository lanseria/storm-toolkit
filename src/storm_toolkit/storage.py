"""JSON 持久化模块。

文件分工：
- watchlist.json: 用户关注的台风 ID 集合（web 写、schedule 读）
- storms_active.json: 最近一次抓取的活跃台风列表（schedule 写、web 读）
- tracks/{id}.json: 关注台风的完整路径历史与多源预测（schedule/web 写、web 读）

文件结构（tracks/{id}.json）:
{
  "id": "...",
  "info": {..., "cma_tfid": "..." | null},
  "last_updated": "...",
  "track_history": [TrackHistoryEntry],   # 仅实况点
  "forecasts": [ForecastBatch]            # 多源预测批次
}

进程间通信通过这些文件完成，无锁但语义安全（每个文件单一写者）。
"""

import json
import os
import tempfile
from pathlib import Path

from . import config
from .models import (
    ForecastBatch,
    StormDetail,
    StormSummary,
    TrackHistoryEntry,
)
from .utils import now_utc_iso, setup_logger

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
    """原子写入 watchlist.json。"""
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


# ── track + forecasts ──────────────────────────────────────────────────
def save_storm_detail(
    detail: StormDetail,
    cma_tfid: str | None = None,
) -> tuple[int, int]:
    """统一持久化入口：追加实况 + 替换预测批 + 刷新 info/last_updated。

    Args:
        detail: 已合并的多源详情（可能含 zoom 实况 + 多源预测）
        cma_tfid: 若指定，刷新到 info.cma_tfid；None 表示不主动改写（保留已有值）

    Returns:
        (新增实况点数, 新增/更新预测批数)
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
    existing_forecasts: list[ForecastBatch] = existing.get("forecasts", [])

    new_track_count = _append_track_history(detail, history)
    new_batch_count = _merge_forecasts(detail["forecasts"], existing_forecasts)

    # info：保留已有 cma_tfid，若本次显式传入则覆盖
    info = existing.get("info") or {}
    info.update({
        "name": detail["name"],
        "title": detail["title"],
        "type": detail["type"],
        "active": detail["active"],
        "season": detail["season"],
        "agencies": detail["agencies"],
    })
    # 中文名：detail 提供 非""则覆盖；为空则保留已有（避免被空覆盖）
    if detail.get("name_cn"):
        info["name_cn"] = detail["name_cn"]
    elif "name_cn" not in info:
        info["name_cn"] = ""
    if cma_tfid is not None:
        info["cma_tfid"] = cma_tfid
    elif "cma_tfid" not in info:
        info["cma_tfid"] = None

    payload = {
        "id": storm_id,
        "info": info,
        "last_updated": now_utc_iso(),
        "track_history": history,
        "forecasts": existing_forecasts,
    }
    _atomic_write_json(path, payload)
    return new_track_count, new_batch_count


def _append_track_history(detail: StormDetail, history: list[TrackHistoryEntry]) -> int:
    """把 detail.track 中尚未记录的点追加到 history。

    去重 key = (date, source)，同时刻同源不重复；不同源同时刻并存。
    """
    seen: set[tuple[str, str]] = {(p["date"], p.get("source", "")) for p in history}
    new_count = 0
    for point in detail["track"]:
        key = (point["date"], point.get("source", ""))
        if key in seen:
            continue
        history.append({**point, "first_seen": detail["fetched_at"]})  # type: ignore[typeddict-item]
        seen.add(key)
        new_count += 1

    history.sort(key=lambda p: p["date"])
    return new_count


def _merge_forecasts(
    incoming: list[ForecastBatch],
    existing: list[ForecastBatch],
) -> int:
    """合并预测批：按 (source, issued_at) 整批替换；每源保留最近 N 批。

    Returns:
        本次新增/更新的批次数（含同 key 覆盖的）。
    """
    keep_n = config.FORECAST_BATCHES_KEEP

    # 用 (source, issued_at) 作为索引；同 key 直接覆盖
    index: dict[tuple[str, str], ForecastBatch] = {
        (b["source"], b["issued_at"]): b for b in existing
    }
    changed = 0
    for batch in incoming:
        if not batch["points"]:
            continue
        key = (batch["source"], batch["issued_at"])
        is_new = key not in index
        index[key] = batch
        if is_new:
            changed += 1
        else:
            # 覆盖（点集可能变化）也算一次更新
            changed += 1

    # 按 source 分组，每组按 issued_at 倒序取前 keep_n
    by_source: dict[str, list[ForecastBatch]] = {}
    for (source, _), batch in index.items():
        by_source.setdefault(source, []).append(batch)

    existing.clear()
    for source, batches in by_source.items():
        batches.sort(key=lambda b: b["issued_at"], reverse=True)
        existing.extend(batches[:keep_n])

    # 按时间稳定排序输出（便于人眼读 JSON）
    existing.sort(key=lambda b: (b["source"], b["issued_at"]))
    return changed


def load_storm_track(storm_id: str) -> dict | None:
    """读取某个台风的完整路径历史与预测。文件不存在时返回 None。"""
    path = config.track_file_for_storm(storm_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"读取 {path.name} 失败: {e}")
        return None


def list_watched_tracks() -> list[dict]:
    """读取所有关注台风的完整路径历史与预测。"""
    watched = load_watchlist()
    results: list[dict] = []
    for storm_id in sorted(watched):
        track = load_storm_track(storm_id)
        if track is not None:
            results.append(track)
    return results


def get_storm_cma_tfid(storm_id: str) -> str | None:
    """读取已持久化的 info.cma_tfid（若存在）。供 aggregator 复用避免重匹配。"""
    track = load_storm_track(storm_id)
    if track is None:
        return None
    info = track.get("info") or {}
    return info.get("cma_tfid")


# ── history（已消亡台风归档，仅留实况） ────────────────────────────────
def history_file_for_storm(storm_id: str) -> Path:
    """根据台风 ID 生成对应的归档 JSON 文件路径。"""
    safe = storm_id.replace("/", "_")
    return config.HISTORY_DIR / f"{safe}.json"


def archive_storm(storm_id: str) -> bool:
    """把 tracks/{id}.json 归档到 history/{id}.json（仅保留实况），并从 watchlist 移除。

    归档文件结构：
        {id, info, archived_at, track_history}
    forecasts 被丢弃（用户只关心历史真实路径）。

    Returns:
        True 表示归档成功；False 表示 tracks 文件不存在。
    """
    src = config.track_file_for_storm(storm_id)
    if not src.exists():
        logger.warning(f"归档失败：tracks 文件不存在 {src.name}")
        return False

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"读取 {src.name} 失败，归档中止: {e}")
        return False

    payload = {
        "id": storm_id,
        "info": data.get("info") or {},
        "archived_at": now_utc_iso(),
        "track_history": data.get("track_history") or [],
    }
    dst = history_file_for_storm(storm_id)
    _atomic_write_json(dst, payload)

    try:
        src.unlink()
    except OSError as e:
        logger.warning(f"删除 tracks 文件 {src.name} 失败（归档已写入）: {e}")

    remove_from_watchlist(storm_id)
    logger.info(f"已归档 {storm_id} → {dst.name}（实况 {len(payload['track_history'])} 点）")
    return True


def load_history_storm(storm_id: str) -> dict | None:
    """读取某个台风的归档。文件不存在返回 None。"""
    path = history_file_for_storm(storm_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"读取归档 {path.name} 失败: {e}")
        return None


def list_history() -> list[dict]:
    """列出所有归档台风（按 id 升序）。"""
    results: list[dict] = []
    for path in sorted(config.HISTORY_DIR.glob("*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            logger.warning(f"读取归档 {path.name} 失败: {e}")
    return results


# ── 兼容别名 ────────────────────────────────────────────────────────────
def append_storm_track(detail: StormDetail) -> int:
    """旧 API 兼容：仅返回新增实况点数。新代码请用 save_storm_detail。"""
    new_tracks, _ = save_storm_detail(detail)
    return new_tracks


# ── helpers ────────────────────────────────────────────────────────────
def _atomic_write_json(path: Path, payload: dict, mode: int = 0o644) -> None:
    """原子写入 JSON：先写到临时文件再 rename。

    Args:
        mode: 目标文件权限模式。默认 0o644（other 可读，宿主机 nginx 等可消费）。
            POSIX 上 tempfile.mkstemp 默认 0o600 会导致非 root 进程读不到。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
