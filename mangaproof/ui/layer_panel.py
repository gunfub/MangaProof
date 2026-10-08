# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

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

#: 0 图层文件（PSD 里没有任何图层数据，例如图片拖进 Photoshop 后未做处理
#: 直接保存）在图层区的空态说明（需求方 2026-10-08）。
#:
#: 只在打开这种文件时显示；关闭任务 / 打开普通文件时必须清空——未打开任务
#: 时图层区要保持初始空态，不能留下一段说明文字。
ZERO_LAYER_PLACEHOLDER = (
    "该 PSD 不含任何图层（0 图层文件）\n\n"
    "常见于把图片拖进 Photoshop、未做任何处理就直接保存。\n"
    "没有可监制的内容，已在左侧文件列表标记为「通过」。"
)


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

        # 空态说明（0 图层文件）：与列表互斥显示，默认两边都空 = 初始空态
        self.placeholder_label = QLabel()
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet(f"color: {COLOR_UNREVIEWED};")
        self.placeholder_label.hide()
        layout.addWidget(self.placeholder_label)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
        # 长图层名单行省略号截断（与问题面板 _ElidedLabel 风格一致），
        # 不横向滚动；完整名称悬停 tooltip 查看。
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_widget.setWordWrap(False)
        self.list_widget.setUniformItemSizes(True)
        layout.addWidget(self.list_widget)

    def set_layers(self, names: List[str], placeholder: str = "") -> None:
        """重建图层行；names 为空且给了 placeholder 时改显示空态说明。

        placeholder 只在「确实没有图层可列」时生效（names 非空一律忽略），
        不传即回到纯列表 / 纯空态——关闭任务走的就是这条默认路径。
        """
        self._layer_names = list(names)
        self.list_widget.clear()
        for name in names:
            text = f"{STATUS_ICONS[UNREVIEWED]} {name}"
            item = QListWidgetItem(text)
            item.setForeground(STATUS_COLORS[UNREVIEWED])
            item.setToolTip(text)
            self.list_widget.addItem(item)
        self._set_placeholder("" if names else placeholder)

    def _set_placeholder(self, text: str) -> None:
        """空态说明与图层列表互斥显示（文字为空 = 回到纯列表）。"""
        self.placeholder_label.setText(text)
        self.placeholder_label.setVisible(bool(text))
        self.list_widget.setVisible(not text)

    def set_statuses(self, statuses: List[str], issue_counts: List[int]) -> None:
        """statuses[i] 对应第 i 个图层状态；issue_counts 为该层问题数。"""
        for row, status in enumerate(statuses):
            item = self.list_widget.item(row)
            if item is None:
                continue
            name = self._layer_names[row] if row < len(self._layer_names) else ""
            extra = f"（{issue_counts[row]} 个问题）" if issue_counts[row] > 0 else ""
            text = f"{STATUS_ICONS.get(status, STATUS_ICONS[UNREVIEWED])} {name}{extra}"
            item.setText(text)
            item.setToolTip(text)
            item.setForeground(STATUS_COLORS.get(status, STATUS_COLORS[UNREVIEWED]))

    def set_current_row(self, row: int) -> None:
        if 0 <= row < self.list_widget.count():
            self.list_widget.setCurrentRow(row)

    def _on_current_row_changed(self, row: int) -> None:
        if row >= 0:
            self.layer_activated.emit(row)
