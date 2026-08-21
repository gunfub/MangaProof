"""任务统计面板（需求 §41、§42、§43）。

- 当前 PSD：图层状态芯片（可点击跳转，需求 §43）、计数；
- 文件夹总体统计 + 总体进度。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mangaproof.review.state import FAILED, PASSED, UNREVIEWED
from mangaproof.ui.theme import (
    COLOR_BG_WIDGET,
    COLOR_FAIL,
    COLOR_PASS,
    COLOR_UNREVIEWED,
    COLOR_WARN,
)

CHIP_STYLES = {
    UNREVIEWED: (COLOR_UNREVIEWED, "○"),
    PASSED: (COLOR_PASS, "✓"),
    FAILED: (COLOR_FAIL, "✗"),
}


class _FlowLayout(QGridLayout):
    """固定 10 列的芯片流式布局。"""

    def __init__(self, columns: int = 10):
        super().__init__()
        self._columns = columns


class StatisticsPanel(QWidget):
    layer_chip_clicked = Signal(int)   # 当前 PSD 图层索引

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._chips: List[QToolButton] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ---- 当前 PSD ----
        self.psd_group = QFrame()
        psd_layout = QVBoxLayout(self.psd_group)
        psd_layout.setContentsMargins(4, 4, 4, 4)
        psd_layout.setSpacing(4)

        self.psd_name_label = QLabel("当前 PSD：-")
        self.psd_name_label.setStyleSheet("font-weight: bold;")
        psd_layout.addWidget(self.psd_name_label)

        self.chip_layout = QGridLayout()
        self.chip_layout.setSpacing(2)
        psd_layout.addLayout(self.chip_layout)

        self.psd_counts_label = QLabel("")
        self.psd_counts_label.setTextFormat(Qt.TextFormat.RichText)
        psd_layout.addWidget(self.psd_counts_label)
        layout.addWidget(self.psd_group)

        # ---- 总体统计 ----
        self.total_group = QFrame()
        total_layout = QVBoxLayout(self.total_group)
        total_layout.setContentsMargins(4, 4, 4, 4)
        total_layout.setSpacing(4)

        total_title = QLabel("总体统计")
        total_title.setStyleSheet("font-weight: bold;")
        total_layout.addWidget(total_title)

        self.total_label = QLabel("")
        self.total_label.setWordWrap(True)
        # 显式声明富文本格式，避免 Qt 启发式（首个换行前无标签）误判为纯文本
        self.total_label.setTextFormat(Qt.TextFormat.RichText)
        total_layout.addWidget(self.total_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        total_layout.addWidget(self.progress_bar)
        layout.addWidget(self.total_group)

        layout.addStretch(1)

    # -- 当前 PSD ----------------------------------------------------------

    def set_current_psd(
        self,
        psd_name: str,
        layer_names: List[str],
        statuses: List[str],
        issue_counts: List[int],
    ) -> None:
        self.psd_name_label.setText(f"当前 PSD：{psd_name}")

        for chip in self._chips:
            self.chip_layout.removeWidget(chip)
            chip.deleteLater()
        self._chips = []

        for idx, name in enumerate(layer_names):
            status = statuses[idx] if idx < len(statuses) else UNREVIEWED
            color, icon = CHIP_STYLES.get(status, CHIP_STYLES[UNREVIEWED])
            chip = QToolButton()
            chip.setText(icon)
            chip.setToolTip(f"{icon} {name}" + (f"（{issue_counts[idx]} 个问题）" if issue_counts[idx] else ""))
            chip.setFixedSize(22, 22)
            chip.setStyleSheet(
                f"QToolButton {{ color: {color}; font-weight: bold;"
                f" background: {COLOR_BG_WIDGET}; border: 1px solid transparent;"
                f" border-radius: 3px; padding: 0; }}"
                f"QToolButton:hover {{ border-color: {color}; }}"
            )
            chip.clicked.connect(lambda _=False, i=idx: self.layer_chip_clicked.emit(i))
            self._chips.append(chip)
            row, col = divmod(idx, 12)
            self.chip_layout.addWidget(chip, row, col)

        passed = statuses.count(PASSED)
        failed = statuses.count(FAILED)
        unreviewed = statuses.count(UNREVIEWED)
        reviewed = passed + failed
        total = len(statuses)
        self.psd_counts_label.setText(
            f"已监制：{reviewed} / {total}　"
            f"<span style='color:{COLOR_PASS}'>通过：{passed}</span>　"
            f"<span style='color:{COLOR_FAIL}'>未通过：{failed}</span>　"
            f"未监制：{unreviewed}"
        )

    # -- 总体 --------------------------------------------------------------

    def set_total(self, counts: dict) -> None:
        total = counts["total"]
        # 富文本下换行用 <br/>（\n 会破坏标签解析）
        self.total_label.setText(
            f"PSD：{counts['files']}　总图层：{total}<br/>"
            f"<span style='color:{COLOR_PASS}'>通过：{counts['passed']}</span>　"
            f"<span style='color:{COLOR_FAIL}'>未通过：{counts['failed']}</span>　"
            f"<span style='color:{COLOR_WARN}'>未监制：{counts['unreviewed']}</span>　"
            f"问题：{counts['issues']}"
        )
        ratio = counts["reviewed"] / total if total > 0 else 0.0
        self.progress_bar.setValue(int(ratio * 1000))
