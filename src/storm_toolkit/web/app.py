"""FastAPI Web 应用。

提供 REST API 与静态前端，让用户选择关注台风、查看路径。
关注列表通过 data/watchlist.json 与 schedule 进程共享。
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..data_acquisition import fetch_storm_detail
from ..storage import (
    add_to_watchlist,
    load_active_storms,
    load_storm_track,
    load_watchlist,
    list_watched_tracks,
    remove_from_watchlist,
    append_storm_track,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Storm Toolkit", version="0.1.0")
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
    """实时拉取并返回某个台风的最新详情（不持久化）。"""
    detail = fetch_storm_detail(storm_id)
    if detail is None:
        raise HTTPException(status_code=502, detail=f"无法获取台风 {storm_id} 的详情")
    return JSONResponse(detail)


@app.get("/api/watchlist")
def api_watchlist() -> JSONResponse:
    """返回所有已关注台风的完整路径历史。"""
    tracks = list_watched_tracks()
    return JSONResponse({"watchlist": sorted(load_watchlist()), "tracks": tracks})


@app.post("/api/watchlist/{storm_id}")
def api_watch(storm_id: str) -> JSONResponse:
    """加入关注列表，并立即抓取一次详情落盘。"""
    add_to_watchlist(storm_id)
    detail = fetch_storm_detail(storm_id)
    new_count = 0
    if detail is not None:
        new_count = append_storm_track(detail)
    return JSONResponse({"ok": True, "id": storm_id, "new_points": new_count})


@app.delete("/api/watchlist/{storm_id}")
def api_unwatch(storm_id: str) -> JSONResponse:
    """从关注列表移除（不删除已有路径历史）。"""
    remove_from_watchlist(storm_id)
    return JSONResponse({"ok": True, "id": storm_id})


@app.get("/api/tracks/{storm_id}")
def api_track(storm_id: str) -> JSONResponse:
    """返回某个台风的持久化路径历史。"""
    track = load_storm_track(storm_id)
    if track is None:
        raise HTTPException(status_code=404, detail=f"未找到台风 {storm_id} 的路径记录")
    return JSONResponse(track)


def run(host: str | None = None, port: int | None = None) -> None:
    """启动 uvicorn 服务。"""
    import uvicorn

    uvicorn.run(
        app,
        host=host or config.WEB_HOST,
        port=port or config.WEB_PORT,
        log_level="info",
    )
