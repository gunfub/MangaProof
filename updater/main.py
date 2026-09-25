# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器 CLI 入口（需求 §43/§45/§46/§83）。

职责很窄：解析参数 → 规范化绝对路径 → 交给 :class:`updater.installer.Installer`
→ 返回退出码。**不依赖 ``cwd`` 推断任何路径**（§45 明令禁止）。

在最早期就要解决两件 PyInstaller windowed onefile 的坑：

1. ``console=False`` 构建时 ``sys.stdout`` / ``sys.stderr`` 可能是 ``None``，
   任何 ``print`` 都会 ``AttributeError``。因此先手工扫描 argv 拿到
   ``--status-file``（或 ``--success-marker``）的目录，把日志文件
   ``installer-<token>.log`` 建好，必要时**把 stdout/stderr 重定向到它**；
2. 后台线程与主线程的未捕获异常都要有记录：装 ``sys.excepthook`` 与
   ``threading.excepthook``，并把失败写进状态文件（§64）。

直接双击的防护见 §83：打包后的安装器缺参启动时**必须弹提示框并优雅退出**，
而不是像以前那样只写一行日志、退出码 2、用户什么都看不到。

命令行（与主程序侧的跨进程契约一致）::

    MangaProof-update-installer --install-dir <绝对路径> --package <绝对路径>
        --data-backup <绝对路径> --success-marker <绝对路径>
        --version <版本号> --sha256 <hex|""> --parent-pid <pid>
        --token <一次性 token> --platform windows|linux|macos
        [--cli] [--status-file <路径>] [--allow-direct-launch]

退出码见 :class:`updater.installer.ExitCode`：0 成功，其余非 0 且必已完成回滚。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Sequence

# 允许 `python updater/main.py` 直接运行（PyInstaller spec 的入口就是本文件）
if __package__ in (None, ""):  # pragma: no cover - 只在直接执行时进入
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from updater import INSTALLER_NAME, privilege, ui  # noqa: E402
from updater import state as state_mod  # noqa: E402
from updater.installer import (  # noqa: E402
    ExitCode,
    Installer,
    InstallerOptions,
    Runtime,
    rerun_argv_from_process,
)

log = logging.getLogger("mangaproof.updater.main")

PROGRAM = INSTALLER_NAME
LOG_PREFIX = "installer-"

#: 调试逃生参数（§83）：跳过"只能由主程序调用"的提示与检查
ALLOW_DIRECT_FLAG = "--allow-direct-launch"

#: 必填参数（§45 的接口契约；用于生成"缺少启动参数：…"诊断行）
REQUIRED_FLAGS: tuple[str, ...] = (
    "--install-dir",
    "--package",
    "--data-backup",
    "--success-marker",
    "--version",
    "--token",
    "--platform",
)

_LOGGER_READY = False
_LOGGER_KEY: tuple | None = None
_LOG_PATH: Path | None = None
_STATUS_PATH: Path | None = None


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """§45 的参数集合（必填项按接口契约；可选参数给出安全默认）。"""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="MangaProof 更新安装器（独立程序，不依赖主程序运行环境）",
    )
    parser.add_argument("--install-dir", required=True,
                        help="安装目录（Windows/Linux：含主程序可执行文件的目录；macOS：MangaProof.app）")
    parser.add_argument("--package", required=True, help="已下载并校验过的更新包（绝对路径）")
    parser.add_argument("--data-backup", required=True, help="用户数据备份目录（绝对路径）")
    parser.add_argument("--success-marker", required=True,
                        help="成功标记文件路径（在更新临时目录里，需求 §58）")
    parser.add_argument("--version", required=True, help="目标版本号")
    parser.add_argument("--token", required=True, help="一次性 token（写入 marker 校验用，需求 §59/§61）")
    parser.add_argument("--platform", required=True, choices=["windows", "linux", "macos"],
                        help="目标平台")
    parser.add_argument("--sha256", default="", help="更新包期望 SHA-256（可为空字符串）")
    parser.add_argument("--parent-pid", type=int, default=0, help="主程序 pid（等待其退出，§46）")
    parser.add_argument("--cli", action="store_true", help="无 GUI 模式（无 tkinter/无图形会话）")
    parser.add_argument("--status-file", default=None,
                        help=f"安装器状态文件（建议持久目录下的 {state_mod.STATE_FILE_NAME}）")
    parser.add_argument(ALLOW_DIRECT_FLAG, action="store_true",
                        help="调试用：允许直接启动（跳过「只能由主程序调用」的提示与检查，需求 §83）")
    return parser


def normalize_path(value: str) -> Path:
    """把参数规范成绝对路径（``~`` 展开 + 去冗余），不解析软链接（§45）。"""
    text = str(value).strip()
    if not text:
        raise ValueError("路径为空")
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        raise ValueError(f"必须是绝对路径（不得依赖 cwd）：{value!r}")
    return Path(os.path.normpath(str(path)))


def parse_options(argv: Sequence[str], parser: argparse.ArgumentParser | None = None) -> InstallerOptions:
    """解析并校验参数；路径不合法时抛 :class:`ValueError`。"""
    parser = parser or build_parser()
    ns = parser.parse_args(list(argv))
    return InstallerOptions(
        install_dir=normalize_path(ns.install_dir),
        package=normalize_path(ns.package),
        data_backup=normalize_path(ns.data_backup),
        success_marker=normalize_path(ns.success_marker),
        version=str(ns.version).strip(),
        token=str(ns.token),
        platform=str(ns.platform),
        sha256=str(ns.sha256 or "").strip(),
        parent_pid=int(ns.parent_pid or 0),
        status_file=normalize_path(ns.status_file) if ns.status_file else None,
        cli=bool(ns.cli),
        allow_direct_launch=bool(ns.allow_direct_launch),
        rerun_argv=rerun_argv_from_process(),
    )


# --------------------------------------------------------------------------- #
# 日志（必须在最早期可用）
# --------------------------------------------------------------------------- #


def _scan_value(argv: Sequence[str], flag: str) -> str | None:
    """不用 argparse 手工扫 ``--flag value`` / ``--flag=value``（解析失败也要能建日志）。"""
    prefix = flag + "="
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _flag_present(argv: Sequence[str], flag: str) -> bool:
    """命令行里是否出现了 ``--flag`` / ``--flag=…``（argparse 之前的粗判）。"""
    prefix = flag + "="
    return any(arg == flag or arg.startswith(prefix) for arg in argv)


def _safe_token(token: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]", "_", (token or "").strip())
    return (text[:24] or "notoken")


def _direct_launch_log_dir() -> Path | None:
    """主程序准备的安装器临时目录（存在才用）。

    直接双击时没有 ``--status-file`` / ``--success-marker`` 可用，日志原本落在
    系统临时根目录（永远是残渣）。放进安装器临时目录后，§82「清理升级缓存」
    能一起清掉。**不存在就不创建**：不能让一次误双击凭空造出目录。
    """
    try:
        from mangaproof.update import platform_dirs

        candidate = platform_dirs.installer_dir(create=False)
    except Exception as exc:  # pragma: no cover - 主程序模块缺失等异常情况
        log.debug("无法解析安装器临时目录：%s", exc)
        return None
    return candidate if candidate.is_dir() else None


def _log_directory(argv: Sequence[str]) -> Path:
    status = _scan_value(argv, "--status-file")
    marker = _scan_value(argv, "--success-marker")
    for value in (status, marker):
        if value:
            try:
                parent = normalize_path(value).parent
            except ValueError:
                continue
            if parent.is_dir():
                return parent
            try:
                parent.mkdir(parents=True, exist_ok=True)
                return parent
            except OSError:
                continue
    fallback = _direct_launch_log_dir()
    if fallback is not None:
        return fallback
    return Path(tempfile.gettempdir())


def setup_logging(argv: Sequence[str]) -> Path | None:
    """建 ``installer-<token>.log`` 并（必要时）把 stdout/stderr 接到它上面。

    同一次进程里被调用多次（单测/嵌入调用）时按 argv 重新配置：否则第二次
    调用会把日志继续写到上一次的文件里，排查时对不上号。
    """
    global _LOGGER_READY, _LOGGER_KEY, _LOG_PATH
    token = _scan_value(argv, "--token")
    key = (tuple(argv), token)
    if _LOGGER_READY and _LOGGER_KEY == key:
        return _LOG_PATH

    stream = None
    try:
        target = _log_directory(argv) / f"{LOG_PREFIX}{_safe_token(token)}.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        stream = open(target, "a", encoding="utf-8", errors="replace", buffering=1)
        _LOG_PATH = target
    except OSError:
        _LOG_PATH = None

    handlers: list[logging.Handler] = []
    if stream is not None:
        handlers.append(logging.StreamHandler(stream))
    # stdout/stderr 为 None（windowed onefile）时，所有输出改走日志文件
    if sys.stdout is None or sys.stderr is None:
        if stream is not None:
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = stream
        else:  # 连日志都建不出来：至少别让 print 炸掉
            sink = open(os.devnull, "w", encoding="utf-8")
            if sys.stdout is None:
                sys.stdout = sink
            if sys.stderr is None:
                sys.stderr = sink
    console = logging.StreamHandler(sys.stderr) if sys.stderr is not None else None
    if console is not None:
        handlers.append(console)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    _LOGGER_READY = True
    _LOGGER_KEY = key
    if _LOG_PATH is not None:
        log.info("安装器日志：%s", _LOG_PATH)
    return _LOG_PATH


def _record_crash(message: str) -> None:
    """把未捕获异常写进状态文件（§64：异常中断后要能判断更新未完成）。"""
    if _STATUS_PATH is None:
        return
    try:
        payload = state_mod.read_json(_STATUS_PATH) or {}
        payload.update({"phase": "FAILED", "error": message, "crashed": True})
        payload.pop("history", None)
        state_mod.assert_no_secrets(payload)
        state_mod.atomic_write_json(_STATUS_PATH, payload)
    except Exception:  # pragma: no cover - 记录失败不影响退出码
        log.error("状态文件写入失败：%s", _STATUS_PATH)


def install_excepthooks() -> None:
    """主线程 + 后台线程的兜底（安装器任何时候被杀/崩溃都要留痕）。"""

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("未捕获异常：\n%s", text)
        _record_crash(f"{exc_type.__name__}: {exc_value}")

    def _thread_hook(args):  # pragma: no cover - 后台线程异常
        text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        log.error("后台线程未捕获异常（%s）：\n%s", args.thread.name if args.thread else "?", text)
        _record_crash(f"{args.exc_type.__name__}: {args.exc_value}")

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


# --------------------------------------------------------------------------- #
# 直接启动提示（§83）
# --------------------------------------------------------------------------- #


def _is_frozen() -> bool:
    """是否是打包后的安装器（PyInstaller onefile）。

    只有打包产物才拦：源码运行（``python updater/main.py``）必须保持命令行行为，
    否则开发、排障、单测都会被一个弹窗挡住（§83）。
    """
    return bool(getattr(sys, "frozen", False))


def _missing_args_detail(argv: Sequence[str]) -> str:
    """缺参诊断行（§83）：列出命令行里没出现的必填参数。"""
    missing = [flag for flag in REQUIRED_FLAGS if not _flag_present(argv, flag)]
    if missing:
        return "缺少启动参数：" + "、".join(missing)
    return "启动参数不完整或非法"


def notify_direct_launch(
    detail: str = "",
    *,
    allow: bool = False,
    cli: bool = False,
) -> bool:
    """§83：直接启动（缺参/参数非法）时的提示；返回是否真的弹了窗。

    三种情况不弹窗，只留日志（命令行本来就有报错可看）：

    - ``--allow-direct-launch``：调试逃生参数；
    - ``--cli``：显式不要 GUI；
    - 源码运行（非 frozen）。
    """
    if allow:
        log.info("%s：已跳过「只能由主程序调用」提示（调试参数，§83）", ALLOW_DIRECT_FLAG)
        return False
    if not _is_frozen():
        log.info("源码运行（非打包）：不做直接启动提示，按命令行报错处理（§83）")
        return False
    return ui.show_launch_notice(detail, gui=not cli)


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #


def run(options: InstallerOptions) -> int:
    """按是否需要 GUI 选择 UI，并执行安装。"""

    def work(reporter) -> int:
        runtime = Runtime(
            reporter=reporter,
            user_kwargs=privilege.child_user_kwargs(),
            log_path=_LOG_PATH,          # 收尾时复制进程序目录 logs/（见 _preserve_installer_log）
        )
        return Installer(options, runtime).run()

    if options.cli:
        log.info("命令行模式：不启动 GUI（--cli）")
        return ui.run_with_ui(work, cli=True, version=options.version)
    return ui.run_with_ui(
        work,
        title="MangaProof 更新安装器",
        subtitle=f"正在更新到 {options.version}",
        version=options.version,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """入口：返回退出码（0 = 成功）。"""
    global _STATUS_PATH
    args = list(sys.argv[1:] if argv is None else argv)
    allow_direct = _flag_present(args, ALLOW_DIRECT_FLAG)
    cli_requested = _flag_present(args, "--cli")
    log_path = setup_logging(args)
    install_excepthooks()

    parser = build_parser()
    try:
        options = parse_options(args, parser)
    except SystemExit as exc:  # argparse：--help（0）或用法错误（2）
        code = int(exc.code or 0)
        if code != 0:
            # §83：打包后缺参/参数非法 = 十有八九是被人直接双击了 → 必须给出可见提示
            notify_direct_launch(
                _missing_args_detail(args), allow=allow_direct, cli=cli_requested
            )
        return code
    except ValueError as exc:
        message = f"安装器参数错误：{exc}"
        log.error("%s", message)
        print(message, file=sys.stderr)
        return int(ExitCode.USAGE)

    _STATUS_PATH = options.state_path
    # Windows：cwd 若在安装目录里，第一步"安装目录改名"就会 WinError 32
    # （见 privilege.ensure_safe_cwd 的说明）。早于任何文件操作执行。
    if options.platform == "windows":
        privilege.ensure_safe_cwd(options.install_dir)
    log.info(
        "安装器启动：版本=%s 平台=%s 安装目录=%s 包=%s pid=%s",
        options.version, options.platform, options.install_dir,
        options.package.name, options.parent_pid,
    )
    try:
        code = int(run(options))
    except BaseException as exc:  # UI 层之外的兜底
        log.error("安装器异常：%s\n%s", exc, traceback.format_exc())
        _record_crash(f"{type(exc).__name__}: {exc}")
        code = int(ExitCode.INTERNAL)
    log.info("安装器结束：退出码 %s", code)
    if code != 0:
        reason = ""
        payload = state_mod.read_json(_STATUS_PATH) if _STATUS_PATH else None
        if payload:
            reason = str(payload.get("error") or payload.get("message") or "")
        detail = f"：{reason}" if reason else ""
        message = f"{PROGRAM} 更新失败（退出码 {code}）{detail}"
        if log_path is not None:
            message = f"{message}；详见日志 {log_path}"
        try:
            print(message, file=sys.stderr)
        except Exception:  # pragma: no cover - 流不可用
            pass
    logging.shutdown()
    return code


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
