"""控制台可见性控制。

规则（按运行方式与平台）：
- 直接运行 Python（python main.py）：始终保留控制台，设置开关不生效；
- Windows 打包产物（sys.frozen）：默认隐藏控制台（设置项 hide_console，
  默认 True），在设置中关闭后恢复控制台显示（Win32 ShowWindow 运行时切换，
  因此打包需用 --console 构建保留控制台子系统）；
- macOS / Linux：没有独立控制台窗口概念——图形启动（--windowed）本就无
  终端输出，从终端启动时终端窗口属于终端程序，应用无权切换。
  由打包参数决定，运行时不做处理。
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("mangaproof.console")

_SW_HIDE = 0
_SW_SHOW = 5


def decide_console_hidden(
    is_frozen: bool, platform: str, hide_console_setting: bool
) -> bool:
    """纯逻辑：是否应隐藏控制台（可测试）。"""
    if not is_frozen:
        return False            # 直接运行 py 文件：始终保留控制台
    if platform != "win32":
        return False            # 非 Windows：运行时不可切换，交给构建参数
    return bool(hide_console_setting)


def set_console_visible(visible: bool) -> None:
    """Windows：显示/隐藏本进程的控制台窗口。非 Windows no-op。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, _SW_SHOW if visible else _SW_HIDE)
    except Exception:
        log.debug("控制台窗口切换失败", exc_info=True)


def apply_console_visibility(settings) -> None:
    """按运行方式与设置决定控制台可见性（在设置加载后调用）。

    settings: Settings 对象（需含 hide_console 字段）。
    """
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        log.info("控制台：显示（直接运行 Python，始终保留，设置开关不生效）")
        return
    if sys.platform != "win32":
        # macOS / Linux 没有独立控制台窗口：图形启动（--windowed）本就没有
        # 终端输出；从终端启动时终端窗口属于终端程序，应用无权切换。
        log.info("控制台：由打包参数决定（%s 无独立控制台窗口，运行时不做处理）", sys.platform)
        return
    hidden = bool(settings.hide_console)
    set_console_visible(not hidden)
    log.info("控制台：%s（Windows 打包产物，可在设置中切换）", "隐藏" if hidden else "显示")
