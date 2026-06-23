"""Storm Toolkit CLI 入口。"""

import argparse
import sys

from . import aggregator, config
from .data_acquisition import fetch_active_storms
from .scheduler import run_once, run_scheduled
from .storage import (
    load_watchlist,
    save_active_storms,
    save_storm_detail,
)
from .utils import setup_logger

logger = setup_logger("storm_toolkit.main")


def run_list() -> None:
    """打印当前活跃台风。"""
    summaries = fetch_active_storms()
    if not summaries:
        logger.warning("未获取到任何活跃台风。")
        return
    watched = load_watchlist()
    print(f"\n当前活跃 {len(summaries)} 个：")
    for s in summaries:
        flag = "[已关注]" if s["id"] in watched else "[未关注]"
        sources = ",".join(s.get("sources", [])) or "-"
        print(f"  {flag} {s['id']:24s} ({s['kind']}, src={sources})")
    print()


def run_acquire() -> None:
    """一次性执行一轮完整同步（双源活跃列表 + 多源关注详情）。"""
    run_once()


def run_reset_tracks() -> None:
    """清空 data/tracks/*.json（保留 watchlist 与活跃列表缓存）。"""
    removed = 0
    for f in config.TRACKS_DIR.glob("*.json"):
        try:
            f.unlink()
            removed += 1
        except OSError as e:
            logger.warning(f"删除 {f.name} 失败: {e}")
    logger.info(f"已清空 {removed} 个 tracks 文件，下一轮调度将自动重抓。")


def run_web(port: int | None = None) -> None:
    """启动 Web 服务。"""
    from .web.app import run as run_app
    run_app(port=port)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Storm Toolkit - 多源台风追踪工具（zoom.earth + 浙江水利厅）",
    )
    parser.add_argument(
        "--web", action="store_true", help="启动 Web 服务（默认行为）",
    )
    parser.add_argument(
        "--schedule", action="store_true",
        help="定时调度模式：每 N 秒同步活跃列表与关注台风路径",
    )
    parser.add_argument(
        "--acquire", action="store_true",
        help="一次性抓取活跃列表 + 关注台风多源详情",
    )
    parser.add_argument(
        "--list", action="store_true", help="仅打印当前活跃台风",
    )
    parser.add_argument(
        "--reset-tracks", action="store_true",
        help="清空 data/tracks/*.json（保留 watchlist 与活跃列表缓存）",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"Web 服务端口（默认 {config.WEB_PORT}）",
    )

    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    elif args.acquire:
        run_acquire()
    elif args.list:
        run_list()
    elif args.reset_tracks:
        run_reset_tracks()
    else:
        run_web(args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("中断。")
        sys.exit(0)
