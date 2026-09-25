# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""``tests/test_updater_*.py`` 的公共构造器（**本身不含用例**）。

全部离线、不依赖 tkinter、不真的提权：

- 造"假安装目录 / 假更新包 / 假新版程序"（POSIX 下用 ``/bin/sh`` 脚本充当
  新版主程序，它按跨进程契约解析 ``--update-token/--success-marker/--update-version``
  并原子写 marker）；
- 造 tar.gz / zip 归档时**逐条指定权限位与条目类型**，这样才测得出"保留 exec 位
  与软链接""设备文件必须被拒"这类关键行为。
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from updater.installer import ProcessOps  # noqa: E402
from updater.ui import Reporter  # noqa: E402

LINUX_ROOT = "MangaProof"
MACOS_ROOT = "MangaProof.app"
WINDOWS_ROOT = "MangaProof"


class ScriptedLookupOps(ProcessOps):
    """进程探测替身：**不扫描测试机上的真实进程表**。

    "按进程名找主程序"（macOS 的 ``open -n -a`` 会让跟踪的 pid 秒退）必须能在单测里
    被精确编排，否则结果会取决于开发机上有没有跑着 MangaProof：

    - :meth:`find_running` 按 ``lookup`` 序列逐个返回（用完后重复最后一个元素）；
      元素可以是 ``[pid, …]``（找到）、``[]``（确认没有）、``None``（查不了）；
    - :meth:`exe_matches` 默认 ``True``（Linux/Windows 语义：spawn 出来的就是主程序），
      用 ``exe_name=`` 可以模拟"pid 已被系统复用给别的进程"。

    进程存活判定仍走真实的 :meth:`ProcessOps.alive`（持有 Popen 时用 ``poll()``）。
    """

    def __init__(
        self,
        lookup: list[list[int] | None] | None = None,
        *,
        exe_name: str | None = None,
        launcher_style: bool = False,
    ) -> None:
        super().__init__()
        self.lookup = list(lookup or [[]])
        self.exe_name = exe_name
        #: True = 模拟 macOS 的 ``open``：**spawn 出来的那个 pid 一律当已退出**，
        #: 只有"按进程名查到"的 pid 才算活着（等价于"启动器秒退、app 换 pid"）。
        self.launcher_style = launcher_style
        self.live: set[int] = set()
        self.lookup_calls = 0

    def alive(self, pid: int) -> bool:
        if self.launcher_style:
            return pid in self.live
        return super().alive(pid)

    def find_running(self, name: str, *, exclude: set[int] | None = None):
        self.lookup_calls += 1
        index = min(self.lookup_calls, len(self.lookup)) - 1
        value = self.lookup[index]
        if callable(value):
            value = value()          # 动态查（例如从 pid 文件里读真正的 app pid）
        result = None if value is None else list(value)
        if self.launcher_style and result:
            self.live.update(result)
        return result

    def exe_matches(self, pid: int, name: str) -> bool | None:
        if self.exe_name is None:
            return True
        return self.exe_name == name


#: 记录所有 UI 事件的 reporter（断言"三层信息"都真的被上报过）
class RecordingReporter(Reporter):
    def __init__(self) -> None:
        self.phases: list[str] = []
        self.items: list[tuple[str, str]] = []
        # 注意：不能叫 progress —— 会盖住 Reporter.progress 方法（安装器会回调它）
        self.progress_calls: list[tuple[int, int]] = []
        self.logs: list[str] = []
        self.result: tuple[bool, str] | None = None

    def phase(self, phase: str) -> None:
        self.phases.append(phase)

    def item(self, rel_path: str, action: str = "") -> None:
        self.items.append((action, rel_path))

    def progress(self, done: int, total: int) -> None:
        self.progress_calls.append((done, total))

    def log(self, line: str) -> None:
        self.logs.append(line)

    def finish(self, ok: bool, message: str = "") -> None:
        self.finished = True
        self.result = (ok, message)

    @property
    def item_paths(self) -> list[str]:
        return [rel for _action, rel in self.items]


@dataclass
class ArchiveEntry:
    """归档内的一条目（测试用；``kind`` ∈ file/dir/sym/chr）。"""

    name: str
    kind: str = "file"
    mode: int = 0o644
    data: bytes = b""
    link: str = ""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 归档构造
# --------------------------------------------------------------------------- #


def write_tar_gz(path: Path, entries: list[ArchiveEntry]) -> Path:
    """写 tar.gz（权限位/类型完全由调用方指定，不做任何"善意修正"）。"""
    with tarfile.open(path, "w:gz") as tf:
        for entry in entries:
            info = tarfile.TarInfo(entry.name)
            info.mode = entry.mode
            info.uid = os.getuid() if hasattr(os, "getuid") else 0
            info.gid = info.uid
            info.uname = info.gname = ""
            info.mtime = 1700000000
            if entry.kind == "dir":
                info.type = tarfile.DIRTYPE
                info.size = 0
                tf.addfile(info)
            elif entry.kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = entry.link
                info.size = 0
                tf.addfile(info)
            elif entry.kind == "chr":
                info.type = tarfile.CHRTYPE
                info.devmajor, info.devminor = 1, 3
                info.size = 0
                tf.addfile(info)
            elif entry.kind == "fifo":
                info.type = tarfile.FIFOTYPE
                info.size = 0
                tf.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                data = entry.data
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


def write_zip(path: Path, entries: list[ArchiveEntry]) -> Path:
    """写 zip（``external_attr`` 里带 Unix 权限位/文件类型，模拟 macOS 产物）。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=(2026, 1, 1, 0, 0, 0))
            if entry.kind == "dir":
                info.external_attr = ((stat.S_IFDIR | entry.mode) << 16) | 0x10
                zf.writestr(info, b"")
            elif entry.kind == "sym":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, entry.link.encode())
            elif entry.kind in ("chr", "fifo"):
                file_type = stat.S_IFCHR if entry.kind == "chr" else stat.S_IFIFO
                info.external_attr = (file_type | entry.mode) << 16
                zf.writestr(info, b"")
            else:
                info.external_attr = (stat.S_IFREG | entry.mode) << 16
                zf.writestr(info, entry.data)
    return path


# --------------------------------------------------------------------------- #
# 假"新版主程序"（POSIX sh 脚本）
# --------------------------------------------------------------------------- #

#: 正常新版：按跨进程契约解析参数 → 原子写 marker（token+version+pid+ts）
#: 另外把 marker 内容复制到 $WITNESS（构建时写死在脚本里），方便断言"确实写对了"
MAIN_SCRIPT_OK = """#!/bin/sh
# MangaProof 假新版主程序（测试用）
WITNESS="{witness}"
while [ $# -gt 0 ]; do
  case "$1" in
    --update-token) TOKEN="$2"; shift 2;;
    --success-marker) MARKER="$2"; shift 2;;
    --update-version) VERSION="$2"; shift 2;;
    *) shift;;
  esac
done
printf '{{"token":"%s","version":"%s","pid":%s,"ts":0}}\\n' "$TOKEN" "$VERSION" "$$" > "$MARKER.tmp"
mv "$MARKER.tmp" "$MARKER"
if [ -n "$WITNESS" ]; then cp "$MARKER" "$WITNESS"; fi
exit 0
"""

#: 坏新版：token 写错
MAIN_SCRIPT_BAD_TOKEN = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --success-marker) MARKER="$2"; shift 2;;
    --update-version) VERSION="$2"; shift 2;;
    *) shift;;
  esac
done
printf '{"token":"not-the-token","version":"%s","pid":%s,"ts":0}\\n' "$VERSION" "$$" > "$MARKER"
exit 0
"""

#: 坏新版：version 写错
MAIN_SCRIPT_BAD_VERSION = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --update-token) TOKEN="$2"; shift 2;;
    --success-marker) MARKER="$2"; shift 2;;
    *) shift;;
  esac
done
printf '{"token":"%s","version":"0.0.0.stale","pid":%s,"ts":0}\\n' "$TOKEN" "$$" > "$MARKER"
exit 0
"""

#: 启动即退出、不写 marker（需求 §60 的场景）
MAIN_SCRIPT_EXIT_EARLY = """#!/bin/sh
exit 3
"""

#: 一直活着但永远不写 marker（"超时"场景；安装器按 §59 结束它并回滚）
MAIN_SCRIPT_HANG = """#!/bin/sh
sleep 300
"""

#: macOS 型"启动器"假新版（等价于 ``/usr/bin/open -n -a``）：
#: 把真正的主程序丢到后台就立刻退出 —— 于是安装器 ``spawn`` 拿到的那个 pid
#: 秒退，而 app 以**另一个 pid** 在跑（2026-09-24 实测的误报失败根因）。
MAIN_SCRIPT_LAUNCHER = """#!/bin/sh
DIR=$(dirname "$0")
"$DIR/_internal/real-main" "$@" >/dev/null 2>&1 &
echo $! > "{pidfile}"
exit 0
"""


def sh_script(body: str) -> bytes:
    return body.encode("utf-8")


def main_script_ok(witness: Path | None = None, *, delay: float = 0.0) -> bytes:
    """正常新版：写 marker 后退出；``delay`` 用来让"标记还没出现"的那几轮可预期。"""
    body = sh_script(MAIN_SCRIPT_OK.format(witness=witness or ""))
    if delay > 0:
        body = body.replace(b"#!/bin/sh\n", f"#!/bin/sh\nsleep {delay}\n".encode(), 1)
    return body


def main_script_bad_token() -> bytes:
    return sh_script(MAIN_SCRIPT_BAD_TOKEN)


def main_script_bad_version() -> bytes:
    return sh_script(MAIN_SCRIPT_BAD_VERSION)


def main_script_exit_early() -> bytes:
    return sh_script(MAIN_SCRIPT_EXIT_EARLY)


def main_script_hang() -> bytes:
    return sh_script(MAIN_SCRIPT_HANG)


def main_script_launcher(pidfile: Path) -> bytes:
    """启动器型新版：需要包内另有 ``_internal/real-main``（见 :func:`launcher_extra`）。"""
    return sh_script(MAIN_SCRIPT_LAUNCHER.format(pidfile=pidfile))


def launcher_extra(real_main: bytes) -> list["ArchiveEntry"]:
    """启动器型新版所需的"真正主程序"条目（丢在 ``_internal/`` 里）。"""
    return [ArchiveEntry("MangaProof/_internal/real-main", "file", 0o755, real_main)]


# --------------------------------------------------------------------------- #
# 假安装目录 / 假更新包
# --------------------------------------------------------------------------- #

USER_SETTINGS = '{"lang": "zh-CN", "theme": "dark"}'
USER_RECENT = '["/tmp/a.mangaproof.json"]'
USER_LOG = "2026-09-22 10:00:00 INFO 启动\n"


def install_dir_name(platform: str) -> str:
    return MACOS_ROOT if platform == "macos" else LINUX_ROOT


def install_main_rel(platform: str) -> str:
    """主程序在**安装目录内**的相对路径（与 updater.archive.INSTALL_MAIN_REL 一致）。"""
    if platform == "macos":
        return "Contents/MacOS/MangaProof"
    if platform == "windows":
        return "MangaProof.exe"
    return "MangaProof"


def package_main_rel(platform: str) -> str:
    """主程序在**包内**的相对路径（相对解压父目录）。"""
    if platform == "macos":
        return "MangaProof.app/Contents/MacOS/MangaProof"
    if platform == "windows":
        return "MangaProof/MangaProof.exe"
    return "MangaProof/MangaProof"


def make_install_dir(
    base: Path,
    *,
    platform: str = "linux",
    main_content: bytes = b"#!/bin/sh\necho OLD\n",
    with_user_data: bool = True,
) -> Path:
    """造一个"旧版本安装目录"（含白名单用户数据）。

    macOS 的安装目录是 ``MangaProof.app`` 本身（需求 §38/§55）。
    """
    install = Path(base) / install_dir_name(platform)
    main = install.joinpath(*install_main_rel(platform).split("/"))
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_bytes(main_content)
    main.chmod(0o755)
    (install / "_internal").mkdir(parents=True, exist_ok=True)
    (install / "_internal" / "old-only.txt").write_text("old build\n", encoding="utf-8")
    if with_user_data:
        (install / "settings.json").write_text(USER_SETTINGS, encoding="utf-8")
        (install / "recent.json").write_text(USER_RECENT, encoding="utf-8")
        (install / "logs").mkdir(parents=True, exist_ok=True)
        (install / "logs" / "app.log").write_text(USER_LOG, encoding="utf-8")
        (install / "not-user-data.bin").write_bytes(b"do not copy\n")
    return install


def make_linux_package(
    path: Path,
    *,
    main_content: bytes | None = None,
    extra: list[ArchiveEntry] | None = None,
    main_mode: int = 0o755,
    with_installer: bool = True,
) -> Path:
    """造一个结构正确的 Linux 更新包（``MangaProof/MangaProof`` + ``_internal/``）。"""
    entries: list[ArchiveEntry] = [
        ArchiveEntry("MangaProof/", "dir", 0o755),
        ArchiveEntry("MangaProof/MangaProof", "file", main_mode,
                     main_content if main_content is not None else main_script_ok()),
        ArchiveEntry("MangaProof/_internal/", "dir", 0o755),
        ArchiveEntry("MangaProof/_internal/data.txt", "file", 0o644, b"new build\n"),
    ]
    if with_installer:
        entries.append(
            ArchiveEntry("MangaProof/MangaProof-update-installer", "file", 0o755, b"#installer\n")
        )
    if extra:
        entries.extend(extra)
    return write_tar_gz(path, entries)


def make_macos_package(path: Path, *, main_content: bytes | None = None,
                       extra: list[ArchiveEntry] | None = None) -> Path:
    """造一个结构正确的 macOS zip 包（``MangaProof.app`` + 交叉软链）。"""
    entries: list[ArchiveEntry] = [
        ArchiveEntry("MangaProof.app/", "dir", 0o755),
        ArchiveEntry("MangaProof.app/Contents/", "dir", 0o755),
        ArchiveEntry("MangaProof.app/Contents/MacOS/", "dir", 0o755),
        ArchiveEntry("MangaProof.app/Contents/MacOS/MangaProof", "file", 0o755,
                     main_content if main_content is not None else main_script_ok()),
        ArchiveEntry("MangaProof.app/Contents/Resources/", "dir", 0o755),
        ArchiveEntry("MangaProof.app/Contents/Resources/real.txt", "file", 0o644, b"real\n"),
        ArchiveEntry("MangaProof.app/Contents/Frameworks/", "dir", 0o755),
        ArchiveEntry("MangaProof.app/Contents/Frameworks/alias.txt", "sym",
                     link="../Resources/real.txt"),
    ]
    if extra:
        entries.extend(extra)
    return write_zip(path, entries)


@dataclass
class FlowEnv:
    """一次安装流程的全部路径（测试断言用）。"""

    install: Path
    package: Path
    backup: Path
    marker: Path
    status: Path
    witness: Path
    version: str = "1.1.0.alpha"
    token: str = "tok-abc123"
    platform: str = "linux"
    sha256: str = ""
    reporter: RecordingReporter = field(default_factory=RecordingReporter)
