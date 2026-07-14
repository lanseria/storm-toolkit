"""FastAPI Web 应用。

提供 REST API 与静态前端，让用户选择关注台风、查看路径与多源预测。
关注列表通过 data/watchlist.json 与 schedule 进程共享。
"""

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import aggregator, config, imagery
from ..storage import (
    add_to_watchlist,
    list_history,
    load_active_storms,
    load_history_storm,
    load_storm_track,
    load_watchlist,
    list_watched_tracks,
    purge_runtime_data,
    remove_from_watchlist,
    save_storm_detail,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Storm Toolkit", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """返回前端首页。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/storms/active")
def api_active_storms() -> JSONResponse:
    """返回最近一次抓取的活跃台风列表（schedule 进程写入的缓存）。"""
    data = load_active_storms()
    watched = load_watchlist()
    for s in data.get("storms", []):
        s["watched"] = s.get("id") in watched
    return JSONResponse(data)


@app.get("/api/storms/{storm_id}")
def api_storm_detail(storm_id: str) -> JSONResponse:
    """实时拉取并返回某个台风的最新多源详情（不持久化）。"""
    detail, _ = aggregator.fetch_combined_detail(storm_id)
    if detail is None:
        raise HTTPException(status_code=502, detail=f"无法获取台风 {storm_id} 的详情")
    return JSONResponse(detail)


@app.get("/api/watchlist")
def api_watchlist() -> JSONResponse:
    """返回所有已关注台风的完整路径历史与多源预测。"""
    tracks = list_watched_tracks()
    return JSONResponse({"watchlist": sorted(load_watchlist()), "tracks": tracks})


@app.post("/api/watchlist/{storm_id}")
def api_watch(storm_id: str) -> JSONResponse:
    """加入关注列表，并立即抓取一次多源详情落盘。"""
    add_to_watchlist(storm_id)
    detail, cma_tfid = aggregator.fetch_combined_detail(storm_id)
    new_tracks = new_batches = 0
    if detail is not None:
        new_tracks, new_batches = save_storm_detail(detail, cma_tfid=cma_tfid)
    return JSONResponse({
        "ok": True,
        "id": storm_id,
        "new_points": new_tracks,
        "new_batches": new_batches,
        "cma_tfid": cma_tfid,
    })


@app.delete("/api/watchlist/{storm_id}")
def api_unwatch(storm_id: str) -> JSONResponse:
    """从关注列表移除（不删除已有路径历史）。"""
    remove_from_watchlist(storm_id)
    return JSONResponse({"ok": True, "id": storm_id})


@app.get("/api/tracks/{storm_id}")
def api_track(storm_id: str) -> JSONResponse:
    """返回某个台风的持久化路径历史与多源预测。"""
    track = load_storm_track(storm_id)
    if track is None:
        raise HTTPException(status_code=404, detail=f"未找到台风 {storm_id} 的路径记录")
    return JSONResponse(track)


@app.get("/api/history")
def api_history() -> JSONResponse:
    """返回所有已归档（消亡）台风，仅含真实路径，不含预测。"""
    items = list_history()
    summaries = [
        {
            "id": h.get("id", ""),
            "info": h.get("info") or {},
            "archived_at": h.get("archived_at", ""),
            "track_count": len(h.get("track_history") or []),
        }
        for h in items
    ]
    return JSONResponse({"history": summaries})


@app.get("/api/history/{storm_id}")
def api_history_detail(storm_id: str) -> JSONResponse:
    """返回某个已归档台风的完整实况路径。"""
    h = load_history_storm(storm_id)
    if h is None:
        raise HTTPException(status_code=404, detail=f"未找到归档 {storm_id}")
    return JSONResponse(h)


# ── 卫星图生成 ──────────────────────────────────────────────────────────
def _satellite_zip_path(storm_id: str, size_sig: str) -> Path:
    """卫星图 zip 路径，文件名含尺寸签名以支持不同规格独立缓存。"""
    safe = storm_id.replace("/", "_")
    return config.SATELLITE_DIR / f"{safe}_{size_sig}.zip"


def _load_track_for_satellite(storm_id: str, source: str) -> dict | None:
    """按 source 加载台风数据。auto 时优先 track，其次 history。"""
    if source == "track":
        return load_storm_track(storm_id)
    if source == "history":
        return load_history_storm(storm_id)
    # auto
    return load_storm_track(storm_id) or load_history_storm(storm_id)


def _build_gen_config(
    width: int, height: int, show_boundaries: bool, show_cities: bool,
    city_font_scale: float,
) -> imagery.GenConfig:
    """根据请求参数构造 GenConfig，含边界校验。"""
    w = max(256, min(4096, int(width)))
    h = max(256, min(4096, int(height)))
    scale = max(0.3, min(3.0, float(city_font_scale)))
    return imagery.GenConfig(
        width=w, height=h,
        show_boundaries=show_boundaries,
        show_cities=show_cities,
        city_font_scale=scale,
    )


@app.post("/api/satellite/{storm_id}")
def api_satellite_generate(
    storm_id: str,
    source: str = Query("auto", pattern="^(auto|track|history)$"),
    width: int = Query(config.SATELLITE_IMAGE_SIZE, ge=256, le=4096),
    height: int = Query(config.SATELLITE_IMAGE_SIZE, ge=256, le=4096),
    show_boundaries: bool = Query(True),
    show_cities: bool = Query(True),
    city_font_scale: float = Query(1.0, ge=0.3, le=3.0),
) -> JSONResponse:
    """启动卫星图生成任务。

    - source=auto: 优先 tracks，其次 history
    - 归档台风且对应尺寸的 zip 已存在 → 直接返回 cached
    - 进行中台风 → 每次都重新生成（覆盖旧 zip）
    """
    track_data = _load_track_for_satellite(storm_id, source)
    if track_data is None:
        raise HTTPException(status_code=404, detail=f"未找到台风 {storm_id} 的数据")

    gc = _build_gen_config(width, height, show_boundaries, show_cities, city_font_scale)

    is_history = source == "history" or (
        source == "auto" and load_storm_track(storm_id) is None
    )
    zip_path = _satellite_zip_path(storm_id, gc.size_sig)

    # 归档台风：对应尺寸的 zip 已存在则命中缓存
    if is_history and zip_path.exists():
        return JSONResponse({
            "ok": True,
            "cached": True,
            "id": storm_id,
            "size_sig": gc.size_sig,
            "zip_url": f"/api/satellite/{storm_id}.zip?size_sig={gc.size_sig}",
        })

    # 启动后台任务
    task_id = uuid.uuid4().hex
    thread = threading.Thread(
        target=imagery.run_generation_task,
        args=(task_id, storm_id, track_data, zip_path, gc),
        daemon=True,
    )
    thread.start()
    return JSONResponse({
        "ok": True,
        "cached": False,
        "id": storm_id,
        "size_sig": gc.size_sig,
        "task_id": task_id,
        "status": "running",
    })


@app.get("/api/satellite/tasks/{task_id}")
def api_satellite_task(task_id: str) -> JSONResponse:
    """轮询卫星图生成任务进度。"""
    state = imagery.get_task(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"未找到任务 {task_id}")
    progress = 0.0
    if state.total > 0:
        progress = round(state.current / state.total, 3)
    # 任务记录其输出 zip 的尺寸签名（在 run_generation_task 里设置）
    size_sig = getattr(state, "size_sig", "1080x1080")
    return JSONResponse({
        "task_id": task_id,
        "storm_id": state.storm_id,
        "size_sig": size_sig,
        "status": state.status,
        "current": state.current,
        "total": state.total,
        "progress": progress,
        "error": state.error,
        "zip_url": (
            f"/api/satellite/{state.storm_id}.zip?size_sig={size_sig}"
            if state.status == "done" else None
        ),
    })


@app.get("/api/satellite/{storm_id}.zip")
def api_satellite_download(
    storm_id: str,
    size_sig: str = Query(..., pattern=r"^\d+x\d+$"),
) -> FileResponse:
    """下载已生成的卫星图 zip。size_sig 形如 1080x1080。"""
    zip_path = _satellite_zip_path(storm_id, size_sig)
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"卫星图尚未生成 {storm_id} ({size_sig})")
    safe = storm_id.replace("/", "_")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{safe}_satellite_{size_sig}.zip",
    )


@app.delete("/api/satellite/cache")
def api_satellite_clear_cache() -> JSONResponse:
    """清除所有已生成的卫星图 zip 缓存（data/satellite/*.zip）。"""
    removed = 0
    for path in config.SATELLITE_DIR.glob("*.zip"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return JSONResponse({"ok": True, "removed": removed})


@app.delete("/api/data")
def api_purge_data() -> JSONResponse:
    """清空运行时数据：storms_active.json + tracks/*.json。

    保留 watchlist（关注的 ID 集合，下次 schedule 周期会重新抓取）与 history 归档。
    """
    result = purge_runtime_data()
    return JSONResponse({"ok": True, **result})


def run(host: str | None = None, port: int | None = None) -> None:
    """启动 uvicorn 服务。"""
    import uvicorn

    uvicorn.run(
        app,
        host=host or config.WEB_HOST,
        port=port or config.WEB_PORT,
        log_level="info",
    )
