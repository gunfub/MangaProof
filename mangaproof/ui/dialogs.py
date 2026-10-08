# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""通用对话框：问题录入（预制类型 / 自定义批注）、返修单生成。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mangaproof.config.settings import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_REPORT_IMAGE_FORMAT,
    JPEG_QUALITY_CHOICES,
)
from mangaproof.review.issue import Issue
from mangaproof.ui.theme import COLOR_PASS, COLOR_TEXT_DIM, COLOR_WARN
from mangaproof.ui.widgets import NoWheelComboBox


class _CommentEdit(QTextEdit):
    """批注输入框：Enter = 确认，Shift+Enter = 换行。

    标注流程是「框选 → 选类型 → 写批注 → 确认」的纯键盘流：Enter 直接
    确认可以省掉一次点击；需要多行批注时按 Shift+Enter（对话框里有提示）。
    """

    submitted = Signal()

    def keyPressEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)   # Shift+Enter 插入换行
            else:
                self.submitted.emit()          # Enter 等同按下「确定」
            return
        super().keyPressEvent(event)


class IssueDialog(QDialog):
    """添加 / 编辑问题（需求 §34、§36、§37）。

    - 预制问题类型下拉（可含“其他”）；
    - 自定义批注文本框（可留空，不阻塞监制，需求 §38）；
    - rect 信息展示（方式 A/B 拖框结果）。

    自动框选模式（auto_pick=True，仅「自动框选」触发时使用）：
    类型未定时自动展开下拉栏，直接用问题类型快捷键选中即为错误原因，
    随即跳到批注输入框；类型已由快捷键确定时直接停在批注框。
    手动拖框触发的对话框保持原逻辑（自己点开下拉栏选择）。
    """

    def __init__(
        self,
        issue_types: List[str],
        parent: Optional[QWidget] = None,
        default_type: Optional[str] = None,
        issue: Optional[Issue] = None,
        rect: Optional[Tuple[float, float, float, float]] = None,
        title: str = "添加问题",
        type_keys: Optional[Dict[str, str]] = None,
        auto_pick: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 320)
        self._rect = rect
        self._auto_pick = bool(auto_pick)
        # 问题类型快捷键 → 类型名（自动框选模式下用按键直接选定类型）
        self._type_keys: Dict[str, str] = {
            str(k).strip().upper(): name
            for name, k in (type_keys or {}).items()
            if str(k).strip()
        }
        self._type_preselected = default_type is not None and default_type in issue_types

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = NoWheelComboBox()
        # 下拉项显示「快捷键 类型名」（问题类型快捷键直接对应，键盘流不必记忆）；
        # 真实类型名放在 userData 里，取用一律走 currentData()。
        key_of = {name: key for key, name in self._type_keys.items()}
        for name in issue_types:
            key = key_of.get(name, "")
            self.type_combo.addItem(f"{key}　{name}" if key else name, name)
        if default_type is not None and default_type in issue_types:
            self.type_combo.setCurrentIndex(self.type_combo.findData(default_type))

        self.comment_edit = _CommentEdit()
        self.comment_edit.setPlaceholderText("自定义批注（可留空），例如：这里应该使用 Bold，而不是 Regular。")
        self.comment_edit.setMinimumHeight(120)
        # 键盘流：Enter 直接确认，Shift+Enter 才换行（见 _CommentEdit）
        self.comment_edit.submitted.connect(self.accept)

        if issue is not None:
            if issue.type in issue_types:
                self.type_combo.setCurrentIndex(self.type_combo.findData(issue.type))
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

        comment_box = QWidget()
        comment_layout = QVBoxLayout(comment_box)
        comment_layout.setContentsMargins(0, 0, 0, 0)
        comment_layout.setSpacing(2)
        comment_layout.addWidget(self.comment_edit)
        self.comment_hint = QLabel("Enter 确认　Shift+Enter 换行")
        self.comment_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        comment_layout.addWidget(self.comment_hint)
        form.addRow("批注：", comment_box)
        layout.addLayout(form)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        if self._auto_pick:
            # 下拉栏弹出后按键事件落在弹出列表上，两个都装过滤器更稳
            self._auto_pick_started = False
            self.installEventFilter(self)
            self.type_combo.installEventFilter(self)
            self.type_combo.view().installEventFilter(self)
            self.type_combo.activated.connect(self._on_type_activated)

    # -- 自动框选模式：展开下拉栏 + 快捷键选类型 + 跳到批注框 --------------

    def showEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        """首次真正显示时才展开下拉栏。

        不在 __init__ 里排队：对话框可能被构造后并未显示（调用方提前
        取消/未 exec），那时弹出下拉栏会抢走键盘焦点，连主窗口的快捷键
        都一起吃掉。
        """
        super().showEvent(event)
        if self._auto_pick and not self._auto_pick_started:
            self._auto_pick_started = True
            QTimer.singleShot(0, self._begin_auto_pick)

    def _begin_auto_pick(self) -> None:
        """类型未定则自动展开下拉栏；已由问题类型快捷键确定则直奔批注框。"""
        if not self.isVisible():
            return
        if self._type_preselected:
            self.comment_edit.setFocus()
            return
        self.type_combo.setFocus()
        self.type_combo.showPopup()

    def _select_type(self, name: str) -> bool:
        idx = self.type_combo.findData(name)
        if idx < 0:
            return False
        self.type_combo.setCurrentIndex(idx)
        if self.type_combo.view().isVisible():
            self.type_combo.hidePopup()
        self.comment_edit.setFocus()      # 自动跳转到批注输入框
        return True

    def _on_type_activated(self, _index: int) -> None:
        """鼠标/回车在下拉栏里选定后同样跳到批注框。"""
        self.comment_edit.setFocus()

    def eventFilter(self, obj, event) -> bool:
        if self._auto_pick and event.type() == QEvent.Type.KeyPress:
            text = (event.text() or "").strip().upper()
            name = self._type_keys.get(text)
            if name and self._select_type(name):
                return True
        return super().eventFilter(obj, event)

    def result_values(self) -> Tuple[str, str]:
        # 下拉项文本含快捷键前缀，真实类型名取 userData
        return str(self.type_combo.currentData()), self.comment_edit.toPlainText().strip()


class ReportDialog(QDialog):
    """返修单生成（需求 §46、§49、§54）。

    除名称外，可在生成时选择页面图像是否压缩（PNG 无损 / JPEG 压缩）、
    JPEG 质量与总览表是否隐藏无问题 PSD，选择结果由主窗口记回设置，下次沿用。

    手动（Ctrl+R）与「全部图层完成」后的自动生成共用这一个窗口，是否弹出
    由设置 `report_show_options` 控制（需求 §86）。
    """

    def __init__(
        self,
        default_name: str,
        task_name: str,
        incomplete: bool,
        parent: Optional[QWidget] = None,
        image_format: str = DEFAULT_REPORT_IMAGE_FORMAT,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        hide_clean_files: bool = True,
        completed: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("生成 MangaProof 返修单")
        self.resize(470, 300)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(default_name)
        form.addRow("返修单名称：", self.name_edit)

        self.image_format_combo = NoWheelComboBox()
        self.image_format_combo.addItem("PNG 无损（默认，体积大）", "png")
        self.image_format_combo.addItem("JPEG 压缩（体积小，有损）", "jpeg")
        fmt_idx = self.image_format_combo.findData(image_format)
        self.image_format_combo.setCurrentIndex(max(0, fmt_idx))
        self.image_format_combo.setToolTip(
            "问题明细页的页面图像格式：\n"
            "· PNG：无损，页面图像较大；\n"
            "· JPEG：压缩，报告体积显著变小（漫画页面常见大幅网点/渐变），\n"
            "  画质略降，红框与编号仍是 PDF 矢量、不受影响。"
        )
        form.addRow("页面图像：", self.image_format_combo)

        self.quality_combo = NoWheelComboBox()
        for q in JPEG_QUALITY_CHOICES:
            self.quality_combo.addItem(f"{q}%" + ("（默认）" if q == DEFAULT_JPEG_QUALITY else ""), q)
        q_idx = self.quality_combo.findData(jpeg_quality)
        self.quality_combo.setCurrentIndex(max(0, q_idx))
        self.quality_combo.setToolTip("仅 JPEG 压缩时生效：质量越低体积越小")
        form.addRow("JPEG 质量：", self.quality_combo)
        self.image_format_combo.currentIndexChanged.connect(self._sync_quality_enabled)
        self._sync_quality_enabled()

        self.hide_clean_check = QCheckBox("总览表隐藏无问题的 PSD（全部图层通过）")
        self.hide_clean_check.setChecked(bool(hide_clean_files))
        self.hide_clean_check.setToolTip(
            "勾选后，返修单的「PSD 总览」表只列出有问题或未完成的 PSD，\n"
            "全部通过且无问题的页会被隐藏（表下注明隐藏数量），报告更聚焦；\n"
            "未通过、未监制的 PSD 始终保留。"
        )
        form.addRow(self.hide_clean_check)
        layout.addLayout(form)

        # 顶部状态行。completed：本次是「全部图层已检查完成」触发的生成——
        # 这个窗口要替代原先单独的「监制完成」提示框（能弹选项窗口时不再连弹
        # 两个框，需求 §86.3），所以完成态必须在这里明说一句。
        if completed:
            note, color = "✓ 所有图层已检查完成，将生成最终返修单。", COLOR_PASS
        elif incomplete:
            note, color = "⚠ 任务尚未全部完成，返修单将标注「任务状态：未完成」。", COLOR_WARN
        else:
            note, color = "", COLOR_WARN
        self.note_label = QLabel(note)
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(f"color: {color};")
        layout.addWidget(self.note_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("生成")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _sync_quality_enabled(self) -> None:
        self.quality_combo.setEnabled(self.image_format() == "jpeg")

    def report_name(self) -> str:
        return self.name_edit.text().strip()

    def image_format(self) -> str:
        return str(self.image_format_combo.currentData())

    def jpeg_quality(self) -> int:
        return int(self.quality_combo.currentData())

    def hide_clean_files(self) -> bool:
        return self.hide_clean_check.isChecked()
