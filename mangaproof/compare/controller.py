"""自动对比控制器（需求 §21～§26、§65）。

状态机：ORIGINAL ↔ BG_ONLY。默认每秒切换 4 次（每张停留 250ms，需求 §22），
速度可在设置中调整（set_interval_ms），运行中调整即时生效。

两种模式（由主窗口按设置分发）：
- 自动挡：start()/stop() 驱动定时器来回闪切；
- 手动挡：swap_once() 不启动定时器，按一下切一次；
- interrupt() 统一处理"被其他操作打断"：停止自动切换并强制恢复 ORIGINAL，
  手动挡下同样强制回原图（复用需求 §26 的行为约定）。

对比期间只切换显示源，不重新读取 PSD、不重算 Composite、
Camera/Zoom/当前图层全部保持不变（需求 §23、§59）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("mangaproof.compare.controller")

ORIGINAL = "ORIGINAL"
BG_ONLY = "BG_ONLY"

# 默认速度：4 次/秒（需求 §22 的原硬编码行为）
DEFAULT_SPEED_HZ = 4
MIN_INTERVAL_MS = 1


def hz_to_interval_ms(hz) -> int:
    """切换频率（次/秒）→ 每张停留时长（ms）。"""
    return max(MIN_INTERVAL_MS, round(1000 / max(1, int(hz))))


class CompareController(QObject):
    display_changed = Signal(str)      # ORIGINAL / BG_ONLY
    running_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._state = ORIGINAL
        self._interval_ms = hz_to_interval_ms(DEFAULT_SPEED_HZ)
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._on_tick)

    # -- 状态 --------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def display_state(self) -> str:
        return self._state

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def set_interval_ms(self, ms: int) -> None:
        """设置每张停留时长。运行中调整会立即按新间隔重新计时。"""
        ms = max(MIN_INTERVAL_MS, int(ms))
        self._interval_ms = ms
        self._timer.setInterval(ms)

    # -- 操作 --------------------------------------------------------------

    def start(self) -> None:
        """开始自动对比。从 ORIGINAL 开始（若当前是 BG_ONLY 先回到 ORIGINAL）。"""
        if self.is_running:
            return
        if self._state != ORIGINAL:
            self._set_state(ORIGINAL)
        self._timer.start()
        self.running_changed.emit(True)
        log.debug("自动对比开始")

    def stop(self) -> None:
        """停止自动对比并恢复 ORIGINAL（需求 §26）。"""
        if not self.is_running:
            return
        self._timer.stop()
        self._set_state(ORIGINAL)
        self.running_changed.emit(False)
        log.debug("自动对比停止")

    def swap_once(self) -> None:
        """手动挡：切换一次显示源（不启动定时器，不影响运行状态）。"""
        if self._state == ORIGINAL:
            self._set_state(BG_ONLY)
        else:
            self._set_state(ORIGINAL)

    def interrupt(self) -> None:
        """被其他操作打断（打开/切换文件、切图层、通过/不通过、批注、
        问题、红框模式、自定义批注等）：停止自动切换并强制恢复 ORIGINAL。

        自动挡 = stop()；手动挡定时器未运行，仅强制回原图。
        """
        if self.is_running:
            self._timer.stop()
            self.running_changed.emit(False)
        self._set_state(ORIGINAL)

    def toggle(self) -> None:
        if self.is_running:
            self.stop()
        else:
            self.start()

    def reset_to_original(self) -> None:
        """强制恢复 ORIGINAL（不改变运行状态）。"""
        self._set_state(ORIGINAL)

    # -- 内部 --------------------------------------------------------------

    def _on_tick(self) -> None:
        if self._state == ORIGINAL:
            self._set_state(BG_ONLY)
        else:
            self._set_state(ORIGINAL)

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.display_changed.emit(state)
