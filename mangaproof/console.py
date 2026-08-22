"""控制台可见性控制。

规则（按运行方式与平台）：
- 直接运行 Python（python main.py）：始终保留控制台，设置开关不生效；
- Windows 打包产物（sys.frozen）：默认关闭控制台窗口（设置项 hide_console，
  默认 True）——FreeConsole() 直接销毁进程的控制台窗口（彻底消失，
  不是最小化隐藏）；在设置中关闭该开关后 AllocConsole() 新建控制台窗口
  并重定向 stdout/stderr，日志重新可见；
- macOS / Linux：没有独立控制台窗口概念——图形启动（--windowed）本就无
  终端输出，从终端启动时终端窗口属于终端程序，应用无权切换。
  由打包参数决定，运行时不做处理。
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("mangaproof.console")


def decide_console_hidden(
    is_frozen: bool, platform: str, hide_console_setting: bool
) -> bool:
    """纯逻辑：是否应隐藏控制台（可测试）。"""
    if not is_frozen:
        return False            # 直接运行 py 文件：始终保留控制台
    if platform != "win32":
        return False            # 非 Windows：运行时不可切换，交给构建参数
    return bool(hide_console_setting)


def _redirect_stdio_to_console() -> None:
    """把 stdout/stderr 重定向到新建的控制台窗口。"""
    try:
        con = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = con
        sys.stderr = con
    except OSError:
        log.debug("重定向标准输出到控制台失败", exc_info=True)


def _redirect_stdio_to_devnull() -> None:
    """控制台销毁后，标准输出改为写入空设备，避免写坏句柄报错。"""
    try:
        devnull = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = devnull
        sys.stderr = devnull
    except OSError:
        pass


def set_console_visible(visible: bool) -> None:
    """Windows：关闭/打开本进程的控制台窗口。非 Windows no-op。

    - 关闭：FreeConsole() 彻底销毁进程的控制台窗口（直接消失，不是
      最小化隐藏）；若从 cmd/PowerShell 启动，仅解除与父终端进程的
      关联，终端窗口本身属于父进程、不会被销毁；
    - 打开：AllocConsole() 新建控制台窗口，并重定向 stdout/stderr。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if visible:
            if not kernel32.GetConsoleWindow():
                kernel32.AllocConsole()
            _redirect_stdio_to_console()
        else:
            kernel32.FreeConsole()
            _redirect_stdio_to_devnull()
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
    if hidden:
        set_console_visible(False)
        log.info("控制台：已关闭（FreeConsole 销毁窗口，可在设置中重新打开）")
    else:
        set_console_visible(True)
        log.info("控制台：已打开（AllocConsole）")
