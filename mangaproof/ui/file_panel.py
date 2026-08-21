"""PSD 文件列表面板（需求 §11.1、§29）。"""

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

from mangaproof.ui.theme import COLOR_FAIL, COLOR_PASS, COLOR_UNREVIEWED, COLOR_WARN


class FilePanel(QWidget):
    file_activated = Signal(int)   # 文件索引

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._files: List[str] = []  # 相对路径（与任务 files 顺序一致）
        self._statuses: Dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.title_label = QLabel("PSD 文件")
        self.title_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title_label)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
        layout.addWidget(self.list_widget)

    def set_task_info(self, task_name: str, task_type: str) -> None:
        kind = "文件夹任务" if task_type == "folder" else "单文件任务"
        self.title_label.setText(f"PSD 文件 — {task_name}（{kind}）")

    def set_files(self, files: List[str]) -> None:
        self._files = list(files)
        self.list_widget.clear()
        for rel in files:
            item = QListWidgetItem(rel)
            item.setData(Qt.ItemDataRole.UserRole, rel)
            self.list_widget.addItem(item)

    def set_file_statuses(self, statuses: Dict[str, str]) -> None:
        """statuses: {rel_path: "done"|"partial"|"failed"|"unreviewed"}"""
        self._statuses = dict(statuses)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            rel = item.data(Qt.ItemDataRole.UserRole)
            status = self._statuses.get(rel, "unreviewed")
            color, icon = {
                "done": (COLOR_PASS, "✓ "),
                "failed": (COLOR_FAIL, "✗ "),
                "partial": (COLOR_WARN, "● "),
            }.get(status, (COLOR_UNREVIEWED, "○ "))
            item.setText(icon + rel)
            item.setForeground(QColor(color))

    def set_current_row(self, row: int) -> None:
        if 0 <= row < self.list_widget.count():
            self.list_widget.setCurrentRow(row)

    def clear_selection(self) -> None:
        self.list_widget.setCurrentRow(-1)

    def _on_current_row_changed(self, row: int) -> None:
        if row >= 0:
            self.file_activated.emit(row)
