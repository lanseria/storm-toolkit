"""FastAPI Web 应用。

提供 REST API 与静态前端，让用户选择关注台风、查看路径与多源预测。
关注列表通过 data/watchlist.json 与 schedule 进程共享。
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import aggregator, config
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
