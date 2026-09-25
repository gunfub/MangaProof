# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""升级缓存清理（**主程序侧**，需求 §36/§37 的两个临时目录）。

清理对象只有两个目录（路径唯一来源是 :mod:`mangaproof.update.platform_dirs`，
绝不在这里拼字符串）：

======================  ==========================================================
``update-package``      下载的更新包、升级时的临时数据备份、成功标记、安装器状态
``update-installer``    从程序目录复制出来的安装器副本（需求 §40）
======================  ==========================================================

**为什么放在主程序而不是安装器里**（需求方 2026-09-24 决策）：

- 更新成功后安装器本来就会把这两个目录整个删掉（需求 §61），所以它们是"可丢弃的"；
  真正需要手动清理的场景是**升级失败**——目录里会留下下载了一半的包、上一次的安装器
  副本和日志，而这时安装器已经退出、不会再回来收尾；
- Windows 上「删不掉自己」只约束**正在运行的安装器 exe 自己**；主程序删的是别人的
  文件，三个平台都能删干净（被占用/无权限时如实报告失败，不假装成功）；
- 主程序在升级流程之外运行，删除不会和正在进行的安装互相踩。

安全防护（删目录一律先确认再动手）：

1. 只认 :data:`~mangaproof.update.platform_dirs.PACKAGE_DIRNAME` /
   ``INSTALLER_DIRNAME`` 两个固定目录名，且必须**正好位于**
   :func:`~mangaproof.update.platform_dirs.temp_root` 之下 —— 名字或位置不符就
   拒绝删除（只记录，不动任何文件）；
2. 目录不存在 → 跳过（不算失败）；
3. 软链接 → 跳过，绝不跟随链接去删别处的文件；
4. 删除失败（权限/占用）→ 如实记录，**不影响另一个目录**，也不抛异常；
5. 删除或跳过之后都把目录**建回来**，后续下载/复制安装器无需再判断存在性。

本模块**零 Qt 依赖**（纯标准库 + 同层的 ``platform_dirs`` / ``humanize``），
因此可以脱离界面直接单测。
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mangaproof.update import platform_dirs
from mangaproof.update.humanize import human_size

log = logging.getLogger("mangaproof.update.cache")

#: 单个目录的处理结果
ACTION_REMOVED = "removed"      # 删掉并重建
ACTION_MISSING = "missing"      # 原本就不存在（跳过删除，仍然建回来）
ACTION_SKIPPED = "skipped"      # 防护拦下 / 用户取消：**没有动过**
ACTION_FAILED = "failed"        # 删除或重建失败（目录保持原样）


@dataclass(frozen=True)
class CacheDirResult:
    """一个缓存目录的处理结果（逐条展示给用户，可追溯）。"""

    label: str
    path: Path
    action: str
    freed_bytes: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action in (ACTION_REMOVED, ACTION_MISSING)

    def describe(self) -> str:
        """一行中文结果（输出区用）。"""
        if self.action == ACTION_REMOVED:
            return f"{self.label}：已删除并重建（释放 {human_size(self.freed_bytes)}）"
        if self.action == ACTION_MISSING:
            return f"{self.label}：原本不存在，已创建空目录"
        if self.action == ACTION_SKIPPED:
            return f"{self.label}：已跳过（{self.detail}）"
        return f"{self.label}：删除失败（{self.detail}），目录保持原样"


@dataclass(frozen=True)
class CacheClearReport:
    """一次清理的完整结果。"""

    results: list[CacheDirResult] = field(default_factory=list)

    @property
    def freed_bytes(self) -> int:
        return sum(item.freed_bytes for item in self.results)

    @property
    def ok(self) -> bool:
        """是否全部按预期处理（跳过不算失败，删除失败算）。"""
        return all(item.ok for item in self.results)

    @property
    def removed_any(self) -> bool:
        return any(item.action == ACTION_REMOVED for item in self.results)

    def summary(self) -> str:
        """多行中文摘要（写进更新页的状态区）。"""
        head = (
            f"升级缓存已清理（释放 {human_size(self.freed_bytes)}）"
            if self.removed_any
            else "升级缓存无需清理"
        )
        lines = [head, ""]
        lines += [f"　· {item.describe()}" for item in self.results]
        return "\n".join(lines)


def cache_dirs() -> list[tuple[str, Path]]:
    """两个缓存目录：``(目录名, 路径)``；**只算路径，不创建**。"""
    return [
        (
            platform_dirs.PACKAGE_DIRNAME,
            platform_dirs.update_package_dir(create=False),
        ),
        (
            platform_dirs.INSTALLER_DIRNAME,
            platform_dirs.installer_dir(create=False),
        ),
    ]


def confirm_text() -> str:
    """清理前的确认文案（弹窗正文；纯文本，便于单测断言）。

    需求方 2026-09-24 指定必须说明：清空哪两个目录、正常更新时这些目录会怎样、
    以及"升级失败时才值得手动清理"。
    """
    entries = "\n".join(f"　· {path}" for _label, path in cache_dirs())
    return (
        "将清空以下两个升级缓存目录：\n\n"
        f"{entries}\n\n"
        "这两个目录按设计就是可丢弃的：更新成功后，桌面端会自动删除已下载的更新包；"
        "更新安装器在 Windows 上无法自行删除，会在下一次更新时被覆盖，"
        "在 Linux / macOS 上则会自行删除。因此正常情况下，目录里通常只剩少量升级日志。\n\n"
        "只有在升级失败时，目录中才会残留下载不完整的更新包与上一次的安装器副本，"
        "此时手动清理是一个好选择。清理后需要重新检查更新。"
    )


def clear_update_cache(
    *, cancel: Callable[[], bool] | None = None
) -> CacheClearReport:
    """清空两个升级缓存目录并重建为空的。

    :param cancel: 可选的取消回调（返回 True 表示放弃剩余目录）；已经处理完的
        目录不回滚 —— 删掉的东西本来就该删。
    """
    results: list[CacheDirResult] = []
    for label, path in cache_dirs():
        if cancel is not None and cancel():
            results.append(
                CacheDirResult(label, path, ACTION_SKIPPED, detail="已取消")
            )
            continue
        results.append(_clear_one(label, path))
    report = CacheClearReport(results)
    log.info(
        "清理升级缓存：%s",
        "；".join(item.describe() for item in report.results) or "无目录",
    )
    return report


def failed_report(message: str) -> CacheClearReport:
    """构造一个"整体失败"的报告（清理线程兜底异常时用，不再往外抛）。"""
    return CacheClearReport(
        [
            CacheDirResult(label, path, ACTION_FAILED, detail=message)
            for label, path in cache_dirs()
        ]
    )


def _clear_one(label: str, path: Path) -> CacheDirResult:
    """处理一个目录：防护检查 → 删除 → 重建。**任何情况都不抛异常。**"""
    root = platform_dirs.temp_root()
    if path.name != label or path.parent != root:
        # 防呆：绝不删除"名字或位置不符"的路径，更不递归删临时根目录
        return CacheDirResult(
            label, path, ACTION_SKIPPED,
            detail=f"路径不在更新临时目录内：{path}",
        )
    if path.is_symlink():
        return CacheDirResult(
            label, path, ACTION_SKIPPED, detail="是符号链接，拒绝跟随删除"
        )

    if not path.exists():
        result = CacheDirResult(label, path, ACTION_MISSING)
    else:
        size = _dir_size(path)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            return CacheDirResult(label, path, ACTION_FAILED, detail=_reason(exc))
        result = CacheDirResult(label, path, ACTION_REMOVED, freed_bytes=size)

    try:
        # 删掉或本来就没有，都建回来：后续下载与"复制安装器到临时目录"直接可用
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CacheDirResult(
            label, path, ACTION_FAILED, detail=f"重建目录失败：{_reason(exc)}"
        )
    return result


def _dir_size(path: Path) -> int:
    """目录当前占用（不跟随软链接，统计失败按 0 计 —— 只为展示）。"""
    total = 0
    for current, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def _reason(exc: OSError) -> str:
    detail = getattr(exc, "strerror", "") or str(exc)
    winerror = getattr(exc, "winerror", None)
    return f"{detail}（WinError {winerror}）" if winerror else detail


__all__ = [
    "ACTION_FAILED",
    "ACTION_MISSING",
    "ACTION_REMOVED",
    "ACTION_SKIPPED",
    "CacheClearReport",
    "CacheDirResult",
    "cache_dirs",
    "clear_update_cache",
    "confirm_text",
    "failed_report",
]
