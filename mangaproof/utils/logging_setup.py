"""开发日志：写入程序目录 logs/ 下，供错误排查使用。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_logger_configured = False


class _CurrentStderrProxy:
    """动态代理当前 sys.stderr。

    打包产物在 Windows 上会 FreeConsole/AllocConsole 切换控制台，
    sys.stderr 会被重定向（devnull ↔ CONOUT$）。若 StreamHandler
    抓住切换前的旧句柄，写日志会报 OSError WinError 6。
    本代理每次写入都转发到「当前」的 sys.stderr。
    """

    def write(self, s: str):
        return sys.stderr.write(s)

    def flush(self):
        return sys.stderr.flush()

    def fileno(self):
        try:
            return sys.stderr.fileno()
        except Exception:
            return -1


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

    # 控制台流走代理：始终写入“当前”的 sys.stderr
    ch = logging.StreamHandler(_CurrentStderrProxy())
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    _logger_configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mangaproof.{name}")
