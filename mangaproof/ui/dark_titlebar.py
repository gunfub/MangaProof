"""原生标题栏暗色化（需求 §28 暗色主题的延伸）。

- Windows：通过 DWM API DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)
  把原生标题栏设为暗色，兼容 Win10 1809（属性 19）与 20H1+（属性 20）；
  最大化/还原会重置标题栏，因此通过应用级事件过滤器自动重设；
- Qt 6.8+：QStyleHints.setColorScheme(Dark) 向系统声明深色偏好，
  Windows 11 / macOS 的原生标题栏会跟随该偏好；
- Linux：由窗口管理器控制，无法从应用侧强制，保持现状。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger("mangaproof.ui.dark_titlebar")

_DWMWA_USE_IMMERSIVE_DARK_MODE_20H1 = 20   # Windows 10 20H1+ / 11
_DWMWA_USE_IMMERSIVE_DARK_MODE_1809 = 19   # Windows 10 1809+


def _apply_dwm_dark(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        dwmapi = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return
    value = ctypes.c_int(1)
    size = ctypes.sizeof(value)
    try:
        hr = dwmapi.DwmSetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            _DWMWA_USE_IMMERSIVE_DARK_MODE_20H1,
            ctypes.byref(value),
            size,
        )
        if hr != 0:  # 老版本系统回退到 1809 属性
            dwmapi.DwmSetWindowAttribute(
                ctypes.wintypes.HWND(hwnd),
                _DWMWA_USE_IMMERSIVE_DARK_MODE_1809,
                ctypes.byref(value),
                size,
            )
    except Exception:
        log.debug("DWM 暗色标题栏设置失败", exc_info=True)


def apply_dark_title_bar(window: QWidget) -> None:
    """对单个顶层窗口应用暗色标题栏（仅 Windows 生效，其他平台 no-op）。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
    except Exception:
        return
    _apply_dwm_dark(hwnd)


class DarkTitleBarFilter(QObject):
    """应用级事件过滤器：顶层窗口显示/状态变化时重设暗色标题栏。"""

    _reapply_types = (
        QEvent.Type.Show,
        QEvent.Type.WinIdChange,
        QEvent.Type.WindowStateChange,
    )

    def eventFilter(self, obj, event):  # noqa: N802（Qt 命名）
        if event.type() in self._reapply_types:
            if isinstance(obj, QWidget) and obj.isWindow():
                apply_dark_title_bar(obj)
        return False


def install_dark_titlebar(app: QApplication) -> None:
    """安装暗色标题栏支持（幂等）。在 QApplication 创建后调用。"""
    if getattr(app, "_dark_titlebar_filter", None) is not None:
        return
    _filter = DarkTitleBarFilter(app)
    app.installEventFilter(_filter)
    app._dark_titlebar_filter = _filter  # 保持引用，防止被垃圾回收

    # Qt 6.8+：声明深色偏好，Windows 11 / macOS 原生标题栏跟随
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except (AttributeError, TypeError) as exc:
        log.debug("当前 Qt 版本不支持 setColorScheme：%s", exc)
