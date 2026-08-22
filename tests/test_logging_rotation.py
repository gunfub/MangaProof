"""日志轮转回归测试：磁盘占用有上界、最旧备份被覆盖删除、启动清理越界文件。

覆盖：
- 小记录持续写入不触发轮转；
- 超过阈值触发滚动，且备份链最多保留 backupCount 份（最旧被删除）；
- logs/ 总占用不超过 (backupCount + 1) × maxBytes；
- setup_logging 启动时清除编号越界的历史轮转文件，且不误删其他文件。

运行：uv run python -m pytest tests/test_logging_rotation.py -v
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mangaproof.utils import logging_setup as ls

MAX_BYTES = ls._LOG_MAX_BYTES
BACKUP_COUNT = ls._LOG_BACKUP_COUNT
BASE = ls._LOG_BASE_NAME


def _make_record(msg: str, seq: int) -> logging.LogRecord:
    return logging.LogRecord(
        "mangaproof.test", logging.INFO, __file__, seq, msg, (), None
    )


@pytest.fixture
def configured_logging(tmp_path):
    """在临时目录配置日志，用后还原全局日志状态。

    setup_logging 有模块级一次性开关（_logger_configured），直接调用
    会把 handler 挂到全局 "mangaproof" logger 上，影响同进程内的其他
    测试；这里记录旧状态，结束后关闭并移除新增 handler、还原级别。
    """
    logger = logging.getLogger("mangaproof")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_configured = ls._logger_configured
    ls._logger_configured = False
    try:
        ls.setup_logging(tmp_path)
        new_handlers = [h for h in logger.handlers if h not in old_handlers]
        fh = next(
            h for h in new_handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        yield tmp_path / "logs", fh
    finally:
        for h in logger.handlers:
            if h not in old_handlers:
                h.close()
                logger.removeHandler(h)
        logger.setLevel(old_level)
        ls._logger_configured = old_configured


def test_small_writes_do_not_rotate(configured_logging):
    log_dir, fh = configured_logging
    for i in range(100):
        fh.emit(_make_record("y" * 1024, i))
    assert not (log_dir / f"{BASE}.1").exists()
    assert (log_dir / BASE).stat().st_size < MAX_BYTES


def test_rotation_caps_backups_and_drops_oldest(configured_logging):
    log_dir, fh = configured_logging

    # 每条记录都超过阈值：每次写入前都触发一次滚动
    big = "x" * (MAX_BYTES + 4096)
    for i in range(5):
        fh.emit(_make_record(big, 100 + i))

    names = sorted(p.name for p in log_dir.glob(f"{BASE}*"))
    assert names == [BASE, f"{BASE}.1", f"{BASE}.2", f"{BASE}.3"], names
    # 最旧的备份被覆盖删除，绝不出现超出保留份数的编号
    assert not (log_dir / f"{BASE}.4").exists()

    # 磁盘占用上界：(backupCount + 1) × maxBytes（含少量写穿余量）
    total = sum(p.stat().st_size for p in log_dir.glob(f"{BASE}*"))
    assert total <= MAX_BYTES * (BACKUP_COUNT + 1) + 64 * 1024, total


def test_sweep_removes_only_out_of_range_backups(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    keep = [BASE, f"{BASE}.1", f"{BASE}.2", f"{BASE}.3", f"{BASE}.0"]
    stale = [f"{BASE}.4", f"{BASE}.5", f"{BASE}.12"]
    decoys = [
        f"{BASE}.bak-20260822",   # 非纯数字后缀：不动
        f"{BASE}.x",              # 非纯数字后缀：不动
        "other.log.1",            # 其他日志：不动
    ]
    for name in keep + stale + decoys:
        (log_dir / name).write_text("junk", encoding="utf-8")

    ls._sweep_stale_rotations(log_dir)

    remaining = sorted(p.name for p in log_dir.iterdir())
    assert remaining == sorted(keep + decoys), remaining


def test_setup_logging_triggers_sweep(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / f"{BASE}.9").write_text("old", encoding="utf-8")

    logger = logging.getLogger("mangaproof")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    monkeypatch.setattr(ls, "_logger_configured", False)
    try:
        ls.setup_logging(tmp_path)
    finally:
        for h in logger.handlers:
            if h not in old_handlers:
                h.close()
                logger.removeHandler(h)
        logger.setLevel(old_level)

    assert not (log_dir / f"{BASE}.9").exists()
    assert (log_dir / BASE).exists()
