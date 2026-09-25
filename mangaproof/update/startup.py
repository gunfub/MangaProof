# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""启动时的更新握手与异常中断恢复（需求 §57、§58、§60、§64）。

两件事：

**A. 被安装器拉起时（带 ``--update-token`` / ``--success-marker`` / ``--update-version``）**

新版主程序在"主程序启动 + 核心初始化 + 旧数据成功加载"之后（需求 §57）
写成功标记（需求 §58：标记放**更新临时目录**，不放程序目录）。
写得太早会让安装器在数据还没加载时就判定成功，因此本模块提供的是
:func:`mark_update_launch_success`，由 ``main.py`` 在窗口显示后调用。

**B. 正常启动时（不带这些参数）**

检查上一次更新是否**没有收尾**（需求 §64：安装器被杀、系统关机/重启、断电、
新版本崩溃）。判据按可靠性排序：

1. 成功标记存在且版本 == 当前版本 → 上次更新其实**成功了**，只是安装器没来得及清理
   → 由新版自己完成收尾（删 ``.old``、备份、更新包、安装器目录、marker）；
2. 安装器状态文件停在中间阶段 → 说明更新中断 → 提示用户（并给出手动恢复指引）；
3. 安装目录里存在 ``MangaProof.old`` 而当前程序能正常启动 → 只清理残留，
   不动当前程序（**绝不**无条件删除 ``.old``：它可能是用户唯一能跑的副本）。

本模块只做"判断 + 收尾"，不做替换（替换是安装器的职责）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from mangaproof import __version__
from mangaproof.update import platform_dirs

log = logging.getLogger("mangaproof.update.startup")

#: 安装器拉起新版时使用的参数（与安装器侧的契约，见 update/installer.py）
ARG_TOKEN = "--update-token"
ARG_MARKER = "--success-marker"
ARG_VERSION = "--update-version"


@dataclass
class UpdateHandshake:
    """本次启动是否"由安装器拉起"。"""

    token: str = ""
    marker_path: Path | None = None
    version: str = ""

    @property
    def active(self) -> bool:
        return bool(self.token and self.marker_path)


def parse_handshake(argv: list[str]) -> tuple[UpdateHandshake, list[str]]:
    """从 argv 里摘出更新握手参数，返回 ``(握手信息, 其余参数)``。

    用 :mod:`argparse` 的 ``parse_known_args``：Qt 自己也会消费命令行参数，
    我们只关心这三个，别的原样留给 QApplication。
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(ARG_TOKEN, dest="token", default="")
    parser.add_argument(ARG_MARKER, dest="marker", default="")
    parser.add_argument(ARG_VERSION, dest="version", default="")
    known, rest = parser.parse_known_args(argv)

    handshake = UpdateHandshake(
        token=str(known.token or ""),
        marker_path=Path(known.marker) if known.marker else None,
        version=str(known.version or ""),
    )
    if handshake.active:
        log.info(
            "本次启动由更新安装器拉起（目标版本 %s，token %s…）",
            handshake.version or "未知",
            handshake.token[:8],
        )
    return handshake, rest


def mark_update_launch_success(handshake: UpdateHandshake) -> bool:
    """写成功标记（需求 §57）。

    调用时机（``main.py``）：窗口已 ``show()``、settings 与 recent 都已成功加载。
    返回是否写入成功；失败只记日志，**不阻断程序启动**（用户至少还能用新版）。
    """
    if not handshake.active:
        return False
    marker = handshake.marker_path
    assert marker is not None
    payload = {
        "token": handshake.token,
        "version": handshake.version or str(__version__),
        "pid": os.getpid(),
        "ts": time.time(),
    }
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(marker)          # 原子替换：安装器正在轮询该文件
    except OSError as exc:
        log.warning("写入更新成功标记失败（不影响使用）：%s", exc)
        return False
    log.info("已写入更新成功标记：%s（版本 %s）", marker, payload["version"])
    return True


# --- 异常中断恢复（需求 §64） ------------------------------------------------

@dataclass
class RecoveryReport:
    """上次更新的残留情况。"""

    had_old_dir: bool = False
    old_dir: Path | None = None
    stage: str = ""
    finished_marker: bool = False
    cleaned: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        """是否需要告诉用户"上次更新没走完"。"""
        return bool(self.had_old_dir) and not self.finished_marker


def detect_interrupted_update() -> RecoveryReport:
    """检查上次更新是否留下残留（需求 §64）。

    只读判断 + 安全清理，不做任何替换或删除程序目录的操作。
    """
    report = RecoveryReport()

    marker = platform_dirs.success_marker_path()
    data = _read_json(marker)
    if isinstance(data, dict):
        marker_version = str(data.get("version", ""))
        if marker_version in ("", str(__version__)):
            # 标记的版本就是当前版本 → 上次更新**成功**了，只是没人清理
            report.finished_marker = True
            log.info("检测到上次更新已成功（marker 版本 %s），开始收尾清理", marker_version)
            report.cleaned = _cleanup_after_success()
            return report

    state = _read_json(platform_dirs.state_file_path())
    if isinstance(state, dict):
        report.stage = str(state.get("phase", ""))

    for candidate in _old_dir_candidates():
        if candidate.exists():
            report.had_old_dir = True
            report.old_dir = candidate
            break

    if report.had_old_dir and report.old_dir is not None:
        log.warning(
            "检测到上次更新残留：%s（阶段 %s）",
            report.old_dir.name, report.stage or "未知",
        )
    return report


def _old_dir_candidates() -> list[Path]:
    """``MangaProof.old`` 可能出现的位置（按平台）。"""
    from mangaproof.update import platform as platform_pkg

    try:
        install_dir = platform_pkg.current().install_dir()
    except Exception:      # pragma: no cover - 平台模块异常时不影响启动
        return []
    name = install_dir.name
    return [install_dir.with_name(f"{name}.old")]


def _cleanup_after_success() -> list[str]:
    """上次更新已成功但未收尾：删掉旧版本与全部临时目录（需求 §61）。"""
    import shutil

    removed: list[str] = []
    for path in _old_dir_candidates():
        if not path.exists():
            continue
        try:
            shutil.rmtree(path)
            removed.append(str(path))
        except OSError as exc:
            log.info("清理 %s 失败（下次再试）：%s", path.name, exc)

    removed.extend(platform_dirs.cleanup_temp_dirs())
    log.info("更新收尾清理完成：%s", removed or "无需清理")
    return removed


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recovery_message(report: RecoveryReport) -> str:
    """给用户看的中文提示（需求 §64：发现更新未完成，优先恢复到旧版本状态）。"""
    if not report.needs_attention:
        return ""
    where = str(report.old_dir) if report.old_dir else "（未知位置）"
    # 更新临时目录里的"数据备份"只是升级途中的中转副本（§49 复制出来、§56 恢复回去），
    # 而且可以被「清理升级缓存」手动删掉 —— 所以这句话必须看它是否真的还在，
    # 否则会把用户指到一个不存在的目录去。
    backup_hint = (
        "用户数据（settings.json / recent.json / logs）保存在更新临时目录的备份里。"
        if platform_dirs.data_backup_dir(create=False).is_dir()
        else "更新临时目录里的数据备份已不存在（可能已被手动清理），"
             "无法再从那里恢复用户数据。"
    )
    return (
        "上一次更新没有正常完成。\n\n"
        f"旧版本目录仍保留在：\n{where}\n\n"
        "当前运行的程序可以继续使用。若当前版本存在问题，"
        "可关闭程序后把上述目录改名为原来的名字，即可回到旧版本；"
        f"{backup_hint}"
    )


__all__ = [
    "ARG_MARKER",
    "ARG_TOKEN",
    "ARG_VERSION",
    "RecoveryReport",
    "UpdateHandshake",
    "detect_interrupted_update",
    "mark_update_launch_success",
    "parse_handshake",
    "recovery_message",
]
