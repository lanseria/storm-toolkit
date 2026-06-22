"""Storm Toolkit CLI 入口。"""

import argparse
import sys

from . import config
from .data_acquisition import fetch_active_storms, fetch_storm_detail
from .scheduler import run_once, run_scheduled
from .storage import append_storm_track, load_watchlist, save_active_storms
from .utils import setup_logger, utc_to_beijing

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
        print(f"  {flag} {s['id']:24s} ({s['kind']})")
    print()


def run_acquire() -> None:
    """一次性抓取活跃列表 + 所有关注台风的最新详情。"""
    summaries = fetch_active_storms()
    save_active_storms(summaries)

    watched = load_watchlist()
    if not watched:
        logger.info("当前无关注台风，仅刷新活跃列表。")
        return

    total_new = 0
    for storm_id in sorted(watched):
        detail = fetch_storm_detail(storm_id)
        if detail is None:
            continue
        new_count = append_storm_track(detail)
        total_new += new_count
        logger.info(f"  [{storm_id}] 新增 {new_count} 个路径点")
    logger.info(f"完成：关注 {len(watched)} 个，新增路径点 {total_new} 个。")


def run_web(port: int | None = None) -> None:
    """启动 Web 服务。"""
    from .web.app import run as run_app
    run_app(port=port)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Storm Toolkit - 基于 zoom.earth 的台风追踪工具",
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
        help="一次性抓取活跃列表 + 关注台风详情",
    )
    parser.add_argument(
        "--list", action="store_true", help="仅打印当前活跃台风",
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
    else:
        run_web(args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("中断。")
        sys.exit(0)
