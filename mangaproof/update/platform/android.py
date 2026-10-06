# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Android 专有更新逻辑（需求 §65、§66、§85）。

Android **不使用独立安装器**，也不做 ``.old`` 替换（需求 §65）：

.. code-block:: text

    检查更新 → 下载更新 → 校验（下载时）→ 写系统 Download → 交给系统包管理器

因此本模块只提供"下载目录/安装目录"等最小信息；
:func:`launch_installer` 直接抛 :class:`NotImplementedError`，避免上层误以为
Android 也能走桌面那套"启动安装器替换文件"的流程。

真正调起系统安装器的动作在**主程序侧**（`MainWindow._install_apk_via_system`
→ `QDesktopServices.openUrl`）：Qt 的 Android 平台层会用清单里的 FileProvider
把 ``file://`` 换成可共享的 ``content://`` 再发 ``ACTION_VIEW``，
所以这里**不需要**任何 JNI / pyjnius（详见 §85.2）。
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("mangaproof.update.platform.android")


def install_dir() -> Path:
    """Android 的程序目录（应用私有目录，见 config/paths.py 的 get_app_dir）。"""
    from mangaproof.config import paths

    return paths.get_app_dir()


def parent_dir() -> Path:
    return install_dir().parent


def needs_elevation() -> bool:
    """Android 没有"提权"概念：应用私有目录天然可写。"""
    return False


def launch_installer(exe: Path, args: list[str], *, elevate: bool) -> int:
    raise NotImplementedError(
        "Android 不使用独立安装器（需求 §65）：应用 APK 由系统安装器安装"
    )


def download_dir() -> Path:
    """系统 Download 目录（需求 §65/§85：APK 落到这里再交给系统安装器）。

    调用方是更新页的 `UpdateDialog._download_dir()`（2026-09-26 起真正接线；
    在此之前本函数一直没有调用者）。

    Android 11+ 上 ``/storage/emulated/0/Download`` 需要
    ``MANAGE_EXTERNAL_STORAGE``（清单已声明，见 scripts/android/build_android.py）；
    未授予时下载会在建目录/写文件处失败，`downloader` 会给出带权限指引的提示。
    """
    return Path("/storage/emulated/0/Download")
