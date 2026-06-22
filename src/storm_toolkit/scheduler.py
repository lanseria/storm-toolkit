"""定时同步调度模块。

循环执行：
1. fetch_active_storms() → save_active_storms()
2. 读 watchlist，对每个 id 调 fetch_storm_detail()
3. append_storm_track() 去重追加

单个抓取失败不退出循环，只 log error 后继续下一个周期。
"""

import time
from datetime import datetime, timezone

from . import config
from .data_acquisition import fetch_active_storms, fetch_storm_detail
from .storage import append_storm_track, load_watchlist, save_active_storms
from .utils import setup_logger, utc_to_beijing

logger = setup_logger("storm_toolkit.scheduler")


def run_once() -> None:
    """执行一轮完整同步：活跃列表 + 关注台风路径。"""
    started_at = datetime.now(timezone.utc)
    logger.info(
        f"===== 同步开始（{utc_to_beijing(started_at).strftime('%Y-%m-%d %H:%M:%S')} BJT）====="
    )

    summaries = fetch_active_storms()
    save_active_storms(summaries)

    watched = load_watchlist()
    if not watched:
        logger.info("当前无关注台风，跳过详情抓取。")
        return

    total_new = 0
    for storm_id in sorted(watched):
        detail = fetch_storm_detail(storm_id)
        if detail is None:
            continue
        new_count = append_storm_track(detail)
        total_new += new_count
        logger.info(f"  [{storm_id}] 新增 {new_count} 个路径点")

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        f"===== 同步完成：关注 {len(watched)} 个，新增路径点 {total_new} 个，"
        f"耗时 {elapsed:.1f}s ====="
    )


def run_scheduled() -> None:
    """定时调度主循环。每 SCHEDULE_INTERVAL_SECONDS 秒执行一轮。Ctrl+C 退出。"""
    interval = config.SCHEDULE_INTERVAL_SECONDS
    logger.info(
        f"定时调度启动，间隔 {interval} 秒（{interval // 60} 分钟）。按 Ctrl+C 停止。"
    )
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            logger.info("收到退出信号，调度停止。")
            raise
        except Exception as e:
            logger.error(f"同步周期异常（将重试）: {e}", exc_info=True)

        logger.info(f"休眠 {interval} 秒后进入下一轮...")
        time.sleep(interval)
