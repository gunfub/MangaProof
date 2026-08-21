"""问题面板（需求 §33～§38）。

当前图层的 Issue 列表、状态按钮、添加入口（拖框 / 自定义批注）。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mangaproof.review.issue import Issue
from mangaproof.review.state import FAILED, PASSED, UNREVIEWED
from mangaproof.ui.theme import COLOR_FAIL, COLOR_PASS, COLOR_UNREVIEWED, COLOR_WARN


class IssuePanel(QWidget):
    status_change_requested = Signal(str)     # UNREVIEWED / PASSED / FAILED
    add_issue_requested = Signal()            # 进入拖框模式（方式 B）
    custom_comment_requested = Signal()       # 自定义批注（Ctrl+Enter）
    edit_issue_requested = Signal(str)        # issue_id（双击编辑）
    delete_issue_requested = Signal(str)      # issue_id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._issues: List[Issue] = []
        self._status = UNREVIEWED

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        title = QLabel("当前图层问题")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.layer_name_label = QLabel("图层：-")
        self.layer_name_label.setWordWrap(True)
        layout.addWidget(self.layer_name_label)

        self.status_label = QLabel("状态：○ 未监制")
        layout.addWidget(self.status_label)

        status_row = QHBoxLayout()
        self.pass_btn = QPushButton("✓ 通过")
        self.pass_btn.setStyleSheet(f"QPushButton {{ color: {COLOR_PASS}; }}")
        self.pass_btn.setToolTip("标记当前图层通过")
        self.fail_btn = QPushButton("✗ 未通过")
        self.fail_btn.setStyleSheet(f"QPushButton {{ color: {COLOR_FAIL}; }}")
        self.fail_btn.setToolTip("标记当前图层未通过")
        self.reset_btn = QPushButton("○ 重置")
        self.reset_btn.setToolTip("重置为未监制（同时清除该层问题）")
        status_row.addWidget(self.pass_btn)
        status_row.addWidget(self.fail_btn)
        status_row.addWidget(self.reset_btn)
        layout.addLayout(status_row)

        self.pass_btn.clicked.connect(lambda: self.status_change_requested.emit(PASSED))
        self.fail_btn.clicked.connect(lambda: self.status_change_requested.emit(FAILED))
        self.reset_btn.clicked.connect(lambda: self.status_change_requested.emit(UNREVIEWED))

        self.issue_list = QListWidget()
        self.issue_list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.issue_list)

        add_row = QHBoxLayout()
        self.add_btn = QPushButton("＋ 拖框添加问题")
        self.add_btn.clicked.connect(self.add_issue_requested.emit)
        add_row.addWidget(self.add_btn)
        layout.addLayout(add_row)

        self.custom_btn = QPushButton("✎ 自定义批注")
        self.custom_btn.clicked.connect(self.custom_comment_requested.emit)
        layout.addWidget(self.custom_btn)

        self.delete_btn = QPushButton("🗑 删除选中问题")
        self.delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self.delete_btn)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

    # -- 更新 --------------------------------------------------------------

    def set_current(self, layer_name: str, status: str, issues: List[Issue]) -> None:
        self._issues = list(issues)
        self._status = status
        self.layer_name_label.setText(f"图层：{layer_name}")
        status_text = {"unreviewed": "○ 未监制", "passed": "✓ 已通过", "failed": "✗ 未通过"}
        self.status_label.setText(f"状态：{status_text.get(status, status_text['unreviewed'])}")

        self.issue_list.clear()
        for issue in self._issues:
            comment = f" — {issue.comment}" if issue.comment else ""
            rect_info = ""
            x, y, w, h = issue.rect
            if w > 0 and h > 0:
                rect_info = f"　[{int(x)},{int(y)} {int(w)}×{int(h)}]"
            item = QListWidgetItem(f"#{issue.issue_no} {issue.type}{comment}{rect_info}")
            item.setData(Qt.ItemDataRole.UserRole, issue.issue_id)
            self.issue_list.addItem(item)

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)

    def set_shortcut_labels(self, bindings: dict, issue_type_tips: str = "") -> None:
        """动态显示当前绑定的快捷键（需求 §30）。

        bindings: {"pass", "fail", "redraw", "custom", "cancel"} 快捷键文案；
        issue_type_tips: 问题类型快捷键清单（显示在拖框按钮 tooltip）。
        """
        self.pass_btn.setText(f"✓ 通过 ({bindings.get('pass', 'Enter')})")
        self.pass_btn.setToolTip(f"标记当前图层通过　快捷键：{bindings.get('pass', 'Enter')}")
        self.fail_btn.setText(f"✗ 未通过 ({bindings.get('fail', '/')})")
        self.fail_btn.setToolTip(f"标记当前图层未通过　快捷键：{bindings.get('fail', '/')}")
        self.add_btn.setText(f"＋ 拖框添加问题 ({bindings.get('redraw', 'R')})")
        self.custom_btn.setText(f"✎ 自定义批注 ({bindings.get('custom', 'Ctrl+Enter')})")
        if issue_type_tips:
            self.add_btn.setToolTip(issue_type_tips)

    def set_buttons_enabled(self, enabled: bool) -> None:
        self.pass_btn.setEnabled(enabled)
        self.fail_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        issue_id = item.data(Qt.ItemDataRole.UserRole)
        if issue_id:
            self.edit_issue_requested.emit(issue_id)

    def _on_delete(self) -> None:
        item = self.issue_list.currentItem()
        if item is None:
            return
        issue_id = item.data(Qt.ItemDataRole.UserRole)
        if issue_id:
            self.delete_issue_requested.emit(issue_id)
