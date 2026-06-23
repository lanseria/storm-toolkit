"""定时同步调度模块。

循环执行：
1. 合并 zoom + cma 活跃列表 → save_active_storms()
2. 读 watchlist，对每个 id 调 aggregator.fetch_combined_detail() 合并多源详情
3. save_storm_detail() 持久化实况 + 多源预测批

单个抓取失败不退出循环，只 log error 后继续下一个周期。
"""

import time
from datetime import datetime, timezone

from . import aggregator, config, matcher
from .providers.zoom_earth import ZoomEarthProvider
from .providers.zj_cma import ZJCmaProvider
from .storage import (
    archive_storm,
    load_watchlist,
    save_active_storms,
    save_storm_detail,
)
from .utils import setup_logger, utc_to_beijing

logger = setup_logger("storm_toolkit.scheduler")


def run_once() -> None:
    """执行一轮完整同步：双源活跃列表 + 关注台风多源详情。"""
    started_at = datetime.now(timezone.utc)
    logger.info(
        f"===== 同步开始（{utc_to_beijing(started_at).strftime('%Y-%m-%d %H:%M:%S')} BJT）====="
    )

    # ── 1. 活跃列表（zoom + cma 合并） ─────────────────────────────
    zoom_active = ZoomEarthProvider().fetch_active()
    cma_raw: list[dict] = []
    if config.CMA_ENABLED:
        try:
            cma_raw = ZJCmaProvider().fetch_active_raw()
        except Exception as e:
            logger.warning(f"[scheduler] CMA 活跃列表抓取异常（已跳过）: {e}")

    summaries = matcher.merge_active(zoom_active, cma_raw)
    save_active_storms(summaries)

    # ── 2. 关注台风详情（多源合并） ───────────────────────────────
    watched = load_watchlist()
    if not watched:
        logger.info("当前无关注台风，跳过详情抓取。")
        return

    total_new_tracks = 0
    total_new_batches = 0
    archived: list[str] = []
    for storm_id in sorted(watched):
        try:
            detail, cma_tfid = aggregator.fetch_combined_detail(storm_id)
        except Exception as e:
            logger.error(f"[{storm_id}] 抓取异常: {e}", exc_info=True)
            continue
        if detail is None:
            logger.warning(f"[{storm_id}] 两源均失败，跳过（不归档，等下轮重试）")
            continue

        # 台风已消亡：归档到 history（仅留实况），并从 watchlist 移除
        if not detail.get("active", True) and detail.get("id") == storm_id:
            logger.info(f"[{storm_id}] 已消亡（active=False），开始归档")
            # 先最后一次 save 以确保最新实况落盘
            try:
                save_storm_detail(detail, cma_tfid=cma_tfid)
            except Exception as e:
                logger.warning(f"[{storm_id}] 归档前最后一次 save 失败: {e}")
            if archive_storm(storm_id):
                archived.append(storm_id)
            continue

        new_tracks, new_batches = save_storm_detail(detail, cma_tfid=cma_tfid)
        total_new_tracks += new_tracks
        total_new_batches += new_batches
        logger.info(
            f"  [{storm_id}] 新增实况 {new_tracks} 点，预测 {new_batches} 批"
            + (f"（cma_tfid={cma_tfid}）" if cma_tfid else "")
        )

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    archive_note = f"，归档 {len(archived)} 个（{','.join(archived)}）" if archived else ""
    logger.info(
        f"===== 同步完成：关注 {len(watched)} 个，"
        f"新增实况 {total_new_tracks} 点 + 预测 {total_new_batches} 批"
        f"{archive_note}，耗时 {elapsed:.1f}s ====="
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
