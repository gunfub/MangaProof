"""控制台可见性控制（打包产物运行时隐藏/恢复）。

规则：
- 直接运行 Python（python main.py）：始终保留控制台，设置开关不生效；
- PyInstaller 打包产物（sys.frozen）：默认隐藏控制台（设置项
  hide_console，默认 True），在设置中关闭后恢复控制台显示；
- 仅 Windows 支持运行时切换（console 子系统）；其他平台由构建参数
  （--console / --windowed）决定，运行时不做处理。
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
    hidden = decide_console_hidden(frozen, sys.platform, settings.hide_console)
    log.info(
        "控制台：%s（%s）",
        "隐藏" if hidden else "显示",
        "打包产物" if frozen else "直接运行 Python，始终保留",
    )
    if frozen and sys.platform == "win32":
        set_console_visible(not hidden)
