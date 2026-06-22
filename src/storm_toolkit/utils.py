"""通用工具函数。"""

import logging
import sys
from datetime import datetime, timezone, timedelta

# Windows 控制台默认 GBK，强制 UTF-8 以正确显示中文日志
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建带格式的 logger。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


BEIJING_TZ = timezone(timedelta(hours=8))
UTC = timezone.utc


def utc_to_beijing(dt: datetime) -> datetime:
    """UTC 转北京时间。"""
    return dt.astimezone(BEIJING_TZ)


def beijing_to_utc(dt: datetime) -> datetime:
    """北京时间转 UTC。"""
    return dt.astimezone(UTC)


def truncate_to_six_hours(dt: datetime) -> datetime:
    """将 UTC 时间截断到最近的 6 小时整点（zoom.earth 列表 API 要求）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.replace(minute=0, second=0, microsecond=0, hour=dt.hour - dt.hour % 6)


def format_zoom_date(dt: datetime) -> str:
    """格式化为 zoom.earth 接受的日期参数：YYYY-MM-DDTHH:MMZ。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def now_utc_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（带 Z 后缀）。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
