"""图层列表面板（需求 §11.2、§14、§29）。

每行：○ 未监制 / ✓ 已通过 / ✗ 未通过（语义颜色，需求 §28）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mangaproof.review.state import FAILED, PASSED, STATUS_ICONS, UNREVIEWED
from mangaproof.ui.theme import COLOR_FAIL, COLOR_PASS, COLOR_UNREVIEWED

STATUS_COLORS = {
    UNREVIEWED: QColor(COLOR_UNREVIEWED),
    PASSED: QColor(COLOR_PASS),
    FAILED: QColor(COLOR_FAIL),
}


class LayerPanel(QWidget):
    layer_activated = Signal(int)   # 图层索引

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._layer_names: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        title = QLabel("图层")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
        layout.addWidget(self.list_widget)

    def set_layers(self, names: List[str]) -> None:
        self._layer_names = list(names)
        self.list_widget.clear()
        for name in names:
            item = QListWidgetItem(f"{STATUS_ICONS[UNREVIEWED]} {name}")
            item.setForeground(STATUS_COLORS[UNREVIEWED])
            self.list_widget.addItem(item)

    def set_statuses(self, statuses: List[str], issue_counts: List[int]) -> None:
        """statuses[i] 对应第 i 个图层状态；issue_counts 为该层问题数。"""
        for row, status in enumerate(statuses):
            item = self.list_widget.item(row)
            if item is None:
                continue
            name = self._layer_names[row] if row < len(self._layer_names) else ""
            extra = f"（{issue_counts[row]} 个问题）" if issue_counts[row] > 0 else ""
            item.setText(f"{STATUS_ICONS.get(status, STATUS_ICONS[UNREVIEWED])} {name}{extra}")
            item.setForeground(STATUS_COLORS.get(status, STATUS_COLORS[UNREVIEWED]))

    def set_current_row(self, row: int) -> None:
        if 0 <= row < self.list_widget.count():
            self.list_widget.setCurrentRow(row)

    def _on_current_row_changed(self, row: int) -> None:
        if row >= 0:
            self.layer_activated.emit(row)
