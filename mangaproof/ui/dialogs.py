"""通用对话框：问题录入（预制类型 / 自定义批注）、返修单生成。"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mangaproof.review.issue import Issue


class IssueDialog(QDialog):
    """添加 / 编辑问题（需求 §34、§36、§37）。

    - 预制问题类型下拉（可含“其他”）；
    - 自定义批注文本框（可留空，不阻塞监制，需求 §38）；
    - rect 信息展示（方式 A/B 拖框结果）。
    """

    def __init__(
        self,
        issue_types: List[str],
        parent: Optional[QWidget] = None,
        default_type: Optional[str] = None,
        issue: Optional[Issue] = None,
        rect: Optional[Tuple[float, float, float, float]] = None,
        title: str = "添加问题",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 320)
        self._rect = rect

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItems(issue_types)
        if default_type is not None and default_type in issue_types:
            self.type_combo.setCurrentText(default_type)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("自定义批注（可留空），例如：这里应该使用 Bold，而不是 Regular。")
        self.comment_edit.setMinimumHeight(120)

        if issue is not None:
            if issue.type in issue_types:
                self.type_combo.setCurrentText(issue.type)
            self.comment_edit.setPlainText(issue.comment)
            if rect is None:
                rect = issue.rect

        rect_text = "（未标注位置）"
        if rect is not None and rect[2] > 0 and rect[3] > 0:
            x, y, w, h = rect
            rect_text = f"（{int(x)}, {int(y)}　{int(w)}×{int(h)}）"
        self.rect_label = QLabel(rect_text)
        self.rect_label.setWordWrap(True)

        form.addRow("问题类型：", self.type_combo)
        form.addRow("红框位置：", self.rect_label)
        form.addRow("批注：", self.comment_edit)
        layout.addLayout(form)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def result_values(self) -> Tuple[str, str]:
        return self.type_combo.currentText(), self.comment_edit.toPlainText().strip()


class ReportDialog(QDialog):
    """返修单生成（需求 §46、§49、§54）。"""

    def __init__(
        self,
        default_name: str,
        task_name: str,
        incomplete: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("生成 MangaProof 返修单")
        self.resize(440, 170)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("返修单名称：", self.name_edit)
        layout.addLayout(form)

        note = "⚠ 任务尚未全部完成，返修单将标注「任务状态：未完成」。" if incomplete else ""
        self.note_label = QLabel(note)
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #f5a623;")
        layout.addWidget(self.note_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("生成")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def report_name(self) -> str:
        return self.name_edit.text().strip()
