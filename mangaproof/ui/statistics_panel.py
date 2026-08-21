"""任务统计面板（需求 §41、§42、§43）。

布局：
- 当前 PSD：图层状态芯片（可点击跳转，需求 §43）+ 2×2 统计卡片；
- 总体统计：3×2 统计卡片 + 总体进度条；
- 统计值使用语义颜色（通过绿 / 未通过红 / 未监制灰 / 问题橙）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mangaproof.review.state import FAILED, PASSED, UNREVIEWED
from mangaproof.ui.theme import (
    COLOR_ACCENT,
    COLOR_BG_WIDGET,
    COLOR_FAIL,
    COLOR_PASS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_UNREVIEWED,
    COLOR_WARN,
)

CHIP_STYLES = {
    UNREVIEWED: (COLOR_UNREVIEWED, "○"),
    PASSED: (COLOR_PASS, "✓"),
    FAILED: (COLOR_FAIL, "✗"),
}


def _stat_cell(value: str, label: str, color: str = COLOR_TEXT) -> QLabel:
    """统计卡片：大号彩色数值 + 小号灰色标签，居中两行。"""
    cell = QLabel()
    cell.setTextFormat(Qt.TextFormat.RichText)
    cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cell.setMinimumHeight(44)
    # 元数据挂在 cell 上，供 _set_cell 重渲染
    cell._stat_label = label
    cell._stat_color = color
    StatisticsPanel._render_cell(cell, value, color)
    return cell


class StatisticsPanel(QWidget):
    layer_chip_clicked = Signal(int)   # 当前 PSD 图层索引

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._chips: List[QToolButton] = []
        self.psd_cells: Dict[str, QLabel] = {}
        self.total_cells: Dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # ---- 当前 PSD ----
        self.psd_group = QFrame()
        psd_layout = QVBoxLayout(self.psd_group)
        psd_layout.setContentsMargins(4, 4, 4, 4)
        psd_layout.setSpacing(6)

        self.psd_name_label = QLabel("当前 PSD：-")
        self.psd_name_label.setStyleSheet("font-weight: bold;")
        psd_layout.addWidget(self.psd_name_label)

        self.chip_layout = QGridLayout()
        self.chip_layout.setSpacing(2)
        psd_layout.addLayout(self.chip_layout)

        # 当前 PSD 统计：2×2 卡片
        psd_grid = QGridLayout()
        psd_grid.setSpacing(4)
        self.psd_cells["reviewed"] = _stat_cell("0 / 0", "已监制", COLOR_ACCENT)
        self.psd_cells["passed"] = _stat_cell("0", "通过", COLOR_PASS)
        self.psd_cells["failed"] = _stat_cell("0", "未通过", COLOR_FAIL)
        self.psd_cells["unreviewed"] = _stat_cell("0", "未监制", COLOR_UNREVIEWED)
        for i, key in enumerate(("reviewed", "passed", "failed", "unreviewed")):
            psd_grid.addWidget(self.psd_cells[key], i // 2, i % 2)
        psd_layout.addLayout(psd_grid)
        layout.addWidget(self.psd_group)

        # ---- 总体统计 ----
        self.total_group = QFrame()
        total_layout = QVBoxLayout(self.total_group)
        total_layout.setContentsMargins(4, 4, 4, 4)
        total_layout.setSpacing(6)

        total_title = QLabel("总体统计")
        total_title.setStyleSheet("font-weight: bold;")
        total_layout.addWidget(total_title)

        # 总体统计：3×2 卡片
        total_grid = QGridLayout()
        total_grid.setSpacing(4)
        self.total_cells["files"] = _stat_cell("0", "PSD")
        self.total_cells["layers"] = _stat_cell("0", "总图层")
        self.total_cells["passed"] = _stat_cell("0", "通过", COLOR_PASS)
        self.total_cells["failed"] = _stat_cell("0", "未通过", COLOR_FAIL)
        self.total_cells["unreviewed"] = _stat_cell("0", "未监制", COLOR_UNREVIEWED)
        self.total_cells["issues"] = _stat_cell("0", "问题")
        for i, key in enumerate(
            ("files", "layers", "passed", "failed", "unreviewed", "issues")
        ):
            total_grid.addWidget(self.total_cells[key], i // 3, i % 3)
        total_layout.addLayout(total_grid)

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
            chip.setToolTip(
                f"{icon} {name}"
                + (f"（{issue_counts[idx]} 个问题）" if issue_counts[idx] else "")
            )
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

        self._set_cell(self.psd_cells["reviewed"], f"{reviewed} / {total}")
        self._set_cell(self.psd_cells["passed"], str(passed))
        self._set_cell(self.psd_cells["failed"], str(failed))
        self._set_cell(self.psd_cells["unreviewed"], str(unreviewed))

    # -- 总体 --------------------------------------------------------------

    def set_total(self, counts: dict) -> None:
        total = counts["total"]
        self._set_cell(self.total_cells["files"], str(counts["files"]))
        self._set_cell(self.total_cells["layers"], str(total))
        self._set_cell(self.total_cells["passed"], str(counts["passed"]))
        self._set_cell(self.total_cells["failed"], str(counts["failed"]))
        self._set_cell(self.total_cells["unreviewed"], str(counts["unreviewed"]))
        # 有问题时问题数用警告色
        self._set_cell(
            self.total_cells["issues"],
            str(counts["issues"]),
            color=COLOR_WARN if counts["issues"] > 0 else COLOR_TEXT,
        )
        ratio = counts["reviewed"] / total if total > 0 else 0.0
        self.progress_bar.setValue(int(ratio * 1000))

    # -- 内部 --------------------------------------------------------------

    @staticmethod
    def _render_cell(cell: QLabel, value: str, color: Optional[str] = None) -> None:
        """按「数值 + 标签」重渲染卡片，保持语义颜色。"""
        if color is None:
            color = cell._stat_color
        cell.setText(
            f"<div style='text-align:center;'>"
            f"<span style='font-size:16px; font-weight:bold; color:{color};'>{value}</span>"
            f"<br/><span style='font-size:10px; color:{COLOR_TEXT_DIM};'>{cell._stat_label}</span>"
            f"</div>"
        )

    @staticmethod
    def _set_cell(cell: QLabel, value: str, color: Optional[str] = None) -> None:
        """只更新卡片数值，保留标签文案与默认颜色。"""
        StatisticsPanel._render_cell(cell, value, color)
