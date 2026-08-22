"""设置对话框（需求 §20、§30、§35、§46、§49）。

- 图层显示比例（20%～90%）；
- 核心快捷键重绑定（QKeySequenceEdit 录制）；
- 问题类型快捷键重绑定；
- PDF 生成开关、返修单自定义名称、递归扫描。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mangaproof.config.settings import (
    DEFAULT_DISPLAY_RATIO,
    DEFAULT_ISSUE_TYPES,
    DEFAULT_KEYBINDINGS,
    DISPLAY_RATIOS,
    Settings,
)

CORE_ACTION_LABELS: Dict[str, str] = {
    "prev_psd": "上一个 PSD",
    "next_psd": "下一个 PSD",
    "prev_layer": "上一个图层",
    "next_layer": "下一个图层",
    "pass_layer": "当前图层通过",
    "fail_layer": "当前图层未通过",
    "toggle_compare": "自动对比",
    "cancel_operation": "取消/退出批注操作",
    "save_task": "保存任务",
    "custom_comment": "自定义批注",
    "open_psd": "打开单个 PSD",
    "open_folder": "打开文件夹",
    "generate_report": "生成返修单",
    "redraw_mode": "红框模式",
}


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("MangaProof 设置")
        self.resize(560, 640)
        self._settings = settings

        layout = QVBoxLayout(self)

        # ---- 显示 ----
        display_group = QGroupBox("显示")
        display_form = QFormLayout(display_group)
        self.ratio_combo = QComboBox()
        for r in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(r * 100)}%", r)
        current_ratio = settings.layer_display_ratio
        if current_ratio not in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(current_ratio * 100)}%", current_ratio)
        idx = self.ratio_combo.findData(current_ratio)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        display_form.addRow("图层自动显示比例：", self.ratio_combo)
        layout.addWidget(display_group)

        # ---- 任务 ----
        task_group = QGroupBox("任务")
        task_form = QFormLayout(task_group)
        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.setChecked(settings.recursive_scan)
        task_form.addRow(self.recursive_check)
        # 控制台开关仅 Windows 打包产物有意义（见 mangaproof/console.py）
        import sys as _sys

        self.console_check = QCheckBox("打包产物隐藏控制台窗口（仅 Windows）")
        self.console_check.setChecked(settings.hide_console)
        self.console_check.setToolTip(
            "仅对 Windows 打包产物生效（默认隐藏）。\n"
            "macOS / Linux 没有独立的控制台窗口，由打包参数决定"
            "（--windowed 图形启动无终端输出），运行时无法切换。\n"
            "直接运行 python main.py 时控制台始终显示，不受此开关影响。"
        )
        if _sys.platform != "win32":
            self.console_check.setEnabled(False)
        task_form.addRow(self.console_check)
        layout.addWidget(task_group)

        # ---- 返修单 ----
        report_group = QGroupBox("MangaProof 返修单")
        report_form = QFormLayout(report_group)
        self.pdf_check = QCheckBox("完成监制后自动生成返修单")
        self.pdf_check.setChecked(settings.generate_pdf_on_complete)
        report_form.addRow(self.pdf_check)
        self.report_name_edit = QLineEdit(settings.report_name)
        self.report_name_edit.setPlaceholderText("留空使用默认名称（PSD 名 / 文件夹名）")
        report_form.addRow("返修单名称：", self.report_name_edit)
        layout.addWidget(report_group)

        # ---- 快捷键 ----
        shortcut_group = QGroupBox("核心快捷键（点击后按键重新绑定）")
        shortcut_layout = QVBoxLayout(shortcut_group)
        self.core_table = QTableWidget(len(CORE_ACTION_LABELS), 2)
        self.core_table.setHorizontalHeaderLabels(["功能", "快捷键"])
        self.core_table.verticalHeader().setVisible(False)
        self.core_table.horizontalHeader().setStretchLastSection(True)
        self.core_table.setColumnWidth(0, 300)
        self._core_edits: Dict[str, QKeySequenceEdit] = {}
        for row, (action, label) in enumerate(CORE_ACTION_LABELS.items()):
            self.core_table.setItem(row, 0, QTableWidgetItem(label))
            edit = QKeySequenceEdit()
            from PySide6.QtGui import QKeySequence
            edit.setKeySequence(QKeySequence(settings.binding(action)))
            self.core_table.setCellWidget(row, 1, edit)
            self._core_edits[action] = edit
        shortcut_layout.addWidget(self.core_table)
        layout.addWidget(shortcut_group)

        # ---- 问题类型快捷键 ----
        issue_group = QGroupBox("问题类型快捷键（点击后按键重新绑定）")
        issue_layout = QVBoxLayout(issue_group)
        self.issue_table = QTableWidget(len(settings.issue_types) + 1, 2)
        self.issue_table.setHorizontalHeaderLabels(["问题类型", "快捷键"])
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.horizontalHeader().setStretchLastSection(True)
        self.issue_table.setColumnWidth(0, 300)
        self._issue_edits: Dict[int, QKeySequenceEdit] = {}
        from PySide6.QtGui import QKeySequence
        for row, item in enumerate(settings.issue_types):
            self.issue_table.setItem(row, 0, QTableWidgetItem(item["name"]))
            edit = QKeySequenceEdit()
            edit.setKeySequence(QKeySequence(item.get("key", "")))
            self.issue_table.setCellWidget(row, 1, edit)
            self._issue_edits[row] = edit
        # 自定义批注快捷键行
        last_row = len(settings.issue_types)
        self.issue_table.setItem(last_row, 0, QTableWidgetItem("自定义批注"))
        self.custom_key_edit = QKeySequenceEdit()
        self.custom_key_edit.setKeySequence(QKeySequence(settings.custom_comment_key))
        self.issue_table.setCellWidget(last_row, 1, self.custom_key_edit)
        issue_layout.addWidget(self.issue_table)
        layout.addWidget(issue_group)

        # ---- 按钮 ----
        button_row = QHBoxLayout()
        self.reset_btn = QPushButton("恢复默认设置")
        self.reset_btn.clicked.connect(self._reset_defaults)
        button_row.addWidget(self.reset_btn)
        button_row.addStretch(1)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        button_row.addWidget(self.button_box)
        layout.addLayout(button_row)

    def _reset_defaults(self) -> None:
        from PySide6.QtGui import QKeySequence
        idx = self.ratio_combo.findData(DEFAULT_DISPLAY_RATIO)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        self.recursive_check.setChecked(False)
        self.console_check.setChecked(True)
        self.pdf_check.setChecked(True)
        self.report_name_edit.clear()
        for action, edit in self._core_edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_KEYBINDINGS.get(action, "")))
        for row, edit in self._issue_edits.items():
            if row < len(DEFAULT_ISSUE_TYPES):
                edit.setKeySequence(QKeySequence(DEFAULT_ISSUE_TYPES[row].get("key", "")))
        self.custom_key_edit.setKeySequence(QKeySequence(DEFAULT_KEYBINDINGS["custom_comment"]))

    def apply_to(self, settings: Settings) -> None:
        settings.layer_display_ratio = float(self.ratio_combo.currentData())
        settings.recursive_scan = self.recursive_check.isChecked()
        settings.generate_pdf_on_complete = self.pdf_check.isChecked()
        settings.report_name = self.report_name_edit.text().strip()
        settings.hide_console = self.console_check.isChecked()

        for action, edit in self._core_edits.items():
            seq = edit.keySequence().toString()
            settings.keybindings[action] = seq if seq else DEFAULT_KEYBINDINGS.get(action, "")

        for row, edit in self._issue_edits.items():
            if row < len(settings.issue_types):
                seq = edit.keySequence().toString()
                settings.issue_types[row]["key"] = seq

        settings.custom_comment_key = (
            self.custom_key_edit.keySequence().toString()
            or DEFAULT_KEYBINDINGS["custom_comment"]
        )
