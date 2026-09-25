# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器内置字体（MiSans）的定位与**进程内**注册（调研报告 §11.2）。

规格要求安装器与主程序用同一份字体（"字体优先 MiSans（随包 ``font/`` 分发），
缺失时按 ``Microsoft YaHei UI → Microsoft YaHei → Noto Sans CJK SC → PingFang SC``
回退"）。主程序走 Qt 的 ``QFontDatabase.addApplicationFont``；Tk 没有等价 API，
只能按平台把字体文件注册进**本进程**：

========  =================================================================  ==========================
平台       机制                                                                Tk 看到的家族名
========  =================================================================  ==========================
Windows   ``gdi32.AddFontResourceExW(..., FR_PRIVATE, 0)``                    ``MiSans Medium``
Linux     ``FcConfigAppFontAddFile(FcConfigGetCurrent(), …)``（libfontconfig） ``MiSans``
macOS     CoreText ``CTFontManagerRegisterFontsForURL(…, Process)``           ``MiSans``
========  =================================================================  ==========================

家族名为什么有两个：``MiSans-Medium.ttf`` 的 name 表里 nameID1（legacy 家族）是
``MiSans Medium``、nameID16（排版家族）是 ``MiSans``（2026-09-25 用 stdlib 解析
实测，见 ``tests/test_updater_fonts.py``）。GDI 只认 nameID1/2，于是暴露
``MiSans Medium``；CoreText / fontconfig / Qt 认 nameID16，看到 ``MiSans``。
Tk 在 Windows 上走 GDI，所以两个名字都要试（:data:`FAMILY_CANDIDATES`）。

**只做进程内注册**：不写系统字体目录、不写 ``~/.fonts``、不碰 fontconfig 缓存。
安装器是一次性程序，退出即消失；改用户环境既没必要，也躲不开 §82 的清理语义。

**粗体**：只随包 Medium 一份。Windows / macOS 由 GDI / CoreText 合成粗体，Linux 的
Xft 不合成（fontconfig 对 bold 请求仍返回 Medium，2026-09-25 实测）—— 于是 Linux 上
标题 / 阶段 / 结果三个粗体标签退化为 Medium，这是已知并接受的取舍（§11.2 有记录）。

许可：``MiSans-Medium.ttf`` 未做任何修改、随软件整体分发；许可全文随包
（``THIRD_PARTY_LICENSES.md`` 第 46 条，安装器 spec 会把 licenses/ 一并打进产物）。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

log = logging.getLogger("mangaproof.updater.fonts")

#: 随包字体文件名（与主程序 ``mangaproof/fonts.py`` 的 FONT_FILENAME 同一份）
FONT_FILENAME = "MiSans-Medium.ttf"

#: Tk 里可能出现的家族名（nameID16 → nameID1；顺序即优先级）
FAMILY_CANDIDATES: tuple[str, ...] = ("MiSans", "MiSans Medium")

#: 注册后端签名：拿到字体文件路径，返回是否注册成功
Register = Callable[[Path], bool]

_REGISTERED = False
_REPORTED: set[str] = set()


def font_candidates() -> list[Path]:
    """随包字体的候选路径：冻结资源目录 → 源码目录。

    - PyInstaller onefile：数据文件在 ``sys._MEIPASS``（spec 的 ``datas`` 声明）；
    - 源码 / 开发态：仓库根的 ``font/``（与主程序 ``mangoproof/fonts.py`` 同构，
      不含"程序目录"那一档 —— 安装器是从临时目录运行的一次性程序，程序目录里
      没有自己的 font/）。
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "font" / FONT_FILENAME)
    candidates.append(Path(__file__).resolve().parent.parent / "font" / FONT_FILENAME)
    return candidates


def find_font_path() -> Path | None:
    """第一个存在的随包字体文件；都没有返回 ``None``。"""
    for path in font_candidates():
        if path.exists():
            return path
    return None


# --------------------------------------------------------------------------- #
# 平台后端（全部"尽力而为"：失败只返回 False，由调用方降级）
# --------------------------------------------------------------------------- #


def _register_windows(path: Path) -> bool:
    """Windows：``AddFontResourceExW(FR_PRIVATE)``（只对本进程可见）。"""
    import ctypes

    FR_PRIVATE = 0x10
    gdi32 = ctypes.WinDLL("gdi32")  # type: ignore[attr-defined]  # pragma: no cover
    gdi32.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    gdi32.AddFontResourceExW.restype = ctypes.c_int
    return int(gdi32.AddFontResourceExW(str(path), FR_PRIVATE, None)) > 0


def _register_linux(path: Path) -> bool:
    """Linux：fontconfig 的"应用字体"（Tk 走 Xft，用的就是 fontconfig）。"""
    import ctypes

    fontconfig = ctypes.CDLL("libfontconfig.so.1")
    fontconfig.FcInit.restype = ctypes.c_int
    fontconfig.FcConfigGetCurrent.restype = ctypes.c_void_p
    fontconfig.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
    if not fontconfig.FcInit():
        return False
    config = fontconfig.FcConfigGetCurrent()
    if not config:
        return False
    added = fontconfig.FcConfigAppFontAddFile(
        ctypes.c_void_p(config), os.fsencode(str(path))
    )
    return int(added) == 1


def _register_macos(path: Path) -> bool:
    """macOS：CoreText 的进程级注册（安装器是裸可执行文件，没有 .app 可挂字体）。"""
    import ctypes

    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool,
    ]
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    raw = os.fsencode(str(path))
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    if not url:
        return False
    try:
        ct.CTFontManagerRegisterFontsForURL.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
        error = ctypes.c_void_p()
        # 1 = kCTFontManagerScopeProcess（只影响本进程）
        return bool(
            ct.CTFontManagerRegisterFontsForURL(ctypes.c_void_p(url), 1, ctypes.byref(error))
        )
    finally:
        cf.CFRelease(ctypes.c_void_p(url))


def _register_unsupported(path: Path) -> bool:  # noqa: ARG001 - 统一签名
    """其它平台（例如 Android）：没有可用的进程内字体注册机制。"""
    return False


def _platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def backend_for(platform: str) -> Register:
    """按平台取注册后端（未知平台给一个永远返回 False 的兜底）。"""
    return {
        "windows": _register_windows,
        "linux": _register_linux,
        "macos": _register_macos,
        "other": _register_unsupported,
    }.get(platform, _register_unsupported)


def register_bundled(
    *,
    candidates: Sequence[Path] | None = None,
    platform: str | None = None,
    backend: Register | None = None,
) -> list[Path]:
    """把随包字体注册进本进程；返回注册成功的文件（无文件时返回空列表）。

    任何失败都只记日志：**字体缺失必须降级为系统字体，不能让安装器起不来**。
    """
    paths = [
        Path(path)
        for path in (candidates if candidates is not None else font_candidates())
        if Path(path).exists()
    ]
    if not paths:
        return []
    register = backend or backend_for(platform or _platform_key())
    done: list[Path] = []
    for path in paths:
        try:
            if register(path):
                done.append(path)
                log.info("已注册内置字体：%s", path)
        except Exception as exc:  # 缺库 / API 不存在 / 权限…… 一律降级
            if str(path) not in _REPORTED:      # 同一个文件只报一次
                _REPORTED.add(str(path))
                log.warning("内置字体注册失败（%s），回退系统字体：%s", exc, path)
    return done


def _tk_families(root) -> set[str] | None:
    """Tk 当前可见的字体家族；读不到（无 Tk / 无图形会话）返回 ``None``。"""
    if root is None:
        return None
    try:
        from tkinter import font as tkfont

        return {str(name) for name in tkfont.families(root)}
    except Exception as exc:
        log.debug("读取 Tk 字体家族失败：%s", exc)
        return None


def pick_family(
    root,
    *,
    families: Iterable[str] | None = None,
    candidates: Sequence[Path] | None = None,
    platform: str | None = None,
    backend: Register | None = None,
) -> str | None:
    """内置 MiSans 的家族名；用不上时返回 ``None``（调用方回退系统字体链）。

    ``families`` / ``backend`` 可注入（单测不碰真实 ctypes、也不开窗口）。
    ``root is None`` 表示调用方还没有 Tk（无图形会话）→ 直接返回 ``None``，
    免得在没有窗口的场景里去注册字体。
    """
    global _REGISTERED
    if root is None and families is None:
        return None
    if not _REGISTERED:
        _REGISTERED = True
        register_bundled(candidates=candidates, platform=platform, backend=backend)
    names = set(families) if families is not None else _tk_families(root)
    if not names:
        return None
    lowered = {str(name).lower(): str(name) for name in names}
    for wanted in FAMILY_CANDIDATES:
        found = lowered.get(wanted.lower())
        if found:
            return found
    return None


def _reset_cache() -> None:  # 单测用
    """清掉"已注册"标记（注册本身是幂等的，清掉只是为了测试可重复）。"""
    global _REGISTERED
    _REGISTERED = False


__all__ = [
    "FAMILY_CANDIDATES",
    "FONT_FILENAME",
    "Register",
    "backend_for",
    "find_font_path",
    "font_candidates",
    "pick_family",
    "register_bundled",
]
