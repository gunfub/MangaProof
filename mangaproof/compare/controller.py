"""自动对比控制器（需求 §21～§26、§65）。

状态机：ORIGINAL ↔ BG_ONLY，每秒 2 个完整循环：
每个状态 250ms，状态切换 4 次/秒（需求 §22）。

对比期间只切换显示源，不重新读取 PSD、不重算 Composite、
Camera/Zoom/当前图层全部保持不变（需求 §23、§59）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("mangaproof.compare.controller")

ORIGINAL = "ORIGINAL"
BG_ONLY = "BG_ONLY"

# 每个状态 250ms（需求 §22）
STATE_INTERVAL_MS = 250


class CompareController(QObject):
    display_changed = Signal(str)      # ORIGINAL / BG_ONLY
    running_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._state = ORIGINAL
        self._timer = QTimer(self)
        self._timer.setInterval(STATE_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    # -- 状态 --------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def display_state(self) -> str:
        return self._state

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
