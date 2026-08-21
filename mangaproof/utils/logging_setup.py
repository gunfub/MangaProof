"""开发日志：写入程序目录 logs/ 下，供错误排查使用。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_logger_configured = False


def setup_logging(app_dir: Path) -> logging.Logger:
    """配置根 logger：控制台 + 滚动文件（logs/mangaproof.log）。

    可重复调用，只会配置一次。
    """
    global _logger_configured
    logger = logging.getLogger("mangaproof")
    if _logger_configured:
        return logger

    log_dir = app_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = None

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_dir is not None:
        try:
            fh = logging.handlers.RotatingFileHandler(
                log_dir / "mangaproof.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    _logger_configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mangaproof.{name}")
