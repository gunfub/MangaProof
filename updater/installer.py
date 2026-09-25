# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器状态机（需求 §45/§46/§53~§64/§78，调研报告 §5.4/§11.4）。

严格按 §78 的顺序推进::

    INIT → VALIDATE → BACKUP_DATA → VERIFY_PACKAGE → RENAME_OLD → EXTRACT
         → RESTORE_DATA → LAUNCH_NEW → WAIT_SUCCESS → (SUCCESS → CLEANUP
                                                        / 否则 ROLLBACK)

三条不容妥协的规则：

1. **破坏性操作前完成全部校验**（§53）：``MangaProof → MangaProof.old`` 之前必须
   已经过了"包存在 + SHA-256 + 结构 + 主程序存在"。
2. **等待成功标记最长 60 秒**（§59），并且新版**提前退出且没有标记时立即回滚**，
   不等满 60 秒（§60）。标记必须 token + version 双匹配（§59/§61）。
   "新版提前退出"的判据是**按进程名找不到主程序**，而不是"跟踪的 pid 消失"：
   macOS 用 ``open -n -a`` 启动 .app，``spawn`` 拿到的 pid 属于 ``open`` 自己，
   它必然秒退（2026-09-24 实测的"更新后误报失败"根因）。pid 消失后先按名字
   重定位（宽限 :data:`RELOCATE_TIMEOUT` 秒），找不到才判失败。等待期间不结束
   主程序；仅超时才按既有语义结束它。安装前同理：pid 退出后要用进程名复核一次。
3. **任何判为失败的分支都必须完成回滚**（§62/§63）：:meth:`Installer.run` 的
   异常出口统一走 :func:`updater.rollback.rollback`；没做过替换时回滚是无操作，
   因此"校验失败不触碰安装目录"是天然成立的。

为可测试性，进程/时间/提权都是可注入的接缝（:class:`Runtime`）：单测不会真的
起 GUI、不会真的提权，也不会等满 60 秒。
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Callable, Sequence

from mangaproof.config.user_data import validate_rules
from mangaproof.update import platform_dirs
from mangaproof.utils.logging_setup import logs_dir

from updater import archive, backup, privilege, rollback, state as state_mod
from updater import verify as verify_mod
from updater.ui import LoggingReporter, NullReporter, Reporter

log = logging.getLogger("mangaproof.updater.installer")

#: 等待新版写入成功标记的上限（需求 §59：最长 60 秒）
SUCCESS_TIMEOUT = 60.0
#: 等待主程序退出的上限（§46：主程序确认安装器启动成功后主动退出）
PARENT_EXIT_TIMEOUT = 30.0
#: 轮询间隔
POLL_INTERVAL = 0.25
#: 跟踪的 pid 消失后，按**进程名**重新找到主程序的宽限期。
#: macOS 用 ``open -n -a`` 启动 .app：spawn 拿到的 pid 是 ``open`` 自己的，
#: 它必然秒退，而真正的 app 由 LaunchServices 稍后拉起（冷启动 / Gatekeeper
#: 校验可能要几秒）。一退出就判失败就是 2026-09-24 实测的误报。
RELOCATE_TIMEOUT = 20.0
#: 按进程名查询的最小间隔（``ps -A`` 不便宜，不必每个 poll 都查一次）
LOOKUP_INTERVAL = 0.5
#: 安装器临时目录名前缀（需求 §40/§36/§37：TEMP|CACHE/MangaProof-update-installer）
INSTALLER_TEMP_PREFIX = "MangaProof-update-installer"


def safe_token(value: str | None) -> str:
    """把 token/版本号变成可安全用于文件名的片段（供安装日志命名）。"""
    import re

    text = re.sub(r"[^A-Za-z0-9._-]", "_", (value or "").strip())
    return (text[:24] or "notoken")


class ExitCode(IntEnum):
    """安装器退出码（0 = 成功；其余非 0，并在 stderr/日志给出可读原因）。"""

    OK = 0
    USAGE = 2            # 参数错误（argparse 同码）
    VALIDATION = 10      # 参数/环境/权限校验失败
    BACKUP = 20          # 备份用户数据失败
    VERIFY = 30          # 更新包校验失败（§53）
    REPLACE = 40         # RENAME_OLD 失败（§54）
    EXTRACT = 50         # 解压失败或解压后主程序不存在（§52）
    RESTORE = 60         # 恢复用户数据失败（§56）
    LAUNCH = 70          # 新版无法启动（§62）
    SUCCESS_TIMEOUT = 80 # 新版提前退出 / 60 秒内没有成功标记（§59/§60）
    ROLLBACK_FAILED = 81 # 回滚本身失败（§63，最严重）
    CANCELLED = 82       # 用户在提权对话框中取消（§42）
    INTERNAL = 90        # 未预期的内部异常


class InstallerFailure(Exception):
    """安装流程中的可预期失败（携带退出码与可读原因）。"""

    def __init__(self, code: ExitCode, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class InstallerOptions:
    """CLI 参数的规范化结果（全部是绝对路径，需求 §45：不得依赖 cwd）。"""

    install_dir: Path
    package: Path
    data_backup: Path
    success_marker: Path
    version: str
    token: str
    platform: str
    sha256: str = ""
    parent_pid: int = 0
    status_file: Path | None = None
    cli: bool = False
    #: 重跑自身（提权）用的完整 argv；由 main.py 填入
    rerun_argv: tuple[str, ...] = ()

    @property
    def state_path(self) -> Path:
        """安装器状态文件路径（**全流程只用一个**）。

        主程序侧约定：状态文件叫 ``installer-state.json``，放在 ``--status-file``
        的**同目录**里。因此 :func:`resolve_state_path` 的规则是：

        - 传的是 ``…/installer-state.json`` → 用它；
        - 传的是同目录下的其它名字，但那个文件**已存在**（主程序真的在用它）→ 用它；
        - 否则 → ``<--status-file 的目录>/installer-state.json``；
        - 完全没传 ``--status-file`` → ``<--success-marker 的目录>/installer-state.json``。
        """
        return resolve_state_path(self.status_file, self.success_marker)


class ProcessOps:
    """启动/探测新版进程（可注入：单测不真的起进程）。

    除了"某个 pid 还在不在"，还必须能回答"**有没有叫这个名字的进程**"：
    macOS 上新版 .app 是经 LaunchServices（``open -n -a``）拉起的，``spawn``
    拿到的 pid 属于 ``open`` 自己、与真正的 app 没有父子关系，只能按名字找回来
    （2026-09-24 实测的"更新后误报失败"根因）。
    """

    def __init__(self) -> None:
        self._procs: dict[int, subprocess.Popen] = {}
        #: 本机 ``ps`` 是否接受 ``state`` 列（只探测一次，失败就退回不带 state 的形式）
        self._ps_with_state: bool | None = None

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: int | None = None,
        group: int | None = None,
    ) -> int:
        kwargs: dict = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":  # pragma: no cover - 平台分支
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True
            if user is not None:
                kwargs["user"] = user
            if group is not None:
                kwargs["group"] = group
        proc = subprocess.Popen([str(a) for a in argv], **kwargs)  # noqa: S603
        self._procs[proc.pid] = proc
        return proc.pid

    def alive(self, pid: int) -> bool:
        proc = self._procs.get(pid)
        if proc is not None:
            return proc.poll() is None
        return _probe_pid(pid)

    def terminate(self, pid: int) -> None:
        proc = self._procs.get(pid)
        if proc is not None and proc.poll() is None:
            try:
                if os.name != "nt" and proc.pid == os.getpgid(proc.pid):
                    # 我们自己 start_new_session 拉起的进程：连同它的子进程一起结束
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
            except OSError:  # pragma: no cover
                pass
            try:  # 回收，避免留下僵尸进程（否则"进程是否还在"会一直为真）
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):  # pragma: no cover
                pass
            return
        if pid <= 0:  # pragma: no cover
            return
        try:
            if os.name == "nt":  # pragma: no cover - 平台分支
                import ctypes

                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
            else:
                os.kill(pid, 15)
        except Exception:  # pragma: no cover - 尽力而为
            pass

    # -- 按进程名查找（"启动器"型拉起 / pid 复核） ------------------------- #

    def exe_matches(self, pid: int, name: str) -> bool | None:
        """``pid`` 的可执行文件名是否等于 ``name``。

        :returns: ``True``/``False``；``None`` = **查不了**（没有 ps / 调用失败），
            调用方必须把它当成"无法确认"，不能当成"不匹配"。
        """
        if pid <= 0 or not name:
            return None
        if os.name == "nt":  # pragma: no cover - 平台分支
            rows = _win_process_table()
            if rows is None:
                return None
            for row_pid, exe in rows:
                if row_pid == pid:
                    return exe == name
            return False
        rows = self._ps_table(pid=pid)
        if rows is None:
            return None
        for row_pid, _state, comm in rows:
            if row_pid == pid:
                return _basename(comm) == name
        return False

    def find_running(self, name: str, *, exclude: set[int] | None = None) -> list[int] | None:
        """正在运行的、可执行文件名等于 ``name`` 的 pid 列表（升序）。

        :returns: ``None`` = **查不了**（此时上层必须继续等，绝不能判失败——
            否则"ps 不可用"会变成又一次误报失败）；``[]`` = 确实没有。
        """
        if not name:
            return None
        skip = set(exclude or ())
        if os.name == "nt":  # pragma: no cover - 平台分支
            rows = _win_process_table()
            if rows is None:
                return None
            return sorted(pid for pid, exe in rows if exe == name and pid not in skip)
        table = self._ps_table()
        if table is None:
            return None
        found = [
            pid for pid, state, comm in table
            if pid not in skip and _basename(comm) == name and not _is_zombie(state)
        ]
        return sorted(found)

    def _ps_table(self, pid: int | None = None) -> list[tuple[int, str, str]] | None:
        """跑一次 ``ps`` 取 ``(pid, state, comm)`` 表；查不了返回 ``None``。

        ``comm`` 在 macOS 上是可执行文件全路径、在 Linux 上是进程名（截断到 15
        字符），统一取 basename 比对；带空格路径按"只切前两列"解析。
        ``state`` 用来剔除僵尸进程（僵尸还占着名字，会把等待拖到超时）。
        """
        for with_state in self._ps_forms():
            args = ["ps", "-A", "-o", "pid=,state=,comm="] if with_state else [
                "ps", "-A", "-o", "pid=,comm="
            ]
            if pid is not None:
                args[1] = "-p"
                args.insert(2, str(pid))
            try:
                proc = subprocess.run(  # noqa: S603
                    args, capture_output=True, text=True, timeout=5.0, check=False
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode != 0:
                continue
            if self._ps_with_state is None:
                self._ps_with_state = with_state
            return _parse_ps_table(proc.stdout, with_state=with_state)
        return None

    def _ps_forms(self) -> tuple[bool, ...]:
        if self._ps_with_state is None:
            return (True, False)
        return (self._ps_with_state,)


def _basename(comm: str) -> str:
    """``ps`` 的 comm 列 → 可执行文件名（macOS 是全路径，Linux 是进程名）。"""
    text = (comm or "").strip()
    if not text:
        return ""
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def _is_zombie(state: str) -> bool:
    return (state or "").strip().upper().startswith("Z")


def _parse_ps_table(text: str, *, with_state: bool) -> list[tuple[int, str, str]]:
    """解析 ``ps -o pid=,state=,comm=`` 的输出（comm 可能带空格，只切前两列）。"""
    rows: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if with_state:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid_text, state, comm = parts
        else:
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            pid_text, comm = parts
            state = ""
        try:
            rows.append((int(pid_text), state, comm))
        except ValueError:      # pragma: no cover - ps 不按格式输出时跳过该行
            continue
    return rows


def _win_process_table() -> list[tuple[int, str]] | None:
    """Windows：Toolhelp32 快照取 ``(pid, exe 文件名)``；失败返回 ``None``。

    与 :func:`_probe_pid` 一样是"尽力而为"：拿不到就返回 ``None``，让上层继续等，
    绝不因为查不到而误判失败。
    """
    try:  # pragma: no cover - 平台分支（本机与 CI 都不跑 Windows 单测）
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        MAX_PATH = 260
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * MAX_PATH),
            ]

        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            rows: list[tuple[int, str]] = []
            while True:
                rows.append((int(entry.th32ProcessID), str(entry.szExeFile)))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
            return rows
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return None


def _probe_pid(pid: int) -> bool:
    """进程是否还在（默认实现：POSIX 用 ``kill(pid, 0)``）。"""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - 平台分支
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:  # pragma: no cover
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover
        return False
    return True


@dataclass
class Runtime:
    """可注入的运行时接缝（默认都是真实实现）。"""

    reporter: Reporter = field(default_factory=NullReporter)
    store: state_mod.StateStore | None = None
    ops: ProcessOps = field(default_factory=ProcessOps)
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    elevate: Callable[..., int] | None = None
    is_admin: Callable[[], bool] = privilege.is_admin
    needs_elevation: Callable[[Path], bool] = privilege.needs_elevation
    remove_tree: Callable[..., bool] = rollback.remove_tree
    replace_path: Callable[..., None] = rollback.replace_path
    #: 安装器自己的日志文件（``updater/main.py`` 建的那个）。收尾时会被复制到
    #: 程序目录 ``logs/`` 再连同更新包一起删掉；None = 没有日志可留存。
    log_path: Path | None = None
    success_timeout: float = SUCCESS_TIMEOUT
    parent_timeout: float = PARENT_EXIT_TIMEOUT
    poll_interval: float = POLL_INTERVAL
    #: pid 消失后按进程名重定位主程序的宽限期（见 :data:`RELOCATE_TIMEOUT`）
    relocate_timeout: float = RELOCATE_TIMEOUT
    #: 按进程名查询的最小间隔（见 :data:`LOOKUP_INTERVAL`）
    lookup_interval: float = LOOKUP_INTERVAL
    retries: int = 5
    retry_delay: float = 0.2
    user_kwargs: dict[str, int] = field(default_factory=dict)
    allow_elevation: bool = True

    @property
    def reporter_or_default(self) -> Reporter:
        return self.reporter if self.reporter is not None else NullReporter()


def resolve_state_path(status_file: Path | str | None, success_marker: Path | str) -> Path:
    """状态文件路径解析（跨进程契约：文件名固定为 ``installer-state.json``）。

    安装器只读写这一个状态文件（不另造名字）；详见
    :attr:`InstallerOptions.state_path`。
    """
    if status_file is None:
        return Path(success_marker).parent / state_mod.STATE_FILE_NAME
    given = Path(status_file)
    if given.name == state_mod.STATE_FILE_NAME:
        return given
    if given.exists():
        # 主程序确实在用这个文件（例如它自己初始化过）→ 尊重现状，不另起炉灶
        log.warning(
            "状态文件名不是约定的 %s，但该文件已存在，按现状使用：%s",
            state_mod.STATE_FILE_NAME, given,
        )
        return given
    return given.parent / state_mod.STATE_FILE_NAME


def rerun_argv_from_process() -> tuple[str, ...]:
    """本安装器的重跑命令行（提权时用；frozen 与源码运行两种情况）。"""
    if getattr(sys, "frozen", False):  # PyInstaller onefile
        return (sys.executable, *sys.argv[1:])
    return (sys.executable, *sys.argv)


def build_elevated_argv(options: InstallerOptions) -> list[str]:
    """提权重跑自己的命令行：去掉 ``--cli`` 再补上（提权只跑 CLI 逻辑）。"""
    base = list(options.rerun_argv) or [sys.executable]
    filtered = [arg for arg in base if arg != "--cli"]
    if not options.rerun_argv:  # 退化情况：按参数重建（单测/嵌入调用）
        filtered = [sys.executable, "--install-dir", str(options.install_dir),
                    "--package", str(options.package),
                    "--data-backup", str(options.data_backup),
                    "--success-marker", str(options.success_marker),
                    "--version", options.version, "--token", options.token,
                    "--platform", options.platform,
                    "--sha256", options.sha256]
        if options.status_file is not None:
            filtered += ["--status-file", str(options.status_file)]
    return [*filtered, "--cli"]


class Installer:
    """执行一次完整安装（一次更新 = 一个实例）。"""

    def __init__(self, options: InstallerOptions, runtime: Runtime | None = None) -> None:
        self.options = options
        self.rt = runtime or Runtime()
        self.reporter = self.rt.reporter_or_default
        self.state = state_mod.UpdateState(
            token=options.token,
            version=options.version,
            install_dir=str(options.install_dir),
            old_dir=str(rollback.old_dir_for(options.install_dir)),
            package=str(options.package),
            data_backup=str(options.data_backup),
            success_marker=str(options.success_marker),
            status_file=str(options.state_path),
            platform=options.platform,
            parent_pid=int(options.parent_pid),
        )
        self.store = self.rt.store or state_mod.StateStore(options.state_path)
        self._elevation_required = False
        self._last_marker_reason = ""
        #: 下一次允许"按进程名查进程"的时刻（``ps -A`` 不便宜，按 lookup_interval 节流）
        self._next_lookup_at = 0.0

    # ------------------------------------------------------------------ #
    # 状态推进
    # ------------------------------------------------------------------ #
    def _phase(self, phase: str, message: str = "") -> None:
        self.reporter.phase(phase)
        self.store.transition(self.state, phase, message=message)
        if message:
            self.reporter.log(message)

    def _item(self, rel: str, action: str = "") -> None:
        self.reporter.item(rel, action)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self) -> int:
        try:
            self._init()
            self._validate()
            if self._elevation_required and self.rt.allow_elevation:
                return self._reexec_elevated()
            self._wait_parent()
            self._backup_data()
            self._verify_package()
            self._rename_old()
            self._extract()
            self._restore_data()
            self._launch_new()
            self._wait_success()
            self._cleanup()
        except InstallerFailure as exc:
            return self._handle_failure(exc)
        except BaseException as exc:  # 兜底：任何异常都必须走回滚
            log.error("安装器内部异常：%s", exc, exc_info=True)
            return self._handle_failure(
                InstallerFailure(ExitCode.INTERNAL, f"安装器内部错误：{exc}")
            )
        self._phase("SUCCESS", "更新成功")
        self.reporter.finish(True, "")
        return int(ExitCode.OK)

    # -- INIT ----------------------------------------------------------- #
    def _require_absolute_paths(self) -> None:
        """INIT 之前就要挡住相对路径：否则后面的探测会相对 **cwd** 发生（§45）。"""
        candidates = [
            ("--install-dir", self.options.install_dir),
            ("--package", self.options.package),
            ("--data-backup", self.options.data_backup),
            ("--success-marker", self.options.success_marker),
        ]
        if self.options.status_file is not None:
            candidates.append(("--status-file", self.options.status_file))
        problems = [
            f"{label} 必须是绝对路径（不得依赖 cwd）：{path}"
            for label, path in candidates
            if not Path(path).is_absolute()
        ]
        if problems:
            raise InstallerFailure(ExitCode.VALIDATION, "；".join(problems))

    def _init(self) -> None:
        self._require_absolute_paths()
        self._phase("INIT")
        opts = self.options
        try:
            opts.data_backup.parent.mkdir(parents=True, exist_ok=True)
            opts.success_marker.parent.mkdir(parents=True, exist_ok=True)
            if opts.status_file is not None:
                opts.status_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InstallerFailure(
                ExitCode.VALIDATION, f"无法创建安装器工作目录：{exc}"
            ) from exc
        self.store.save(self.state)
        self.reporter.log(
            f"安装目录：{opts.install_dir}（平台 {opts.platform}，目标版本 {opts.version}）"
        )
        self._recover_previous_run()

    def _recover_previous_run(self) -> None:
        """§64：发现 ``.old`` 等残留时优先恢复到旧版本状态。"""
        old = Path(self.state.old_dir)
        if not old.exists():
            return
        self.reporter.log(f"检测到残留的 {old.name}，正在检查上一次更新是否完成…")
        try:
            result = rollback.recover_interrupted(
                self.state,
                on_item=lambda rel: self._item(rel, "恢复"),
                on_progress=self.reporter.progress,
                sleep=self.rt.sleep,
                retries=self.rt.retries,
                retry_delay=self.rt.retry_delay,
            )
        except rollback.RollbackError as exc:
            raise InstallerFailure(ExitCode.ROLLBACK_FAILED, str(exc)) from exc
        self.reporter.log(result.message)
        if result.action == "broken":
            raise InstallerFailure(ExitCode.VALIDATION, result.message)
        if result.action == "restored":
            # 安装目录刚刚被换回旧版本：本次更新作废，要求重新发起（状态一致优先）
            raise InstallerFailure(
                ExitCode.VALIDATION,
                f"{result.message}；请重新发起一次更新（需求 §64）",
            )
        self.store.save(self.state)

    # -- VALIDATE ------------------------------------------------------- #
    def _validate(self) -> None:
        self._phase("VALIDATE")
        opts = self.options
        problems: list[str] = []
        for label, path in (
            ("--install-dir", opts.install_dir),
            ("--package", opts.package),
            ("--data-backup", opts.data_backup),
            ("--success-marker", opts.success_marker),
        ):
            if not Path(path).is_absolute():
                problems.append(f"{label} 必须是绝对路径：{path}（需求 §45）")
        if opts.platform not in archive.PLATFORMS:
            problems.append(f"--platform 只能是 {list(archive.PLATFORMS)}：{opts.platform!r}")
        if not str(opts.version).strip():
            problems.append("--version 不能为空（成功标记要按版本校验，需求 §59）")
        if not str(opts.token).strip():
            problems.append("--token 不能为空（成功标记要按 token 校验，需求 §59）")
        if opts.sha256:
            from mangaproof.update.checksum import normalize_sha256

            try:
                normalize_sha256(opts.sha256)
            except ValueError as exc:
                problems.append(f"--sha256 不合法：{exc}")
        if not opts.install_dir.is_dir():
            problems.append(f"安装目录不存在：{opts.install_dir}（需求 §38）")
        if not opts.package.is_file():
            problems.append(f"更新包不存在：{opts.package}（需求 §53）")
        try:
            validate_rules()
        except Exception as exc:  # InvalidRule
            problems.append(f"用户数据白名单非法：{exc}（需求 §47）")
        if problems:
            raise InstallerFailure(ExitCode.VALIDATION, "；".join(problems))

        main_rel = archive.install_main_rel(opts.platform)
        if not (opts.install_dir / main_rel).is_file():
            self.reporter.log(
                f"警告：现有安装目录内未找到 {main_rel}（仍按 §54 整体替换）"
            )
        if not opts.sha256:
            self.reporter.log("警告：未提供 --sha256，本次不做哈希校验（需求 §53）")

        self._elevation_required = bool(self.rt.needs_elevation(opts.install_dir))
        if self._elevation_required:
            self.reporter.log(
                f"安装目录父目录不可写：{opts.install_dir.parent}（需求 §39）"
            )
        self.store.save(self.state)
        self.reporter.log("参数校验通过")

    def _reexec_elevated(self) -> int:
        """需要提权时：用平台原生机制重跑自己（``--cli``），本进程不再动文件。"""
        argv = build_elevated_argv(self.options)
        self.reporter.log("需要管理员权限，正在请求平台授权（仅用于文件操作）…")
        self.store.transition(
            self.state, "VALIDATE", message="等待平台授权（提权只执行文件操作）"
        )
        elevate = self.rt.elevate or privilege.run_elevated
        try:
            code = int(elevate(argv, platform=self.options.platform))
        except privilege.PrivilegeCancelled as exc:
            self.reporter.finish(False, str(exc))
            return int(ExitCode.CANCELLED)
        except privilege.PrivilegeUnavailable as exc:
            self.reporter.log(str(exc))
            self.reporter.finish(False, str(exc))
            return int(ExitCode.VALIDATION)
        except privilege.PrivilegeError as exc:
            self.reporter.log(str(exc))
            self.reporter.finish(False, str(exc))
            return int(ExitCode.VALIDATION)
        self.reporter.finish(code == 0, "" if code == 0 else f"提权安装退出码 {code}")
        return code

    # -- 等待主程序退出（§46） ------------------------------------------ #
    def _wait_parent(self) -> None:
        """等待主程序退出：pid 确认 + **进程名复核**。

        只看 pid 不够（2026-09-24 实测修正），两种反向情况都会出事：

        - pid 被系统复用给别的进程 → ``alive(pid)`` 永远为真，只能干等到超时
          （按名字查一次就能认出来：主程序确实不在了 → 放行）；
        - pid 没了但主程序还在跑（换了 pid / 另有实例）→ 会**在程序运行时替换它的
          文件**，所以 pid 退出后必须再确认"没有任何叫这个名字的进程"。
        """
        pid = int(self.options.parent_pid)
        if pid <= 0:
            return
        name = self._main_process_name()
        deadline = self.rt.now() + self.rt.parent_timeout
        self._item(f"主程序 pid={pid}", "等待退出")
        self.reporter.progress(0, -1)

        while self.rt.ops.alive(pid):
            if name and self.rt.ops.exe_matches(pid, name) is False:
                # 这个 pid 已经不属于主程序了。三种可能，都靠"按名字查一次"区分：
                #   · 查得到 → 主程序换了 pid 还在跑（继续等它，别去动它的文件）；
                #   · 查得到且为空 → pid 只是被系统复用，主程序确实已经退出；
                #   · 查不了（None）→ 保守起见继续等这个 pid，直到超时。
                found = self.rt.ops.find_running(name, exclude={os.getpid()})
                if found:
                    self.reporter.log(
                        f"pid={pid} 已不是 {name}，但检测到 {name} 仍在运行"
                        f"（pid={found[0]}），继续等待它退出"
                    )
                    break
                if found is not None:
                    self.reporter.log(
                        f"pid={pid} 已被系统分配给其它进程（不是 {name}），"
                        "视为主程序已退出"
                    )
                    break
            if self.rt.now() >= deadline:
                raise InstallerFailure(
                    ExitCode.VALIDATION,
                    f"等待主程序退出超时（pid={pid}，{self.rt.parent_timeout:.0f} 秒）",
                )
            self.rt.sleep(self.rt.poll_interval)

        # pid 退出（或已确认换人）之后：**再按进程名确认一次**。只看 pid 会在
        # "主程序换了 pid / 还有另一个实例"时带着运行中的程序去替换文件。
        while name:
            found = self.rt.ops.find_running(name, exclude={os.getpid()})
            if found is None:
                self.reporter.log(f"无法枚举进程（ps 不可用），跳过 {name} 的名称复核")
                break
            if not found:
                break
            if self.rt.now() >= deadline:
                raise InstallerFailure(
                    ExitCode.VALIDATION,
                    f"仍有 {name} 进程在运行（pid={found[0]}）："
                    "请先退出该程序，再重新发起更新",
                )
            self._item(f"{name}（pid={found[0]}）", "等待退出")
            self.rt.sleep(self.rt.poll_interval)
        self.reporter.log(f"主程序（pid={pid}）已退出，开始安装")

    def _main_process_name(self) -> str:
        """主程序的可执行文件名（按平台取；取不到 = 空串，表示不做名称校验）。"""
        try:
            return Path(archive.install_main_rel(self.options.platform)).name
        except archive.ArchiveError:      # pragma: no cover - VALIDATE 已挡住非法平台
            return ""

    # -- BACKUP_DATA（§49） --------------------------------------------- #
    def _backup_data(self) -> None:
        self._phase("BACKUP_DATA")
        try:
            report = backup.backup_user_data(
                self.options.install_dir,
                self.options.data_backup,
                on_item=lambda rel: self._item(rel, "备份"),
                on_progress=self.reporter.progress,
            )
        except backup.BackupError as exc:
            raise InstallerFailure(ExitCode.BACKUP, str(exc)) from exc
        self.reporter.log(f"已备份用户数据 {report.copied} 项（复制而非移动，需求 §49）")
        if report.skipped:
            self.reporter.log("白名单中不存在的项：" + "、".join(report.skipped))

    # -- VERIFY_PACKAGE（§53） ------------------------------------------ #
    def _verify_package(self) -> None:
        self._phase("VERIFY_PACKAGE")
        try:
            result = verify_mod.verify_package(
                self.options.package,
                platform=self.options.platform,
                expected_sha256=self.options.sha256,
                on_item=lambda rel: self._item(rel, "校验"),
                on_progress=self.reporter.progress,
            )
        except verify_mod.VerifyError as exc:
            raise InstallerFailure(ExitCode.VERIFY, str(exc)) from exc
        self.state.package_sha256 = result.sha256
        self.store.save(self.state)
        self.reporter.log(
            f"更新包校验通过：{result.package.name}"
            f"（{result.size} 字节，{result.info.member_count} 个条目）"
        )

    # -- RENAME_OLD（§54/§55） ------------------------------------------ #
    def _rename_old(self) -> None:
        self._phase("RENAME_OLD")
        install = self.options.install_dir
        old = Path(self.state.old_dir)
        if old.exists():
            self._item(old.name, "删除残留")
            if not self.rt.remove_tree(
                old, retries=self.rt.retries, delay=self.rt.retry_delay,
                sleep=self.rt.sleep,
            ):
                raise InstallerFailure(
                    ExitCode.REPLACE, f"无法删除残留的旧版本目录：{old}"
                )
        self._item(f"{install.name} → {old.name}", "重命名")
        self.reporter.progress(0, -1)
        try:
            # 带重试：Windows 的 WinError 32 大多是瞬时占用（杀毒/索引器/句柄
            # 未完全释放），与删除路径保持同样的五次重试语义
            self.rt.replace_path(
                install, old,
                retries=self.rt.retries, delay=self.rt.retry_delay,
                sleep=self.rt.sleep,
            )
        except OSError as exc:
            raise InstallerFailure(
                ExitCode.REPLACE, f"重命名安装目录失败：{install} → {old}（{exc}）"
            ) from exc
        self.state.renamed_old = True
        # 立刻落盘：此后若安装器被杀，靠这个标记才能判断"更新未完成"（§64）
        self.store.save(self.state)

    # -- EXTRACT（§51/§52/§54） ----------------------------------------- #
    def _extract(self) -> None:
        self._phase("EXTRACT")
        install = self.options.install_dir
        parent = install.parent
        # 解压目标必须先清空：残留目录（上次失败/用户留下的同名目录）会被"合并"，
        # 里面的软链接还能把后续解压引到目标目录之外（需求 §52 的目标目录逃逸）。
        target_root = parent / archive.app_root_name(self.options.platform)
        if target_root.exists() or target_root.is_symlink():
            self._item(target_root.name, "清理解压目标残留")
            if not self.rt.remove_tree(
                target_root, retries=self.rt.retries, delay=self.rt.retry_delay,
                sleep=self.rt.sleep,
            ):
                raise InstallerFailure(
                    ExitCode.EXTRACT, f"无法清理解压目标残留目录：{target_root}"
                )
        try:
            result = archive.safe_extract(
                self.options.package,
                parent,
                platform=self.options.platform,
                on_item=lambda rel: self._item(rel, "解压"),
                on_progress=self.reporter.progress,
            )
        except archive.ArchiveError as exc:
            raise InstallerFailure(ExitCode.EXTRACT, str(exc)) from exc
        extracted_root = parent / result.info.root_dir_name
        if extracted_root != install:
            # 归档顶层名与安装目录名不一致（用户重命名过安装目录）：改名就位
            self._item(f"{extracted_root.name} → {install.name}", "改名")
            try:
                if install.exists():  # 理论上已被 RENAME_OLD 腾空
                    self.rt.remove_tree(install, retries=1, sleep=self.rt.sleep)
                self.rt.replace_path(
                    extracted_root, install,
                    retries=self.rt.retries, delay=self.rt.retry_delay,
                    sleep=self.rt.sleep,
                )
            except OSError as exc:
                raise InstallerFailure(
                    ExitCode.EXTRACT,
                    f"新版本就位失败：{extracted_root} → {install}（{exc}）",
                ) from exc
        try:
            entry = verify_mod.verify_installed(
                install,
                self.options.platform,
                on_item=lambda rel: self._item(rel, "确认主程序"),
            )
        except verify_mod.VerifyError as exc:
            raise InstallerFailure(ExitCode.EXTRACT, str(exc)) from exc
        self.state.child_pid = 0
        self.store.save(self.state)
        self.reporter.log(
            f"解压完成：{result.extracted} 个条目（跳过 {len(result.skipped)} 个），"
            f"主程序 {entry.name}"
        )

    # -- RESTORE_DATA（§56） -------------------------------------------- #
    def _restore_data(self) -> None:
        self._phase("RESTORE_DATA")
        try:
            report = backup.restore_user_data(
                self.options.data_backup,
                self.options.install_dir,
                on_item=lambda rel: self._item(rel, "恢复"),
                on_progress=self.reporter.progress,
            )
        except backup.BackupError as exc:
            raise InstallerFailure(ExitCode.RESTORE, str(exc)) from exc
        self.reporter.log(f"已恢复用户数据 {report.copied} 项（需求 §56）")

    # -- LAUNCH_NEW（§57） ---------------------------------------------- #
    def _launch_argv(self) -> tuple[list[str], str | None]:
        """新版启动命令行（**跨进程契约**，与主程序侧一致）。

        Windows/Linux：``[新版可执行文件, --update-token … --success-marker …
        --update-version …]``；macOS：``/usr/bin/open -n -a <MangaProof.app> --args …``。
        """
        opts = self.options
        args = [
            state_mod.ARG_TOKEN, opts.token,
            state_mod.ARG_MARKER, str(opts.success_marker),
            state_mod.ARG_VERSION, opts.version,
        ]
        if opts.platform == "macos":
            return (
                ["/usr/bin/open", "-n", "-a", str(opts.install_dir), "--args", *args],
                None,
            )
        entry = opts.install_dir / archive.install_main_rel(opts.platform)
        return ([str(entry), *args], str(opts.install_dir))

    def _launch_new(self) -> None:
        self._phase("LAUNCH_NEW")
        opts = self.options
        marker = Path(opts.success_marker)
        if marker.exists():
            # 清掉可能残留的标记：否则上一次更新的标记会被误判成本次成功（§59）
            self.reporter.log(f"清除残留的成功标记：{marker}")
            state_mod.remove_marker(marker)
            self.state.token = opts.token  # 保证随状态落盘的 token 是本次的
        argv, cwd = self._launch_argv()
        env = privilege.strip_pyinstaller_env()
        self._item(os.path.basename(argv[0]), "启动新版本")
        self.reporter.progress(0, -1)
        user_kwargs = dict(self.rt.user_kwargs)
        try:
            pid = self.rt.ops.spawn(argv, cwd=cwd, env=env, **user_kwargs)
        except OSError as exc:
            if user_kwargs:  # 降权启动失败：退回普通启动（不要因此判更新失败）
                self.reporter.log(f"以降权身份启动新版失败（{exc}），改为普通启动")
                try:
                    pid = self.rt.ops.spawn(argv, cwd=cwd, env=env)
                except OSError as exc2:
                    raise InstallerFailure(
                        ExitCode.LAUNCH, f"新版本无法启动：{argv[0]}（{exc2}）（需求 §62）"
                    ) from exc2
            else:
                raise InstallerFailure(
                    ExitCode.LAUNCH, f"新版本无法启动：{argv[0]}（{exc}）（需求 §62）"
                ) from exc
        self.state.child_pid = int(pid)
        self.store.save(self.state)
        self.reporter.log(f"新版本已启动（pid={pid}），等待成功标记（§59）")

    # -- WAIT_SUCCESS（§59/§60/§61） ------------------------------------ #
    def _wait_success(self) -> None:
        """等待新版写成功标记（§59/§60/§61）。

        **"新版提前退出"的判据是"按进程名找不到主程序"，不是"跟踪的 pid 消失"**
        （2026-09-24 实测修正）。macOS 上 ``_launch_argv`` 用 ``open -n -a`` 启动
        .app，``spawn`` 返回的是 ``open`` 自己的 pid —— 它一定会立刻退出，而真正
        的 app 由 LaunchServices 稍后拉起、pid 完全不同。旧实现看到 pid 消失就
        立刻判"新版已退出但没写标记"并回滚，于是每次 macOS 更新都被误判失败、
        而且在 app 正在启动时把它的目录删掉了。

        现在的顺序（每次 pid 消失时走一遍）：

        1. 退出前可能刚写完标记 → 最后再确认一次（§60 原有语义）；
        2. 按进程名找主程序：找到 → 记下新 pid 继续等标记（**不判失败**）；
        3. 找不到 → 进入 :data:`RELOCATE_TIMEOUT` 宽限期（``open`` 退出到 app
           出现之间本来就有几百毫秒到几秒），宽限期内一直找不到才判失败；
        4. 查不了（``ps`` 不可用）→ 当作"可能还活着"，等满 ``success_timeout``
           再按超时处理，绝不因为查不到而早判失败。

        等待期间**不结束主程序**；只有真到了超时（§59 的 60 秒）才按既有语义
        结束它并回滚（需求方 2026-09-24 决策：超时维持现状）。
        """
        self._phase("WAIT_SUCCESS")
        opts = self.options
        marker = Path(opts.success_marker)
        name = self._main_process_name()
        deadline = self.rt.now() + self.rt.success_timeout
        relocate_deadline: float | None = None
        self._item(
            f"等待新版本写入成功标记（最长 {self.rt.success_timeout:.0f} 秒）", "等待"
        )
        while True:
            check = state_mod.check_marker(marker, opts.token, opts.version)
            if check.valid:
                self.reporter.log(f"收到有效成功标记：{marker}")
                return
            if check.exists and check.reason != self._last_marker_reason:
                self._last_marker_reason = check.reason
                self.reporter.log(f"暂不接受成功标记：{check.reason}")

            pid = int(self.state.child_pid)
            child_alive = pid > 0 and self.rt.ops.alive(pid)
            if not child_alive and pid > 0:
                # 新版已退出：退出前可能刚写完标记，最后再确认一次（§60）
                final = state_mod.check_marker(marker, opts.token, opts.version)
                if final.valid:
                    self.reporter.log(f"收到有效成功标记：{marker}")
                    return

            if child_alive:
                relocate_deadline = None
            else:
                now = self.rt.now()
                if now >= self._next_lookup_at:
                    self._next_lookup_at = now + max(0.0, self.rt.lookup_interval)
                    found = (
                        self.rt.ops.find_running(name, exclude={os.getpid()})
                        if name else None
                    )
                    if found:
                        self._adopt_child(found[0])
                        relocate_deadline = None
                    elif found is None:
                        if relocate_deadline is None:
                            self.reporter.log(
                                f"pid={pid} 已退出，且无法枚举进程（ps 不可用）："
                                "只能等满超时再判定"
                            )
                        relocate_deadline = None
                    elif relocate_deadline is None:
                        relocate_deadline = now + self.rt.relocate_timeout
                        self.reporter.log(
                            f"跟踪的进程（pid={pid}）已退出，正在按名称查找主程序"
                            f"（最多 {self.rt.relocate_timeout:.0f} 秒）"
                        )
                if relocate_deadline is not None and now >= relocate_deadline:
                    self._item(f"新版本（{name or pid}）", "已退出且未写成功标记")
                    raise InstallerFailure(
                        ExitCode.SUCCESS_TIMEOUT,
                        f"新版本进程已退出且没有写入成功标记（等待 {name or pid} "
                        f"{self.rt.relocate_timeout:.0f} 秒未出现），立即回滚（需求 §60）",
                    )

            if self.rt.now() >= deadline:
                if pid > 0:
                    self.rt.ops.terminate(pid)  # 超时：尽力结束挂住的新版
                raise InstallerFailure(
                    ExitCode.SUCCESS_TIMEOUT,
                    f"等待成功标记超时（{self.rt.success_timeout:.0f} 秒），"
                    "更新失败（需求 §59/§62）",
                )
            self.rt.sleep(self.rt.poll_interval)

    def _adopt_child(self, pid: int) -> None:
        """把跟踪目标换成"按进程名找到的"那个 pid（同步状态文件，便于事后排查）。"""
        old = int(self.state.child_pid)
        if pid == old:
            return
        self.reporter.log(
            f"pid={old} 已退出，主程序以 pid={pid} 在运行，继续等待成功标记（§59）"
        )
        self._item(f"主程序（pid={pid}）", "继续等待")
        self.state.child_pid = int(pid)
        self.store.save(self.state)

    # -- CLEANUP（§61） ------------------------------------------------- #
    def _cleanup(self) -> None:
        self._phase("CLEANUP")
        opts = self.options
        # 先把安装日志搬进程序目录 logs/：它是"这次更新到底做了什么"的唯一记录，
        # 而它此刻正躺在马上要被删掉的更新包目录里。
        self._preserve_installer_log()
        # 顺序照 §61：删 .old → 删数据备份 → 删更新包 → 删安装器临时目录 → 删标记
        old = Path(self.state.old_dir)
        self._item(old.name, "删除旧版本")
        if not self.rt.remove_tree(
            old, retries=self.rt.retries, delay=self.rt.retry_delay, sleep=self.rt.sleep
        ):
            self.reporter.log(f"警告：旧版本目录删除失败，可手动删除：{old}")
        self._item(Path(opts.data_backup).name, "删除数据备份")
        if not backup.remove_backup_dir(opts.data_backup):
            self.reporter.log(f"警告：数据备份目录删除失败：{opts.data_backup}")
        self._item(opts.package.name, "删除更新包")
        try:
            Path(opts.package).unlink(missing_ok=True)
        except OSError as exc:
            self.reporter.log(f"警告：更新包删除失败：{opts.package}（{exc}）")
        # 更新包目录里剩下的东西（含安装日志）一并清掉：这里没有"正在运行的
        # 文件"，删不掉才是异常，所以用带重试的 remove_tree 并把失败如实报出来。
        self._cleanup_package_dir()
        self._cleanup_installer_temp()
        self._item(Path(opts.success_marker).name, "删除成功标记")
        state_mod.remove_marker(opts.success_marker)

    def _preserve_installer_log(self) -> None:
        """把安装日志复制进程序目录 ``logs/``（更新包目录随后会被整个删掉）。

        命名 ``installer-<目标版本>-<token前8位>.log``：一次更新一个文件，
        不会互相覆盖，也不会混进 ``mangaproof.log`` 的轮转序列里。
        复制失败只是少一份诊断材料，**绝不能因此让更新判失败**。
        """
        source = self.rt.log_path
        if source is None or not Path(source).is_file():
            return
        install = Path(self.options.install_dir)
        target_dir = logs_dir(install)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / (
                f"installer-{safe_token(self.options.version)}"
                f"-{safe_token(self.options.token)[:8]}.log"
            )
            shutil.copy2(source, target)
        except OSError as exc:
            self.reporter.log(f"警告：安装日志留存失败（{exc}）：{source}")
            return
        self._item(f"logs/{target.name}", "留存安装日志")

    def _cleanup_package_dir(self) -> None:
        """清掉更新包目录（更新包本体 + 数据备份 + 成功标记 + 安装日志）。

        安全约束（**别删错东西**）：只删"更新临时目录下那个固定名字的目录"，
        即 ``TEMP|CACHE/MangaProof-update-package``。绝不照搬 ``--package`` 的父目录
        —— 单测/嵌入调用时包可能就放在安装目录里，照搬会把整个程序删掉。
        """
        canonical = platform_dirs.update_package_dir(create=False)
        if canonical.name != platform_dirs.PACKAGE_DIRNAME:
            return                      # 契约变了就宁可不删
        if not canonical.is_dir():
            return
        # 更新包本体此刻应该已经在里面被删过一次了；这里连同日志/备份/标记一起收尾
        if self.rt.remove_tree(
            canonical, retries=self.rt.retries, delay=self.rt.retry_delay,
            sleep=self.rt.sleep,
        ):
            self._item(canonical.name, "删除更新包目录")
        else:
            self.reporter.log(f"警告：更新包目录删除失败，可手动删除：{canonical}")

    def _cleanup_installer_temp(self) -> None:
        """删除安装器临时目录（§40/§61）——**尽力而为**，删不掉不是失败。

        Windows 上这个 exe 删不掉自己：running onefile 程序的可执行文件带
        FILE_SHARE_DELETE 以外的共享模式（而且它删自己的目录），
        :func:`rollback.remove_tree` 重试几次后会登记"重启后删除"；
        实在删不掉也无所谓 —— 下一次更新会把新副本覆盖过去（同名同目录），
        目录永远不会越堆越多。这里只在真的没删掉时把原因写清楚，
        免得用户看到"未删除"以为更新失败了。
        """
        if not getattr(sys, "frozen", False):
            return
        exe = Path(sys.executable)
        temp_dir = exe.parent
        if not temp_dir.name.startswith(INSTALLER_TEMP_PREFIX):
            return
        self._item(temp_dir.name, "删除安装器临时目录")
        removed = self.rt.remove_tree(
            exe, retries=2, delay=self.rt.retry_delay, sleep=self.rt.sleep
        )
        removed = self.rt.remove_tree(
            temp_dir, retries=2, delay=self.rt.retry_delay, sleep=self.rt.sleep
        ) and removed
        if not removed:
            self.reporter.log(
                f"注：安装器自身的副本暂时无法删除（Windows 上正在运行的 exe 不能"
                f"删自己），下次更新会直接覆盖：{temp_dir}"
            )

    # -- 失败与回滚（§62/§63） ------------------------------------------ #
    def _handle_failure(self, failure: InstallerFailure) -> int:
        message = str(failure)
        self.reporter.log(f"失败（退出码 {int(failure.code)}）：{message}")
        self.state.error = message
        self.state.rollback_reason = message
        self._phase("ROLLBACK", "正在回滚到旧版本…")
        code = int(failure.code)
        if not Path(self.state.install_dir).is_absolute():
            # 参数阶段就失败了：绝不能拿相对路径去"回滚"（会相对 cwd 乱动文件）
            self.reporter.log("安装参数非法，未触碰任何文件，无需回滚")
            self._phase("FAILED", message)
            self.reporter.finish(False, message)
            return code
        try:
            result = rollback.rollback(
                self.state,
                on_item=lambda rel: self._item(rel, "回滚"),
                on_progress=self.reporter.progress,
                sleep=self.rt.sleep,
                retries=self.rt.retries,
                retry_delay=self.rt.retry_delay,
            )
        except rollback.RollbackError as exc:
            self.reporter.log(f"回滚失败：{exc}")
            self.state.error = f"{message}；回滚失败：{exc}"
            self._phase("FAILED", "回滚失败，安装目录可能不完整")
            self.reporter.finish(False, self.state.error)
            return int(ExitCode.ROLLBACK_FAILED)
        self.reporter.log(f"回滚结果：{result.reason}")
        for action in result.actions:
            self.reporter.log(f"  · {action}")
        self._phase("FAILED", message)
        self.reporter.finish(False, message)
        return code


def run_install(
    options: InstallerOptions,
    *,
    reporter: Reporter | None = None,
    runtime: Runtime | None = None,
) -> int:
    """便捷入口：跑一次安装并返回退出码。"""
    rt = runtime or Runtime()
    if reporter is not None:
        rt.reporter = reporter
    return Installer(options, rt).run()


def default_reporter() -> Reporter:
    """控制台降级用的 reporter（GUI 由 ``ui.run_with_ui`` 提供）。"""
    from updater.ui import ConsoleReporter, MultiReporter

    return MultiReporter(ConsoleReporter(), LoggingReporter())


__all__ = [
    "INSTALLER_TEMP_PREFIX",
    "LOOKUP_INTERVAL",
    "PARENT_EXIT_TIMEOUT",
    "POLL_INTERVAL",
    "RELOCATE_TIMEOUT",
    "SUCCESS_TIMEOUT",
    "ExitCode",
    "Installer",
    "InstallerFailure",
    "InstallerOptions",
    "ProcessOps",
    "Runtime",
    "build_elevated_argv",
    "default_reporter",
    "rerun_argv_from_process",
    "resolve_state_path",
    "run_install",
]
