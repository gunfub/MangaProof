# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""设置对话框（需求 §20、§30、§35、§46、§49）。

- 图层显示比例（10%～100%）与 bg / bg 拷贝 的独立比例（默认关）；
- 自动对比：模式（自动/手动切换）与预设切换速度档位；
- 快捷键设置独立子对话框（KeybindingsDialog）：核心快捷键、
  问题类型快捷键、自定义批注键——主对话框只保留入口按钮，
  避免设置页过长挤压；
- PDF 生成开关、返修单自定义名称、递归扫描；
- 问题红框显示范围（当前页全部问题 / 仅当前图层）、蓝色虚线边界框开关、
  内存回收策略档位。

主对话框的分组放在滚动区内，底部按钮固定可见（768p 笔记本友好）。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mangaproof.compare.controller import hz_to_interval_ms
from mangaproof.config.settings import (
    android_memory_policy_locked,
    android_ui_scaling,
    COMPARE_SPEED_TIERS,
    CORE_SHORTCUT_LABELS,
    DEFAULT_BG_COPY_DISPLAY_RATIO,
    DEFAULT_BG_DISPLAY_RATIO,
    DEFAULT_BG_RATIO_ENABLED,
    DEFAULT_COMPARE_MODE,
    DEFAULT_COMPARE_SPEED_HZ,
    DEFAULT_DISPLAY_RATIO,
    DEFAULT_ISSUE_SCOPE,
    DEFAULT_ISSUE_TYPES,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_KEYBINDINGS,
    default_memory_policy,
    DEFAULT_REPORT_IMAGE_FORMAT,
    DEFAULT_SHOW_LAYER_OUTLINE,
    default_ui_scale,
    DEFAULT_WHEEL_MODE,
    DISPLAY_RATIOS,
    effective_memory_policy,
    JPEG_QUALITY_CHOICES,
    Settings,
    shortcut_conflicts,
    ui_scale_choices,
)
from mangaproof.ui.theme import COLOR_ACCENT, COLOR_BG_WIDGET, COLOR_TEXT, COLOR_WARN
from mangaproof.ui.widgets import NoWheelComboBox

class KeybindingsDialog(QDialog):
    """快捷键设置子对话框：核心快捷键 + 问题类型快捷键 + 自定义批注键。

    与主设置对话框配合：主对话框保存本实例，仅在主对话框 OK 时
    才把修改写回 Settings（保持「取消即放弃」语义）。
    """

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.resize(560, 560)

        layout = QVBoxLayout(self)

        # ---- 核心快捷键 ----
        core_group = QGroupBox("核心快捷键（点击后按键重新绑定）")
        core_layout = QVBoxLayout(core_group)
        self.core_table = QTableWidget(len(CORE_SHORTCUT_LABELS), 2)
        self.core_table.setHorizontalHeaderLabels(["功能", "快捷键"])
        self.core_table.verticalHeader().setVisible(False)
        self.core_table.horizontalHeader().setStretchLastSection(True)
        self.core_table.setColumnWidth(0, 300)
        self._core_edits: Dict[str, QKeySequenceEdit] = {}
        for row, (action, label) in enumerate(CORE_SHORTCUT_LABELS.items()):
            self.core_table.setItem(row, 0, QTableWidgetItem(label))
            edit = QKeySequenceEdit()
            edit.setKeySequence(QKeySequence(settings.binding(action)))
            self.core_table.setCellWidget(row, 1, edit)
            self._core_edits[action] = edit
        core_layout.addWidget(self.core_table)
        layout.addWidget(core_group)

        # ---- 问题类型快捷键 ----
        issue_group = QGroupBox("问题类型快捷键（点击后按键重新绑定）")
        issue_layout = QVBoxLayout(issue_group)
        self.issue_table = QTableWidget(len(settings.issue_types) + 1, 2)
        self.issue_table.setHorizontalHeaderLabels(["问题类型", "快捷键"])
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.horizontalHeader().setStretchLastSection(True)
        self.issue_table.setColumnWidth(0, 300)
        self._issue_edits: Dict[int, QKeySequenceEdit] = {}
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

        # ---- 冲突提示（同一按键绑给多个动作时 Qt 会判定歧义，全都不触发）----
        self.conflict_label = QLabel("")
        self.conflict_label.setWordWrap(True)
        self.conflict_label.setStyleSheet(f"color: {COLOR_WARN};")
        self.conflict_label.setVisible(False)
        layout.addWidget(self.conflict_label)

        # ---- 按钮 ----
        button_row = QHBoxLayout()
        self.reset_btn = QPushButton("恢复默认快捷键")
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

        # 任一快捷键变化就即时重算冲突提示
        for edit in list(self._core_edits.values()) + list(self._issue_edits.values()):
            edit.keySequenceChanged.connect(self._refresh_conflicts)
        self.custom_key_edit.keySequenceChanged.connect(self._refresh_conflicts)
        self._refresh_conflicts()

    # -- 冲突检查 ----------------------------------------------------------

    def conflicts(self) -> dict[str, list[tuple[str, str]]]:
        """当前编辑框内容的重复快捷键（未保存也检查）。"""
        keybindings: Dict[str, str] = {}
        for action, edit in self._core_edits.items():
            seq = edit.keySequence().toString()
            keybindings[action] = seq or DEFAULT_KEYBINDINGS.get(action, "")
        issue_types = [
            {"name": str(self.issue_table.item(row, 0).text()),
             "key": edit.keySequence().toString()}
            for row, edit in self._issue_edits.items()
            if self.issue_table.item(row, 0) is not None
        ]
        custom = (
            self.custom_key_edit.keySequence().toString()
            or DEFAULT_KEYBINDINGS["custom_comment"]
        )
        return shortcut_conflicts(keybindings, issue_types, custom)

    @staticmethod
    def _format_conflicts(conflicts: dict[str, list[tuple[str, str]]]) -> str:
        lines = []
        for seq, names in sorted(conflicts.items()):
            who = " ／ ".join(f"{kind}：{label}" for kind, label in names)
            lines.append(f"· {seq}　→　{who}")
        return "\n".join(lines)

    def _refresh_conflicts(self) -> None:
        conflicts = self.conflicts()
        if not conflicts:
            self.conflict_label.setVisible(False)
            self.conflict_label.setText("")
            return
        self.conflict_label.setText(
            "⚠ 快捷键冲突（同一按键绑了多个动作时，按下不会有任何反应，"
            "请改绑其中一个）：\n" + self._format_conflicts(conflicts)
        )
        self.conflict_label.setVisible(True)

    def accept(self) -> None:
        conflicts = self.conflicts()
        if conflicts:
            QMessageBox.warning(
                self,
                "快捷键冲突",
                "以下按键被绑定到了多个动作，保存后按下不会有任何反应：\n\n"
                f"{self._format_conflicts(conflicts)}\n\n"
                "请先改绑其中一个，再点「确定」。",
            )
            self._refresh_conflicts()
            return
        super().accept()

    def _reset_defaults(self) -> None:
        for action, edit in self._core_edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_KEYBINDINGS.get(action, "")))
        for row, edit in self._issue_edits.items():
            if row < len(DEFAULT_ISSUE_TYPES):
                edit.setKeySequence(QKeySequence(DEFAULT_ISSUE_TYPES[row].get("key", "")))
        self.custom_key_edit.setKeySequence(QKeySequence(DEFAULT_KEYBINDINGS["custom_comment"]))
        self._refresh_conflicts()

    def apply_to(self, settings: Settings) -> None:
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


class SettingsDialog(QDialog):
    """程序设置主对话框。

    设置分组放在滚动区里（内容随版本增长，768p 笔记本上按钮不能被挤到
    屏幕外）；底部「恢复默认设置 / 确定 / 取消」固定在滚动区之外，
    任何窗口高度下都可见可点。
    """

    def __init__(
        self,
        settings: Settings,
        parent: Optional[QWidget] = None,
        intro: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("MangaProof 设置")
        self.resize(560, 560)   # 与快捷键子对话框一致；小屏可自由缩小（内容滚动）
        self._settings = settings
        self._kb_dialog: Optional[KeybindingsDialog] = None
        self._kb_reset_defaults = False

        layout = QVBoxLayout(self)

        # 首次使用引导文案（可选）：只说明「这里是全部设置项、不改就用默认值」，
        # 不预设、不推荐任何值——放在滚动区之外，任何窗口高度下都看得见
        self.intro_label: Optional[QLabel] = None
        if intro:
            self.intro_label = QLabel(intro)
            self.intro_label.setObjectName("settingsIntro")
            self.intro_label.setWordWrap(True)
            self.intro_label.setStyleSheet(
                f"QLabel#settingsIntro {{ color: {COLOR_TEXT};"
                f" background: {COLOR_BG_WIDGET};"
                f" border-left: 3px solid {COLOR_ACCENT};"
                f" padding: 6px 8px; }}"
            )
            layout.addWidget(self.intro_label)

        # 滚动区：高度不足时出现滚动条，宽度始终跟随对话框
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        # 底部留一点空隙：滚到底时最后一组不贴边
        body_layout.setContentsMargins(0, 0, 0, 6)
        self.scroll_area.setWidget(body)
        layout.addWidget(self.scroll_area, 1)

        # ---- 显示 ----
        display_group = QGroupBox("显示")
        display_form = QFormLayout(display_group)
        self.ratio_combo = NoWheelComboBox()
        for r in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(r * 100)}%", r)
        current_ratio = settings.layer_display_ratio
        if current_ratio not in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(current_ratio * 100)}%", current_ratio)
        idx = self.ratio_combo.findData(current_ratio)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        self.ratio_combo.setToolTip(
            "自动缩放的目标比例：定位当前图层 / 切换图层 / 翻页时，把该图层最长边\n"
            "缩放到视口对应尺寸的这个比例（100% = 正好铺满视口）。\n"
            "它**不是像素缩放百分比**——状态栏那个「缩放：xx%」才是当前的实际像素缩放；\n"
            "手动滚轮缩放不会被它覆盖，只影响下一次自动定位。"
        )
        display_form.addRow("图层自动显示比例：", self.ratio_combo)

        # bg / bg 拷贝 的独立比例（需求 §20.2，默认关）。
        # 关闭时两个下拉置灰：值仍保留在设置里，只是不参与自动缩放。
        self.bg_ratio_check = QCheckBox("bg / bg 拷贝 使用独立的自动显示比例")
        self.bg_ratio_check.setChecked(settings.bg_ratio_enabled)
        self.bg_ratio_check.setToolTip(
            "开启后，只有下面这两类图层用自己的比例，其余图层仍用上面的全局比例：\n"
            "· bg：PS 里名为 bg 的图层；没有 bg 名时按既有规则退回「最底部有内容的图层」；\n"
            "· bg 拷贝：名为「bg 拷贝」（中文版）或「bg copy」（英文版）的图层，\n"
            "  没有这一层就不套用独立比例、也不做兜底。\n"
            "关闭（默认）时所有图层统一用全局比例，行为与旧版一致。"
        )
        display_form.addRow(self.bg_ratio_check)

        self.bg_ratio_combo = NoWheelComboBox()
        self.bg_copy_ratio_combo = NoWheelComboBox()
        for combo, current in (
            (self.bg_ratio_combo, settings.bg_display_ratio),
            (self.bg_copy_ratio_combo, settings.bg_copy_display_ratio),
        ):
            for r in DISPLAY_RATIOS:
                combo.addItem(f"{int(r * 100)}%", r)
            if current not in DISPLAY_RATIOS:      # 兼容手工写入的非标准档位
                combo.addItem(f"{int(current * 100)}%", current)
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self.bg_ratio_combo.setToolTip(
            "bg 图层（含「没有 bg 名时退回最底部有内容图层」的兜底）的自动显示比例。\n"
            "默认 100%：最长边正好铺满视口。"
        )
        self.bg_copy_ratio_combo.setToolTip(
            "「bg 拷贝 / bg copy」图层的自动显示比例，默认 100%。\n"
            "文件里没有这一层时这个值不生效（没有就不做兜底）。"
        )
        self.bg_ratio_check.toggled.connect(self._update_bg_ratio_enabled)
        self._update_bg_ratio_enabled()
        display_form.addRow("bg 自动显示比例：", self.bg_ratio_combo)
        display_form.addRow("bg 拷贝自动显示比例：", self.bg_copy_ratio_combo)

        self.issue_scope_combo = NoWheelComboBox()
        self.issue_scope_combo.addItem("当前页全部问题（默认，跨图层显示红框）", "page")
        self.issue_scope_combo.addItem("仅当前图层的问题（旧版行为）", "layer")
        scope_idx = self.issue_scope_combo.findData(settings.issue_scope)
        self.issue_scope_combo.setCurrentIndex(max(0, scope_idx))
        self.issue_scope_combo.setToolTip(
            "画布上红框（问题标注）的显示范围：\n"
            "· 当前页全部问题：当前 PSD 的所有问题都显示，翻到哪页就看全哪页的标注；\n"
            "· 仅当前图层：只显示当前图层的问题，其余图层红框隐藏。\n"
            "切换图层/翻页即时生效，不影响已保存的问题数据。"
        )
        display_form.addRow("问题红框显示：", self.issue_scope_combo)

        self.layer_outline_check = QCheckBox("显示当前图层的蓝色虚线边界框")
        self.layer_outline_check.setChecked(settings.show_layer_outline)
        self.layer_outline_check.setToolTip(
            "画布上标出当前图层视觉内容范围的蓝色虚线框（定位辅助）。\n"
            "关闭后不再绘制该框，图层定位与自动缩放行为不受影响；\n"
            "切换图层、翻页、改设置均即时生效。"
        )
        display_form.addRow(self.layer_outline_check)

        # 界面缩放：**仅 Android 显示**。桌面端缩放恒为 1.0（由
        # config/settings.resolve_ui_scale 的平台判定硬保证），因此桌面既不
        # 显示这一项、也不读取设置文件里的值，避免误改桌面显示。
        self.ui_scale_combo: Optional[NoWheelComboBox] = None
        if android_ui_scaling():
            self.ui_scale_combo = NoWheelComboBox()
            for scale in ui_scale_choices():
                self.ui_scale_combo.addItem(f"{int(round(scale * 100))}%", scale)
            idx = self.ui_scale_combo.findData(settings.ui_scale)
            if idx < 0:   # 兼容手工写入的非标准档位
                self.ui_scale_combo.addItem(
                    f"{int(round(settings.ui_scale * 100))}%", settings.ui_scale
                )
                idx = self.ui_scale_combo.count() - 1
            self.ui_scale_combo.setCurrentIndex(max(0, idx))
            self.ui_scale_combo.setToolTip(
                "整个界面（含对话框）的显示缩放，安卓端专有；桌面端恒为 100%。\n"
                "默认值按机型给出：手机 55%，折叠屏内屏 / 平板 75%。\n"
                "改动需要**重启应用**后完全生效——Qt 只在启动时读取一次缩放。\n"
                "提示：50% 时文字会非常小；折叠屏内屏上想让顶部工具栏整行不折叠，\n"
                "经验值是 75% 及以下（与设备宽度有关）。"
            )
            display_form.addRow("界面缩放（重启后生效）：", self.ui_scale_combo)

        body_layout.addWidget(display_group)

        # ---- 自动对比 ----
        compare_group = QGroupBox("自动对比")
        compare_form = QFormLayout(compare_group)
        self.compare_mode_combo = NoWheelComboBox()
        self.compare_mode_combo.addItem("自动切换（定时来回闪切）", "auto")
        self.compare_mode_combo.addItem("手动切换（按一下切一次）", "manual")
        mode_idx = self.compare_mode_combo.findData(settings.compare_mode)
        self.compare_mode_combo.setCurrentIndex(max(0, mode_idx))
        compare_form.addRow("对比模式：", self.compare_mode_combo)

        self.compare_speed_combo = NoWheelComboBox()
        for hz, name in COMPARE_SPEED_TIERS:
            self.compare_speed_combo.addItem(
                f"{name} · {hz} 次/秒（每张 {hz_to_interval_ms(hz)}ms）", hz
            )
        current_hz = settings.compare_speed_hz
        if current_hz not in [hz for hz, _ in COMPARE_SPEED_TIERS]:
            self.compare_speed_combo.addItem(
                f"自定义 · {current_hz} 次/秒（每张 {hz_to_interval_ms(current_hz)}ms）",
                current_hz,
            )
        speed_idx = self.compare_speed_combo.findData(current_hz)
        self.compare_speed_combo.setCurrentIndex(max(0, speed_idx))
        self.compare_speed_combo.setToolTip("手动切换模式下无需设定速度")
        compare_form.addRow("切换速度：", self.compare_speed_combo)
        self.compare_mode_combo.currentIndexChanged.connect(
            self._update_compare_speed_enabled
        )
        self._update_compare_speed_enabled()
        body_layout.addWidget(compare_group)

        # ---- 任务 ----
        task_group = QGroupBox("任务")
        task_form = QFormLayout(task_group)
        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.setChecked(settings.recursive_scan)
        task_form.addRow(self.recursive_check)
        # 控制台开关仅 Windows 打包产物有意义（见 mangaproof/console.py）
        import sys as _sys

        self.console_check = QCheckBox("打包产物关闭控制台窗口（仅 Windows）")
        self.console_check.setChecked(settings.hide_console)
        self.console_check.setToolTip(
            "仅对 Windows 打包产物生效（默认关闭）。\n"
            "开启后控制台窗口直接消失（FreeConsole 销毁，不是最小化）；\n"
            "关闭此开关后重新显示控制台窗口（AllocConsole）。\n"
            "macOS / Linux 无独立控制台窗口，由打包参数决定。\n"
            "直接运行 python main.py 时控制台始终显示，不受此开关影响。"
        )
        if _sys.platform != "win32":
            self.console_check.setEnabled(False)
        task_form.addRow(self.console_check)

        # 内存回收策略（三档：宽松/平衡/激进），运行时热应用。
        # Android 端锁定为激进：控件**禁用但保留**（与上面 console_check 同做法），
        # 让用户看得到"现在用的是哪一档"，同时不给改——直接隐藏会让人以为
        # 设置项做丢了。
        self.memory_policy_combo = NoWheelComboBox()
        self.memory_policy_combo.addItem("宽松（LRU 768MB，bg 预生成池 768MB）", "relaxed")
        self.memory_policy_combo.addItem("平衡（LRU 512MB，bg 预生成池 512MB）", "balanced")
        self.memory_policy_combo.addItem("激进（LRU 256MB，bg 预生成仅留 2 张）", "aggressive")
        policy_idx = self.memory_policy_combo.findData(settings.memory_policy)
        self.memory_policy_combo.setCurrentIndex(max(0, policy_idx))
        if android_memory_policy_locked():
            self.memory_policy_combo.setEnabled(False)
            self.memory_policy_combo.setToolTip(
                "Android 端固定为「激进」档，不可更改：移动设备内存紧张，\n"
                "激进档（LRU 256MB、bg 预生成仅留 2 张）能显著降低被系统回收的概率。\n"
                "三档均会驱逐窗口外文档结构（内存与书本页数无关）。"
            )
        else:
            self.memory_policy_combo.setToolTip(
                "内存占用与缓存慷慨度的平衡档位，切换后立即生效，无需重启。\n"
                "三档均会驱逐窗口外文档结构（内存与书本页数无关）。"
            )
        task_form.addRow("内存策略：", self.memory_policy_combo)
        body_layout.addWidget(task_group)

        # ---- 返修单 ----
        report_group = QGroupBox("MangaProof 返修单")
        report_form = QFormLayout(report_group)
        self.pdf_check = QCheckBox("完成监制后自动生成返修单")
        self.pdf_check.setChecked(settings.generate_pdf_on_complete)
        self.pdf_check.setToolTip(
            "勾选（默认）：所有图层检查完毕时自动生成返修单——使用上面的名称、\n"
            "页面图像格式与「总览表隐藏无问题 PSD」选项，同一次完成只生成一次；\n"
            "之后又补加/修改问题再完成时会重新生成。\n"
            "关闭后不会自动生成，可随时用「生成返修单」(Ctrl+R) 手动生成。"
        )
        report_form.addRow(self.pdf_check)
        self.report_name_edit = QLineEdit(settings.report_name)
        self.report_name_edit.setPlaceholderText("留空使用默认名称（PSD 名 / 文件夹名）")
        report_form.addRow("返修单名称：", self.report_name_edit)

        self.report_image_combo = NoWheelComboBox()
        self.report_image_combo.addItem("PNG 无损（默认，体积大）", "png")
        self.report_image_combo.addItem("JPEG 压缩（体积小，有损）", "jpeg")
        fmt_idx = self.report_image_combo.findData(settings.report_image_format)
        self.report_image_combo.setCurrentIndex(max(0, fmt_idx))
        self.report_image_combo.setToolTip(
            "问题明细页的页面图像格式（生成时也可临时改选，选择会被记住）：\n"
            "· PNG：无损，报告体积大；\n"
            "· JPEG：压缩，体积显著变小（漫画页面常见大幅网点/渐变），画质略降；\n"
            "红框与 ①②③ 编号始终是 PDF 矢量，与图片格式无关。"
        )
        report_form.addRow("页面图像：", self.report_image_combo)

        self.report_quality_combo = NoWheelComboBox()
        for q in JPEG_QUALITY_CHOICES:
            self.report_quality_combo.addItem(
                f"{q}%" + ("（默认）" if q == DEFAULT_JPEG_QUALITY else ""), q
            )
        q_idx = self.report_quality_combo.findData(settings.report_jpeg_quality)
        self.report_quality_combo.setCurrentIndex(max(0, q_idx))
        self.report_quality_combo.setToolTip("仅 JPEG 压缩时生效：质量越低体积越小")
        report_form.addRow("JPEG 质量：", self.report_quality_combo)

        self.report_hide_clean_check = QCheckBox(
            "总览表隐藏无问题的 PSD（全部图层通过）"
        )
        self.report_hide_clean_check.setChecked(settings.report_hide_clean_files)
        self.report_hide_clean_check.setToolTip(
            "返修单的「PSD 总览」表默认只列出有问题或未完成的 PSD；\n"
            "全部通过且无问题的页隐藏（表下注明隐藏数量）。\n"
            "生成对话框（Ctrl+R）中可临时改选，选择会被记住。"
        )
        report_form.addRow(self.report_hide_clean_check)
        self.report_image_combo.currentIndexChanged.connect(
            self._update_report_quality_enabled
        )
        self._update_report_quality_enabled()
        body_layout.addWidget(report_group)

        # ---- 快捷键（入口按钮 → 独立子对话框）----
        shortcut_group = QGroupBox("快捷键与滚轮")
        shortcut_form = QFormLayout(shortcut_group)
        self.wheel_mode_combo = NoWheelComboBox()
        self.wheel_mode_combo.addItem("上下移动视图（默认）", "pan")
        self.wheel_mode_combo.addItem("缩放视图", "zoom")
        wheel_idx = self.wheel_mode_combo.findData(settings.wheel_mode)
        self.wheel_mode_combo.setCurrentIndex(max(0, wheel_idx))
        self.wheel_mode_combo.setToolTip(
            "不按修饰键时鼠标滚轮的行为。\n"
            "Ctrl+滚轮始终为缩放，Alt+滚轮始终为左右移动；\n"
            "触控板双指滚不受影响，始终为平移。"
        )
        shortcut_form.addRow("鼠标滚轮（不按修饰键）：", self.wheel_mode_combo)
        self.kb_button = QPushButton("设置快捷键…")
        self.kb_button.setToolTip("在独立窗口中设置核心快捷键与问题类型快捷键")
        self.kb_button.clicked.connect(self._open_keybindings_dialog)
        shortcut_form.addRow("核心 / 问题类型快捷键：", self.kb_button)
        body_layout.addWidget(shortcut_group)

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

    # -- 快捷键子对话框 ----------------------------------------------------

    def _open_keybindings_dialog(self) -> None:
        # 主对话框已点过"恢复默认设置"时，子对话框以默认值为初值
        src = Settings() if self._kb_reset_defaults else self._settings
        dialog = KeybindingsDialog(src, self)
        if dialog.exec() == KeybindingsDialog.DialogCode.Accepted:
            self._kb_dialog = dialog
            self._kb_reset_defaults = False

    # -- 自动对比联动 ------------------------------------------------------

    def _update_compare_speed_enabled(self) -> None:
        manual = self.compare_mode_combo.currentData() == "manual"
        self.compare_speed_combo.setEnabled(not manual)

    def _update_bg_ratio_enabled(self) -> None:
        """bg 独立比例仅在开关打开时可用（关闭时置灰，值保留）。"""
        enabled = self.bg_ratio_check.isChecked()
        self.bg_ratio_combo.setEnabled(enabled)
        self.bg_copy_ratio_combo.setEnabled(enabled)

    def _update_report_quality_enabled(self) -> None:
        """JPEG 质量仅在选择 JPEG 压缩时可用。"""
        self.report_quality_combo.setEnabled(
            self.report_image_combo.currentData() == "jpeg"
        )

    # -- 复位 / 应用 --------------------------------------------------------

    def _reset_defaults(self) -> None:
        idx = self.ratio_combo.findData(DEFAULT_DISPLAY_RATIO)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        self.bg_ratio_check.setChecked(DEFAULT_BG_RATIO_ENABLED)
        idx = self.bg_ratio_combo.findData(DEFAULT_BG_DISPLAY_RATIO)
        self.bg_ratio_combo.setCurrentIndex(max(0, idx))
        idx = self.bg_copy_ratio_combo.findData(DEFAULT_BG_COPY_DISPLAY_RATIO)
        self.bg_copy_ratio_combo.setCurrentIndex(max(0, idx))
        idx = self.compare_mode_combo.findData(DEFAULT_COMPARE_MODE)
        self.compare_mode_combo.setCurrentIndex(max(0, idx))
        idx = self.compare_speed_combo.findData(DEFAULT_COMPARE_SPEED_HZ)
        self.compare_speed_combo.setCurrentIndex(max(0, idx))
        idx = self.wheel_mode_combo.findData(DEFAULT_WHEEL_MODE)
        self.wheel_mode_combo.setCurrentIndex(max(0, idx))
        idx = self.issue_scope_combo.findData(DEFAULT_ISSUE_SCOPE)
        self.issue_scope_combo.setCurrentIndex(max(0, idx))
        self.layer_outline_check.setChecked(DEFAULT_SHOW_LAYER_OUTLINE)
        self.recursive_check.setChecked(False)
        self.console_check.setChecked(True)
        self.pdf_check.setChecked(True)
        self.report_name_edit.clear()
        # 恢复默认走**平台默认**：Android 上该控件已禁用，复位到"平衡"会显示一个
        # 与事实不符的值（实际跑的是激进）。
        idx = self.memory_policy_combo.findData(default_memory_policy())
        self.memory_policy_combo.setCurrentIndex(max(0, idx))
        idx = self.report_image_combo.findData(DEFAULT_REPORT_IMAGE_FORMAT)
        self.report_image_combo.setCurrentIndex(max(0, idx))
        idx = self.report_quality_combo.findData(DEFAULT_JPEG_QUALITY)
        self.report_quality_combo.setCurrentIndex(max(0, idx))
        self.report_hide_clean_check.setChecked(True)
        self._update_report_quality_enabled()
        # 界面缩放（仅 Android 存在该控件）：回到平台默认（Android 75% / 桌面 100%）
        if self.ui_scale_combo is not None:
            idx = self.ui_scale_combo.findData(default_ui_scale())
            self.ui_scale_combo.setCurrentIndex(max(0, idx))
        # 快捷键同样复位：记录"应用默认"意图，OK 时写回默认值
        self._kb_dialog = None
        self._kb_reset_defaults = True

    def apply_to(self, settings: Settings) -> None:
        settings.layer_display_ratio = float(self.ratio_combo.currentData())
        settings.bg_ratio_enabled = self.bg_ratio_check.isChecked()
        settings.bg_display_ratio = float(self.bg_ratio_combo.currentData())
        settings.bg_copy_display_ratio = float(self.bg_copy_ratio_combo.currentData())
        settings.compare_mode = str(self.compare_mode_combo.currentData())
        settings.compare_speed_hz = int(self.compare_speed_combo.currentData())
        settings.wheel_mode = str(self.wheel_mode_combo.currentData())
        settings.issue_scope = str(self.issue_scope_combo.currentData())
        settings.show_layer_outline = self.layer_outline_check.isChecked()
        settings.recursive_scan = self.recursive_check.isChecked()
        settings.generate_pdf_on_complete = self.pdf_check.isChecked()
        settings.report_name = self.report_name_edit.text().strip()
        settings.report_image_format = str(self.report_image_combo.currentData())
        settings.report_jpeg_quality = int(self.report_quality_combo.currentData())
        settings.report_hide_clean_files = self.report_hide_clean_check.isChecked()
        settings.hide_console = self.console_check.isChecked()
        # 过一层平台规整：Android 的控件已禁用，但程序化改下拉仍可能绕过，
        # 收敛在这里能保证写回设置的值永远是本平台该用的档位。
        settings.memory_policy = effective_memory_policy(
            self.memory_policy_combo.currentData()
        )
        if self.ui_scale_combo is not None:   # 仅 Android 存在
            settings.ui_scale = float(self.ui_scale_combo.currentData())

        if self._kb_reset_defaults:
            settings.keybindings = dict(DEFAULT_KEYBINDINGS)
            for i, item in enumerate(settings.issue_types):
                if i < len(DEFAULT_ISSUE_TYPES):
                    item["key"] = DEFAULT_ISSUE_TYPES[i]["key"]
            settings.custom_comment_key = DEFAULT_KEYBINDINGS["custom_comment"]
        elif self._kb_dialog is not None:
            self._kb_dialog.apply_to(settings)
