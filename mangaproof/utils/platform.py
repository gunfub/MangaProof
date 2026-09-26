# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""平台判定（供"平台专有行为"使用）。

与 `utils/shutdown.py::is_android()` 的区别（重要，务必分清用途）：

- `is_android()` 是"三重判定、任一命中即算 Android"，其中包含 `ANDROID_ROOT`
  环境变量。用于**退出方式**这类低风险分支没问题，但环境变量会"泄漏"：
  桌面机器只要从容器 / proot / Android 工具链会话里继承了 `ANDROID_ROOT`，
  就会被误判成 Android（实测：Linux 桌面 + `ANDROID_ROOT=/system` →
  `is_android()` 返回 True）。
- 本模块的 `is_android_strict()` **只认编译期平台标识，不看任何环境变量**：
  Qt 的 `QOperatingSystemVersion::currentType()` 是 constexpr + `#if defined(Q_OS_*)`
  【源码】qtbase/src/corelib/global/qoperatingsystemversion.h：

      #if defined(Q_OS_WIN)       return Windows;
      #elif defined(Q_OS_MACOS)    return MacOS;
      ...
      #elif defined(Q_OS_ANDROID)  return Android;
      #else                        return Unknown;   // 桌面 Linux 走这里

  因此 Windows / Linux / macOS 上**永远**返回 False，运行期无法被污染
  （PySide6 的 `OSType` 甚至没有 Linux 成员，Linux 桌面必然是 Unknown）。

用途约束：任何"只在 Android 上生效、误判会伤害桌面"的功能，必须用本模块的
判定。当前使用者：

- `config/settings.py`：Android 专有界面缩放（QT_SCALE_FACTOR）；
- `main.py`：Android 上禁用 Qt 原生菜单栏路径（找回 文件/设置/关于）。

除平台判定外，本模块也收留"**运行形态**"判定（源码运行 vs 打包产物），
见 :func:`is_source_run` —— 它与平台判定同属"本次运行的环境事实"，
且 `config/paths.py` / `console.py` 已在各自模块里就同一事实做过分流，
集中在这里可以避免第四处重复实现。
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("mangaproof.utils.platform")


def qt_os_type_name() -> str:
    """Qt 判定出的当前操作系统类型名（如 "Android" / "Windows" / "Unknown"）。

    仅用于日志与诊断；取不到时返回 "unknown"。
    """
    try:
        from PySide6.QtCore import QOperatingSystemVersion

        return QOperatingSystemVersion.currentType().name
    except Exception:  # pragma: no cover - 仅在 Qt 缺失/异常时触发
        log.debug("读取 QOperatingSystemVersion 失败", exc_info=True)
        return "unknown"


def is_android_strict() -> bool:
    """是否运行在 Android 上（严格判定，用于平台专有行为）。

    判据只有两条，均为编译期/解释器级事实，**不含任何环境变量**：

    1. `QOperatingSystemVersion.currentType() == OSType.Android`
       —— Android 版 Qt 的编译期常量（桌面三平台分别是 Windows / MacOS / Unknown）；
    2. `sys.platform == "android"`
       —— 官方 Android CPython（p4a 编译的 3.11 报 "linux"，两条互为补充）。

    任何异常或未知情况一律返回 False（fail-safe：宁可 Android 上不生效，
    也绝不在桌面上误生效）。
    """
    if sys.platform == "android":
        return True
    try:
        from PySide6.QtCore import QOperatingSystemVersion

        return (
            QOperatingSystemVersion.currentType()
            == QOperatingSystemVersion.OSType.Android
        )
    except Exception:  # pragma: no cover - 仅在 Qt 缺失/异常时触发
        log.debug("Qt 平台类型判定失败，按非 Android 处理", exc_info=True)
        return False


def is_source_run(*, frozen: bool | None = None) -> bool:
    """是否以**源码方式**运行（而非 PyInstaller 打包产物）。

    判据只有一条，与 `config/paths.py` 的 `get_app_dir()`、
    `console.py` 的 `apply_console_visibility()` **同源**：PyInstaller 冻结态会
    设置 `sys.frozen`，直接跑 `python main.py` 不会。

    为什么把 ``frozen`` 做成可传入而不是直接读 `sys`：照
    `console.decide_console_hidden()` 的做法，把运行事实当参数传进来，单测就能
    直接枚举真值表，不必去 patch 全局 `sys`（见 tests/test_smoke.py 的同名用例）。

    用途约束（重要）：本判定只用于**与打包形态强相关**的策略分支。当前唯一
    使用者是更新页的「源码运行不提供更新包下载」——更新包是打包好的二进制
    发行版（.zip / .tar.gz / .apk），对源码树没有意义，正确的更新方式是 git pull。

    与 Android 的关系（**刻意不特判**，勿当疏漏）：p4a 打的 APK 不是 PyInstaller，
    不设 `sys.frozen`，因此本函数在 Android 上会返回 True。Android 的更新流程是
    **尚未完成的独立工作流**（`update/platform/android.py`：第一版隐藏入口、
    没有独立安装器，`launch_installer()` 直接抛 NotImplementedError），
    按需求方决策留待 Android 分支统一处理，本函数不在这里替它做特判。
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    return not frozen
