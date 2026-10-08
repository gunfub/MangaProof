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
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
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
#: 直接保存）在图层区的空态说明（需求 §88）。
#:
#: 只在打开这种文件时显示；关闭任务 / 打开普通文件时必须清空——未打开任务
#: 时图层区要保持初始空态，不能留下一段说明文字。
ZERO_LAYER_PLACEHOLDER = (
    "该 PSD 不含任何图层（0 图层文件）\n"
    "常见于把图片拖进 Photoshop、未做任何处理就直接保存；\n"
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
        # 竖向 Fixed：标题只占自己那一行的高度。否则一旦列表让出竖向空间
        # （例如图层区只剩说明文字时），多余高度会摊到标题上——"图层"二字
        # 会在被拉高的标题里垂直居中，看起来像掉到了中间。
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(title)

        # 空态说明（0 图层文件）：显示时把列表高度收掉、宽度提示留着
        # （见 _set_placeholder）——面板宽度不因这段文字变化，多余竖向空间
        # 归说明区，"图层"标题始终贴在顶部。
        self.placeholder_label = QLabel()
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.placeholder_label.setStyleSheet(f"color: {COLOR_UNREVIEWED};")
        # 横向 Ignored + 最小宽度 1：说明文字反过来不能把图层栏撑宽。窄屏 /
        # Android 放大字号下，最长一行会超过图层列表的默认宽度，按 sizeHint
        # 参与布局就会把右侧面板顶宽——这里让它只按可用宽度折行。
        self.placeholder_label.setMinimumWidth(1)
        self.placeholder_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        # 外面再包一层滚动区：字号被放得很大而图层栏很矮时，说明宁可滚动也
        # 不能被截断（滚动条只在真的放不下时才出现）
        self.placeholder_area = QScrollArea()
        self.placeholder_area.setWidgetResizable(True)
        self.placeholder_area.setFrameShape(QFrame.Shape.NoFrame)
        self.placeholder_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.placeholder_area.setMinimumWidth(1)
        self.placeholder_area.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.placeholder_area.setWidget(self.placeholder_label)
        self.placeholder_area.hide()
        layout.addWidget(self.placeholder_area)

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
        """重建图层行；names 为空且给了 placeholder 时，列表上方多一段说明。

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
        """只切换说明文字本身：图层列表始终留在布局里（宽度提示留着）。

        列表负责撑住面板宽度，说明显示时把列表**高度**收掉：
        - 不能一起隐藏列表——隐藏会连它的宽度提示一起丢掉，面板会被这段
          说明文字反向决定宽度（窄屏 / 放大字号下就是"图层栏被撑宽"），
          首次布局时还会缩得比平时窄；
        - 高度收掉后，多余竖向空间全给说明文字，标题也贴着顶部——不再出现
          "图层"二字被拉高的标题带到中间去的情况。
        """
        self.placeholder_label.setText(text)
        self.placeholder_area.setVisible(bool(text))
        # 16777215 = QWIDGETSIZE_MAX（Qt 默认无上限），恢复原状用它
        self.list_widget.setMaximumHeight(0 if text else 16777215)

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
