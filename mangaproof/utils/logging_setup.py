"""开发日志：写入程序目录 logs/ 下，供错误排查使用。

轮转策略（防止日志无限叠加、浪费磁盘与 IO）：
- 大小轮转：mangaproof.log 超过 _LOG_MAX_BYTES 即滚动为
  mangaproof.log.1，只保留最近 _LOG_BACKUP_COUNT 份，最旧的被
  覆盖删除；
- 启动清理：logs/ 中编号超出保留份数的历史轮转文件（如旧版本遗留）
  在启动时统一清除。

磁盘占用上界恒为 (_LOG_BACKUP_COUNT + 1) × _LOG_MAX_BYTES。
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

_logger_configured = False

_LOG_MAX_BYTES = 2 * 1024 * 1024   # 单文件滚动阈值：2 MB
_LOG_BACKUP_COUNT = 3              # 轮转备份保留份数（磁盘上界约 8 MB）
_LOG_BASE_NAME = "mangaproof.log"

_LOG_BACKUP_RE = re.compile(rf"{re.escape(_LOG_BASE_NAME)}\.(\d+)\Z")


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


def _sweep_stale_rotations(log_dir: Path) -> None:
    """清除编号超出保留份数的历史轮转文件（启动时兜底）。

    RotatingFileHandler 只在滚动发生的瞬间删除最旧备份；若保留份数
    曾调小、或旧版本遗留了更多备份文件，它们会一直躺在磁盘上。
    启动时按同一上界清理，保证 logs/ 占用不随时间无限叠加。
    仅匹配 "mangaproof.log.<数字>"，其他文件一概不动；清理失败静默
    （日志文件不应成为启动失败的原因）。
    """
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        m = _LOG_BACKUP_RE.fullmatch(entry.name)
        if m is not None and int(m.group(1)) > _LOG_BACKUP_COUNT:
            try:
                entry.unlink()
            except OSError:
                pass


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
        # 先清理越界的历史轮转文件，再挂滚动文件 handler
        _sweep_stale_rotations(log_dir)
        try:
            fh = logging.handlers.RotatingFileHandler(
                log_dir / _LOG_BASE_NAME,
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
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
