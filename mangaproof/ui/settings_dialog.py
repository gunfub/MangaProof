# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""设置对话框（需求 §20、§30、§35、§46、§49、§55）。

布局：左侧分类侧边栏 + 右侧分类页。

先看下面这些约定，再改这个文件：

- 侧边栏五项 = 原来的五个小分类（缩放与标注显示 / 对比呈现 / 扫描与运行策略 /
  返修单参数 / 快捷键与滚轮）。点哪一项，右侧只显示那一类的设置项；
- 每个分类页有自己的滚动区，页头是分类名 + 一句说明。页内不再套 QGroupBox：
  分类名已经在侧边栏，再套一个同名外框只是重复；
- 每行 = 左边文本（统一左对齐）+ 右边控件，见 :class:`SettingsRow`：
  下拉框 / 输入框从**本页统一字段列**开始、向右占满到基线——列起点 = 本页最长的
  行文本 + 10px，等价于"左缘对齐到本页最窄的那个下拉框"；开关 / 按钮保持自身
  宽度、贴右缘；
- 一切按**统一基线 GUTTER=24** 排：控件右缘距窗口右缘 24、左侧侧边栏与「恢复默认」
  距窗口左缘 24、侧边栏到内容文本 24、上下留白 24；底部「确定 / 取消」的右缘也
  压在控件右缘这条线上；
- 页头（标题 + 灰色说明 + 1px 分割线）**固定不动**，设置项在它下面的滚动区里：
  内容不超高时滚动条隐藏、控件右缘压在基线上；需要滚动时滚动条出现在最右
  （右缘与「确定 / 取消」对齐），控件右缘让到滚动条左侧 SCROLL_GAP=6px；
- 开关统一用 ToggleSwitch（胶囊形、点一下就切换，见 ui/widgets.py），不再用方块勾选框；
- 控件被禁用时（如手动对比下的「切换速度」、PNG 下的「JPEG 质量」），**该行文本
  跟随置灰**——由 SettingsRow 的事件过滤统一处理，新增设置项不用再单独写；
- 同一分类内的设置项行距 16px（原来 QFormLayout 默认约 6px，挤在一起不好认）；
- 窗口默认 860×560（原来 560×560 太方；与「第三方许可」窗口同尺寸），侧边栏 180；
- 侧边栏文字与内容页标题**同一格式**（15px 粗体），并且页头与侧边栏第一项的文字
  顶端对齐（两边用同一个行高 `_nav_row_height`，各自垂直居中）；
- 下拉框一律 NoWheelComboBox：**只能点开选**，滚轮不改值（见 ui/widgets.py），
  这条是设计要求，不要换回原生 QComboBox；
- 底部「确定 / 取消」：确定保存并应用**所有分类**的改动，取消则本次全部丢弃
  （控件状态只活在对话框里，永远不提前写 Settings）。

**分类页必须一次建好、常驻内存**（不能按需创建，也不能切页时销毁重建）：
用户可能在多个分类里各改几项再点「确定」，若切页时重建控件，先前分类的改动
就丢了。所以这里用 QStackedWidget 只做显示切换，`apply_to()` 按控件逐个读取，
与当前显示哪一页无关。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mangaproof.camera.centering import POSITION_FAST, POSITION_VISUAL
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
    default_position_mode,
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
from mangaproof.ui.theme import (
    COLOR_ACCENT,
    COLOR_BG_PANEL,
    COLOR_BG_SELECTED,
    COLOR_BG_WIDGET,
    COLOR_BG_HOVER,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_WARN,
)
from mangaproof.ui.widgets import NoWheelComboBox, ToggleSwitch

#: 侧边栏分类：内部键 -> (侧边栏 / 页头标题, 页头的一句说明)。
#: 顺序即侧边栏顺序，键同时是 page_widgets / page_rows / page_scrolls 的键。
PAGES: Dict[str, tuple[str, str]] = {
    "display": ("缩放与标注显示", "画布上的自动缩放与叠加显示"),
    "compare": ("对比呈现", "Original 与背景的闪切方式与速度"),
    "task": ("扫描与运行策略", "打开任务时的扫描方式、控制台窗口与内存回收"),
    "report": ("返修单参数", "完成监制后自动生成的返修单：名称、页面图像与总览"),
    "shortcut": ("快捷键与滚轮", "核心 / 问题类型快捷键，以及不按修饰键时的滚轮行为"),
}

#: 统一留白基线：控件右缘距窗口右缘、左侧标题/侧边栏/底部按钮的留白都取它
GUTTER = 24

#: 滚动条出现时，控件右缘与滚动条左侧的间隙（不滚动时控件回到基线，不留这段）
SCROLL_GAP = 6

#: 侧边栏宽度（固定）：15px 粗体下最长项「缩放与标注显示 / 扫描与运行策略」
#: 实宽 105px，180 够用
NAV_WIDTH = 180

#: 侧边栏列表内边距 / 列表项上下内边距：页头靠这两个值 + 边框与第一项文字对齐
NAV_PADDING = 4
NAV_ITEM_VPAD = 8

#: 页头标题与侧边栏项共用的字号 / 字重（反馈要求"侧边栏文本同标题格式"）
HEADER_FONT_QSS = "font-size: 15px; font-weight: bold;"

#: 窗口默认尺寸（原 560×560 太方；与 ui/license_dialog.py 的 860×560 一致）
#: 与最小尺寸（小屏仍可缩，每个分类页各自滚动）
DEFAULT_DIALOG_SIZE = (860, 560)
MINIMUM_DIALOG_SIZE = (720, 440)

#: 同一分类内相邻设置项的纵向间距（原来 QFormLayout 默认约 6px）
ROW_SPACING = 16

#: 行内"文本 → 控件"的间距（下拉框贴着冒号）
ROW_LABEL_GAP = 10


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
        # 与主设置对话框 / 「关于」页一致：没装 Qt 翻译时标准按钮是英文，显式改中文
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
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


class SettingsRow(QWidget):
    """一行设置：左边是文本（统一左对齐），右边是控件（统一右对齐）。

    为什么不用 QFormLayout：需求是"文本统一左对齐、控件统一压在右基线上"，
    而且下拉框要从各自文本之后开始（每行起点因此不同）。QFormLayout 的字段列
    只能整体对齐，两件事都做不到。

    两种排法（``expand``）：

    - ``expand=True``（下拉框 / 输入框）：控件紧跟文本之后（ROW_LABEL_GAP）开始，
      向右**占满**到行的右缘。行本身按自己的文本起始；页面建完后
      ``SettingsDialog._align_page_fields()`` 会把本页填充行的文本补到等宽，
      于是整页的下拉框左缘对齐到"本页最窄的那个下拉框"、右缘一律压基线；
    - ``expand=False``（开关 / 按钮）：控件保持自身宽度、贴行右缘（右对齐）；
    - **控件被禁用时文本一起置灰**：行内监听控件的 ``EnabledChange``，把同样的
      禁用状态施加到文本上（样式见 ui/theme.py 的 ``QLabel#settingsRowLabel:disabled``）。
      这样 ``_update_*_enabled()`` 里只需要管控件，不用逐个记得改文本；
    - 控件上已有的 tooltip 会在建行时复制给文本，鼠标停在文字上也能看到说明。
    """

    def __init__(
        self,
        text: str,
        control: QWidget,
        expand: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.label = QLabel(text)
        self.label.setObjectName("settingsRowLabel")
        self.control = control
        self.expand = expand      # True = 填充型（下拉框/输入框），参与字段列对齐

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ROW_LABEL_GAP)   # 标签与控件之间固定间距（"贴着冒号"）
        row.addWidget(
            self.label, 0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        if expand:
            control.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            row.addWidget(control, 1)
        else:
            control.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
            row.addStretch(1)
            row.addWidget(
                control, 0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        if isinstance(control, ToggleSwitch):
            # 开关本身没有文字，读屏软件用的是行文本
            control.setAccessibleName(text)
        if control.toolTip():
            self.label.setToolTip(control.toolTip())

        control.installEventFilter(self)
        self.label.setEnabled(control.isEnabled())

    def eventFilter(self, obj, event) -> bool:   # noqa: N802（Qt 命名）
        """控件启用状态变化 → 文本跟着置灰/恢复。"""
        if obj is self.control and event.type() == QEvent.Type.EnabledChange:
            self.label.setEnabled(self.control.isEnabled())
        return super().eventFilter(obj, event)


class SettingsDialog(QDialog):
    """程序设置主对话框：左侧分类侧边栏 + 右侧分类页。

    每个分类页都有自己的滚动区（768p 笔记本上内容也不会把底部按钮挤出去）；
    底部「恢复默认设置 / 确定 / 取消」固定在分类区之外，任何窗口高度下都
    可见可点。「确定」写回**所有分类**的改动，「取消」全部丢弃（含窗口 X / Esc）。
    """

    def __init__(
        self,
        settings: Settings,
        parent: Optional[QWidget] = None,
        intro: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("MangaProof 设置")
        # 默认横向（原 560×560 太方）：侧边栏 180 + 内容区约 640
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*MINIMUM_DIALOG_SIZE)
        self._settings = settings
        self._kb_dialog: Optional[KeybindingsDialog] = None
        self._kb_reset_defaults = False

        #: 分类键（见模块级 PAGES）-> 页面控件 / 行容器 / 滚动区 / 表头部件
        self.page_widgets: Dict[str, QWidget] = {}
        self.page_rows: Dict[str, QVBoxLayout] = {}
        self.page_scrolls: Dict[str, QScrollArea] = {}
        self.page_titles: Dict[str, QLabel] = {}
        self.page_hints: Dict[str, QLabel] = {}
        self.page_dividers: Dict[str, QFrame] = {}
        self.page_contents: Dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        # 四边留白统一用基线值：左侧与右缘（控件基线到窗口边）对称
        layout.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        layout.setSpacing(10)

        # 首次使用引导文案（可选）：只说明「这里是全部设置项、不改就用默认值」，
        # 不预设、不推荐任何值——放在分类区之外，任何窗口高度下都看得见
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

        # ---- 主体：左侧分类 + 右侧分类页 ----
        body = QHBoxLayout()
        body.setSpacing(GUTTER)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("settingsNav")
        self.nav_list.setFixedWidth(NAV_WIDTH)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.nav_list.setStyleSheet(
            f"""
            QListWidget#settingsNav {{
                background: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: {NAV_PADDING}px;
                outline: none;
                {HEADER_FONT_QSS}
            }}
            QListWidget#settingsNav::item {{
                padding: {NAV_ITEM_VPAD}px 10px;
                margin: 0px;
                border-radius: 5px;
            }}
            QListWidget#settingsNav::item:selected {{
                background: {COLOR_BG_SELECTED};
                color: white;
            }}
            QListWidget#settingsNav::item:hover {{
                background: {COLOR_BG_HOVER};
            }}
            """
        )
        # 侧边栏文字与页头同一格式（15px 粗体）。绘制由上面的 QSS 负责；
        # 这里再设一次同尺寸 QFont，用它算出统一行高——QSS 的字号要等窗口
        # 显示后才进 fontMetrics()，建页时算不准。
        header_font = self.nav_list.font()
        header_font.setPixelSize(15)
        header_font.setBold(True)
        self.nav_list.setFont(header_font)
        self._nav_row_height = (
            QFontMetrics(header_font).height() + 2 * NAV_ITEM_VPAD
        )
        body.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        layout.addLayout(body, 1)

        # 五个分类页：**一次全部建好**（切页只切显示，不销毁重建）。
        # 顺序即侧边栏顺序，也是 PAGES 的插入顺序。
        self._build_display_page(self._add_page("display"))
        self._build_compare_page(self._add_page("compare"))
        self._build_task_page(self._add_page("task"))
        self._build_report_page(self._add_page("report"))
        self._build_shortcut_page(self._add_page("shortcut"))
        # 行全部加完之后再收尾：
        # 1) 同一页的下拉框/输入框统一从"本页最长文本之后"开始（= 最窄那个下拉框的左缘）；
        # 2) 内容不足一屏时整体贴顶，多出的高度留在下方。
        for rows in self.page_rows.values():
            self._align_page_fields(rows)
            rows.addStretch(1)

        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setCurrentRow(0)

        # ---- 按钮 ----
        button_row = QHBoxLayout()
        self.reset_btn = QPushButton("恢复默认设置")
        self.reset_btn.clicked.connect(self._reset_defaults)
        button_row.addWidget(self.reset_btn)
        button_row.addStretch(1)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        # 程序没装 Qt 翻译，标准按钮默认是英文 "OK / Cancel"；这里是全中文界面，显式改中文
        # （与 ui/about_dialog.py、ui/license_dialog.py 的做法一致）
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        button_row.addWidget(self.button_box)
        layout.addLayout(button_row)

    # -- 分类页骨架 --------------------------------------------------------

    def _add_page(self, key: str) -> QVBoxLayout:
        """建一个分类页（侧边栏项 + 页头 + 独立滚动区），返回它的「行」容器。

        页面在这里就**建好并常驻**：切页只切 QStackedWidget 的显示，绝不重建，
        否则用户先改 A 分类、再改 B 分类时，A 的改动会随控件销毁一起丢掉。
        """
        title, hint = PAGES[key]

        # 侧边栏项先建：页头要按它的行高 / 文字格式对齐
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(QSize(0, self._nav_row_height))
        self.nav_list.addItem(item)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        # 与侧边栏第一项的文字顶端对齐：下沉「列表边框 1px + 列表内边距」
        page_layout.setContentsMargins(0, 1 + NAV_PADDING, 0, 0)
        page_layout.setSpacing(0)      # 页头三段的距离全部显式给，便于精确控制

        title_label = QLabel(title)
        title_label.setObjectName("settingsPageTitle")
        title_label.setStyleSheet(HEADER_FONT_QSS)
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        # 与侧边栏项同高：两边文字都在各自的盒子里垂直居中，文字顶端自然对齐
        title_label.setFixedHeight(self._nav_row_height)
        page_layout.addWidget(title_label)
        page_layout.addSpacing(2)
        self.page_titles[key] = title_label

        hint_label = QLabel(hint)
        hint_label.setObjectName("settingsPageHint")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        page_layout.addWidget(hint_label)
        self.page_hints[key] = hint_label

        # 分割线：说明与设置项之间，上下留空都取设置项行距（ROW_SPACING）。
        # 它属于固定页头——滚动区在它下面，翻页时标题/说明/分割线都不动。
        page_layout.addSpacing(ROW_SPACING)
        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLOR_BORDER}; border: none;")
        page_layout.addWidget(divider)
        page_layout.addSpacing(ROW_SPACING)
        self.page_dividers[key] = divider

        # 每页自己的滚动区：内容再长也只滚这一页，底部按钮始终在滚动区外
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        # 顶部不再留白（页头下方的 16px 已经给了）；右侧间隙由滚动条显示时动态补
        content_layout.setContentsMargins(0, 0, 0, 14)
        content_layout.setSpacing(0)

        # 设置项一行一个（SettingsRow）：文本统一左对齐、控件统一右对齐。
        # 行距用 layout.spacing 控制，和页头之间留 8px（上面的 top margin）。
        rows = QVBoxLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(ROW_SPACING)
        content_layout.addLayout(rows)
        scroll.setWidget(content)
        page_layout.addWidget(scroll, 1)

        # 滚动条显示/隐藏时同步内容右侧间隙（见 eventFilter）
        scroll.verticalScrollBar().installEventFilter(self)
        self.stack.addWidget(page)
        self.page_widgets[key] = page
        self.page_rows[key] = rows
        self.page_scrolls[key] = scroll
        self.page_contents[key] = content
        return rows

    def _add_row(
        self,
        rows: QVBoxLayout,
        text: str,
        control: QWidget,
        expand: bool = False,
    ) -> SettingsRow:
        """往分类页里添一行设置。

        - ``expand=True``：下拉框 / 输入框从文本后 ROW_LABEL_GAP 起、占满到行的右缘；
        - ``expand=False``：开关 / 按钮保持自身宽度、贴行右缘。

        注意：控件的 tooltip 要在调用本方法**之前**设好——建行时会把它复制给文本。
        """
        row = SettingsRow(text, control, expand, rows.parentWidget())
        rows.addWidget(row)
        return row

    def _align_page_fields(self, rows: QVBoxLayout) -> None:
        """把同一页的填充型控件对齐到同一条左缘（= 本页最窄的那个下拉框）。

        所有控件右缘都压在基线上，所以"左缘对齐到最窄的下拉框"等价于：取本页
        最长的行文本宽度作为字段列起点，其余行的文本补足到这个宽度。

        只统计下拉框 / 输入框行：开关行的文案通常比下拉标签长得多（例如
        「bg / bg 拷贝 使用独立的自动显示比例」），算进去会把整列无谓地推右，
        而开关本身是贴右缘的、不参与这一列。列宽由字体度量实时算出，改文案
        或加设置项都会自动重排。
        """
        fill_rows: list[SettingsRow] = []
        for i in range(rows.count()):
            widget = rows.itemAt(i).widget()
            if isinstance(widget, SettingsRow) and widget.expand:
                fill_rows.append(widget)
        if not fill_rows:
            return
        column = max(row.label.sizeHint().width() for row in fill_rows)
        for row in fill_rows:
            row.label.setMinimumWidth(column)

    def eventFilter(self, obj, event) -> bool:   # noqa: N802（Qt 命名）
        """滚动条显示/隐藏 → 同步该页内容的右侧间隙。

        有滚动条时控件右缘让到滚动条左侧 6px；没有滚动条时收回这 6px，
        控件右缘继续压在基线上（与底部「确定 / 取消」同一条线）。
        """
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            for key, scroll in self.page_scrolls.items():
                if scroll.verticalScrollBar() is obj:
                    self._set_content_gap(
                        key,
                        SCROLL_GAP if event.type() == QEvent.Type.Show else 0,
                    )
                    break
        return super().eventFilter(obj, event)

    def _set_content_gap(self, key: str, gap: int) -> None:
        """把某页内容容器的右侧内边距设为 gap（0 = 收回基线）。"""
        content = self.page_contents[key]
        margins = content.layout().contentsMargins()
        if margins.right() == gap:
            return
        content.layout().setContentsMargins(
            margins.left(), margins.top(), gap, margins.bottom()
        )

    def _on_nav_changed(self, row: int) -> None:
        """侧边栏切换：只切显示，不重建控件（见模块 docstring 的说明）。"""
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    # -- 各分类页内容 ------------------------------------------------------

    def _build_display_page(self, rows: QVBoxLayout) -> None:
        self.ratio_combo = NoWheelComboBox()
        for r in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(r * 100)}%", r)
        current_ratio = self._settings.layer_display_ratio
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
        self._add_row(rows, "图层自动显示比例：", self.ratio_combo, expand=True)

        # 图层定位依据（需求 §17、§18）：两档取舍，不是开关——开关只有一句话
        # 的空间，说不清"快在哪、差在哪"；下拉与「对比模式 / 问题红框显示」同类。
        #
        # 写代码的人注意（**不要**写进 tooltip，用户界面不需要这条）：
        # 后续任何"某些图层居中更准"的改进只做在视觉内容模式（visual 分支），
        # 快速模式是纯元数据路径，见 camera/centering.py 的模块不变量。
        self.position_combo = NoWheelComboBox()
        self.position_combo.addItem("视觉内容边界（精准，逐层解析像素）", POSITION_VISUAL)
        self.position_combo.addItem("PSD 图层原始框（快速，零像素解析）", POSITION_FAST)
        position_idx = self.position_combo.findData(self._settings.position_mode)
        self.position_combo.setCurrentIndex(max(0, position_idx))
        self.position_combo.setToolTip(
            "定位与自动缩放以哪个矩形为准：\n"
            "· 视觉内容边界（桌面默认）：逐层解析 alpha、取实际内容包围盒，居中更准；\n"
            "  代价是首次切换要解图层像素（大图层有等待，且占内存）。\n"
            "· PSD 图层原始框（Android 默认）：直接读 PSD 里该图层自带的 x / y / w / h，\n"
            "  零像素解析，切换与定位瞬时完成；图层有透明留白、蒙版或特效时，\n"
            "  居中会不如上面精准——这是刻意的取舍。\n"
            "快速模式下：蓝色虚线框画的就是原始框，图层预热不再进行（图像预加载照旧），\n"
            "自动框选也直接用原始框。"
        )
        self._add_row(rows, "图层定位依据：", self.position_combo, expand=True)

        # bg / bg 拷贝 的独立比例（需求 §20.2，默认关）。
        # 关闭时两个下拉置灰：值仍保留在设置里，只是不参与自动缩放。
        self.bg_ratio_check = ToggleSwitch()
        self.bg_ratio_check.setChecked(self._settings.bg_ratio_enabled)
        self.bg_ratio_check.setToolTip(
            "开启后，只有下面这两类图层用自己的比例，其余图层仍用上面的全局比例：\n"
            "· bg：PS 里名为 bg 的图层；没有 bg 名时按既有规则退回「最底部有内容的图层」；\n"
            "· bg 拷贝：名为「bg 拷贝」（中文版）或「bg copy」（英文版）的图层，\n"
            "  没有这一层就不套用独立比例、也不做兜底。\n"
            "关闭（默认）时所有图层统一用全局比例，行为与旧版一致。"
        )
        self._add_row(rows, "bg / bg 拷贝 使用独立的自动显示比例", self.bg_ratio_check)

        self.bg_ratio_combo = NoWheelComboBox()
        self.bg_copy_ratio_combo = NoWheelComboBox()
        for combo, current in (
            (self.bg_ratio_combo, self._settings.bg_display_ratio),
            (self.bg_copy_ratio_combo, self._settings.bg_copy_display_ratio),
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
        self._add_row(rows, "bg 自动显示比例：", self.bg_ratio_combo, expand=True)
        self._add_row(
            rows, "bg 拷贝自动显示比例：", self.bg_copy_ratio_combo, expand=True
        )

        self.issue_scope_combo = NoWheelComboBox()
        self.issue_scope_combo.addItem("当前页全部问题（默认，跨图层显示红框）", "page")
        self.issue_scope_combo.addItem("仅当前图层的问题（旧版行为）", "layer")
        scope_idx = self.issue_scope_combo.findData(self._settings.issue_scope)
        self.issue_scope_combo.setCurrentIndex(max(0, scope_idx))
        self.issue_scope_combo.setToolTip(
            "画布上红框（问题标注）的显示范围：\n"
            "· 当前页全部问题：当前 PSD 的所有问题都显示，翻到哪页就看全哪页的标注；\n"
            "· 仅当前图层：只显示当前图层的问题，其余图层红框隐藏。\n"
            "切换图层/翻页即时生效，不影响已保存的问题数据。"
        )
        self._add_row(rows, "问题红框显示：", self.issue_scope_combo, expand=True)

        self.layer_outline_check = ToggleSwitch()
        self.layer_outline_check.setChecked(self._settings.show_layer_outline)
        self.layer_outline_check.setToolTip(
            "画布上标出当前图层内容范围的蓝色虚线框（定位辅助）。\n"
            "框的语义跟随上面的「图层定位依据」：视觉内容模式画实际内容边界，\n"
            "快速模式画 PSD 图层原始框（与本次居中用的矩形同源）。\n"
            "关闭后不再绘制该框，图层定位与自动缩放行为不受影响；\n"
            "切换图层、翻页、改设置均即时生效。"
        )
        self._add_row(rows, "显示当前图层的蓝色虚线边界框", self.layer_outline_check)

        # 界面缩放：**仅 Android 显示**。桌面端缩放恒为 1.0（由
        # config/settings.resolve_ui_scale 的平台判定硬保证），因此桌面既不
        # 显示这一项、也不读取设置文件里的值，避免误改桌面显示。
        self.ui_scale_combo: Optional[NoWheelComboBox] = None
        if android_ui_scaling():
            self.ui_scale_combo = NoWheelComboBox()
            for scale in ui_scale_choices():
                self.ui_scale_combo.addItem(f"{int(round(scale * 100))}%", scale)
            idx = self.ui_scale_combo.findData(self._settings.ui_scale)
            if idx < 0:   # 兼容手工写入的非标准档位
                self.ui_scale_combo.addItem(
                    f"{int(round(self._settings.ui_scale * 100))}%",
                    self._settings.ui_scale,
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
            self._add_row(
                rows, "界面缩放（重启后生效）：", self.ui_scale_combo, expand=True
            )

    def _build_compare_page(self, rows: QVBoxLayout) -> None:
        self.compare_mode_combo = NoWheelComboBox()
        self.compare_mode_combo.addItem("自动切换（定时来回闪切）", "auto")
        self.compare_mode_combo.addItem("手动切换（按一下切一次）", "manual")
        mode_idx = self.compare_mode_combo.findData(self._settings.compare_mode)
        self.compare_mode_combo.setCurrentIndex(max(0, mode_idx))
        self._add_row(rows, "对比模式：", self.compare_mode_combo, expand=True)

        self.compare_speed_combo = NoWheelComboBox()
        for hz, name in COMPARE_SPEED_TIERS:
            self.compare_speed_combo.addItem(
                f"{name} · {hz} 次/秒（每张 {hz_to_interval_ms(hz)}ms）", hz
            )
        current_hz = self._settings.compare_speed_hz
        if current_hz not in [hz for hz, _ in COMPARE_SPEED_TIERS]:
            self.compare_speed_combo.addItem(
                f"自定义 · {current_hz} 次/秒（每张 {hz_to_interval_ms(current_hz)}ms）",
                current_hz,
            )
        speed_idx = self.compare_speed_combo.findData(current_hz)
        self.compare_speed_combo.setCurrentIndex(max(0, speed_idx))
        self.compare_speed_combo.setToolTip("手动切换模式下无需设定速度")
        self._add_row(rows, "切换速度：", self.compare_speed_combo, expand=True)
        self.compare_mode_combo.currentIndexChanged.connect(
            self._update_compare_speed_enabled
        )
        self._update_compare_speed_enabled()

    def _build_task_page(self, rows: QVBoxLayout) -> None:
        self.recursive_check = ToggleSwitch()
        self.recursive_check.setChecked(self._settings.recursive_scan)
        self._add_row(rows, "递归扫描子文件夹", self.recursive_check)
        # 控制台开关仅 Windows 打包产物有意义（见 mangaproof/console.py）
        import sys as _sys

        self.console_check = ToggleSwitch()
        self.console_check.setChecked(self._settings.hide_console)
        self.console_check.setToolTip(
            "仅对 Windows 打包产物生效（默认关闭）。\n"
            "开启后控制台窗口直接消失（FreeConsole 销毁，不是最小化）；\n"
            "关闭此开关后重新显示控制台窗口（AllocConsole）。\n"
            "macOS / Linux 无独立控制台窗口，由打包参数决定。\n"
            "直接运行 python main.py 时控制台始终显示，不受此开关影响。"
        )
        if _sys.platform != "win32":
            self.console_check.setEnabled(False)
        self._add_row(rows, "打包产物关闭控制台窗口（仅 Windows）", self.console_check)

        # 内存回收策略（三档：宽松/平衡/激进），运行时热应用。
        # Android 端锁定为激进：控件**禁用但保留**（与上面 console_check 同做法），
        # 让用户看得到"现在用的是哪一档"，同时不给改——直接隐藏会让人以为
        # 设置项做丢了。
        self.memory_policy_combo = NoWheelComboBox()
        self.memory_policy_combo.addItem("宽松（LRU 768MB，bg 预生成池 768MB）", "relaxed")
        self.memory_policy_combo.addItem("平衡（LRU 512MB，bg 预生成池 512MB）", "balanced")
        self.memory_policy_combo.addItem("激进（LRU 256MB，bg 预生成仅留 2 张）", "aggressive")
        policy_idx = self.memory_policy_combo.findData(self._settings.memory_policy)
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
        self._add_row(rows, "内存策略：", self.memory_policy_combo, expand=True)

    def _build_report_page(self, rows: QVBoxLayout) -> None:
        self.pdf_check = ToggleSwitch()
        self.pdf_check.setChecked(self._settings.generate_pdf_on_complete)
        self.pdf_check.setToolTip(
            "勾选（默认）：所有图层检查完毕时自动生成返修单——使用上面的名称、\n"
            "页面图像格式与「总览表隐藏无问题 PSD」选项，同一次完成只生成一次；\n"
            "之后又补加/修改问题再完成时会重新生成。\n"
            "生成前是否弹选项窗口由下面的开关控制（默认弹）。\n"
            "关闭后不会自动生成，可随时用「生成返修单」(Ctrl+R) 手动生成。"
        )
        self._add_row(rows, "完成监制后自动生成返修单", self.pdf_check)

        self.report_options_check = ToggleSwitch()
        self.report_options_check.setChecked(self._settings.report_show_options)
        self.report_options_check.setToolTip(
            "勾选（默认）：每次生成返修单前都先弹出选项窗口（名称 / 页面图像格式 /\n"
            "JPEG 质量 / 总览表是否隐藏无问题 PSD）——手动生成 (Ctrl+R) 与完成后\n"
            "自动生成都受这个开关控制；窗口里改过的格式 / 质量 / 总览选择会被记住。\n"
            "关闭后不再弹窗，直接用本页的设置生成。"
        )
        self._add_row(rows, "生成返修单前显示选项窗口", self.report_options_check)

        self.report_name_edit = QLineEdit(self._settings.report_name)
        self.report_name_edit.setPlaceholderText("留空使用默认名称（PSD 名 / 文件夹名）")
        self.report_name_edit.setMinimumWidth(320)
        self._add_row(rows, "返修单名称：", self.report_name_edit, expand=True)

        self.report_image_combo = NoWheelComboBox()
        self.report_image_combo.addItem("PNG 无损（默认，体积大）", "png")
        self.report_image_combo.addItem("JPEG 压缩（体积小，有损）", "jpeg")
        fmt_idx = self.report_image_combo.findData(self._settings.report_image_format)
        self.report_image_combo.setCurrentIndex(max(0, fmt_idx))
        self.report_image_combo.setToolTip(
            "问题明细页的页面图像格式（生成时也可临时改选，选择会被记住）：\n"
            "· PNG：无损，报告体积大；\n"
            "· JPEG：压缩，体积显著变小（漫画页面常见大幅网点/渐变），画质略降；\n"
            "红框与 ①②③ 编号始终是 PDF 矢量，与图片格式无关。"
        )
        self._add_row(rows, "页面图像：", self.report_image_combo, expand=True)

        self.report_quality_combo = NoWheelComboBox()
        for q in JPEG_QUALITY_CHOICES:
            self.report_quality_combo.addItem(
                f"{q}%" + ("（默认）" if q == DEFAULT_JPEG_QUALITY else ""), q
            )
        q_idx = self.report_quality_combo.findData(self._settings.report_jpeg_quality)
        if q_idx < 0:
            # 非档位值（settings 加载时已回落；这里兜底直接构造的 Settings）
            q_idx = self.report_quality_combo.findData(DEFAULT_JPEG_QUALITY)
        self.report_quality_combo.setCurrentIndex(max(0, q_idx))
        self.report_quality_combo.setToolTip("仅 JPEG 压缩时生效：质量越低体积越小")
        self._add_row(rows, "JPEG 质量：", self.report_quality_combo, expand=True)

        self.report_hide_clean_check = ToggleSwitch()
        self.report_hide_clean_check.setChecked(self._settings.report_hide_clean_files)
        self.report_hide_clean_check.setToolTip(
            "返修单的「PSD 总览」表默认只列出有问题或未完成的 PSD；\n"
            "全部通过且无问题的页隐藏（表下注明隐藏数量）。\n"
            "开启选项窗口时可在生成前临时改选，选择会被记住；\n"
            "关闭选项窗口时一律以本页的设置为准。"
        )
        self._add_row(
            rows, "总览表隐藏无问题的 PSD（全部图层通过）", self.report_hide_clean_check
        )
        self.report_image_combo.currentIndexChanged.connect(
            self._update_report_quality_enabled
        )
        self._update_report_quality_enabled()

    def _build_shortcut_page(self, rows: QVBoxLayout) -> None:
        self.wheel_mode_combo = NoWheelComboBox()
        self.wheel_mode_combo.addItem("上下移动视图（默认）", "pan")
        self.wheel_mode_combo.addItem("缩放视图", "zoom")
        wheel_idx = self.wheel_mode_combo.findData(self._settings.wheel_mode)
        self.wheel_mode_combo.setCurrentIndex(max(0, wheel_idx))
        self.wheel_mode_combo.setToolTip(
            "不按修饰键时鼠标滚轮的行为。\n"
            "Ctrl+滚轮始终为缩放，Alt+滚轮始终为左右移动；\n"
            "触控板双指滚不受影响，始终为平移。"
        )
        self._add_row(
            rows, "鼠标滚轮（不按修饰键）：", self.wheel_mode_combo, expand=True
        )

        self.kb_button = QPushButton("设置快捷键…")
        self.kb_button.setToolTip("在独立窗口中设置核心快捷键与问题类型快捷键")
        self.kb_button.clicked.connect(self._open_keybindings_dialog)
        self._add_row(rows, "核心 / 问题类型快捷键：", self.kb_button)

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
        """把**所有分类**的控件复位到默认值。

        只是改控件，不直接改 Settings——仍需点「确定」才生效（点「取消」等于没做过）。
        """
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
        # 定位依据回到**平台默认**（桌面视觉边界 / Android 快速原始框），
        # 与 default_position_mode()、SettingsManager 缺键默认同源。
        idx = self.position_combo.findData(default_position_mode())
        self.position_combo.setCurrentIndex(max(0, idx))
        self.recursive_check.setChecked(False)
        self.console_check.setChecked(True)
        self.pdf_check.setChecked(True)
        self.report_options_check.setChecked(True)
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
        """把**所有分类**的当前控件值写回 settings（只在「确定」时调用）。

        取值一律按控件读，与当前显示的是哪个分类页无关：五个页面在构造时一次
        建好并常驻（QStackedWidget 只切显示），因此用户在多个分类里改过的值
        会在这里一次性全部写回。**不能**改成"只保存当前可见页"。
        """
        settings.layer_display_ratio = float(self.ratio_combo.currentData())
        settings.bg_ratio_enabled = self.bg_ratio_check.isChecked()
        settings.bg_display_ratio = float(self.bg_ratio_combo.currentData())
        settings.bg_copy_display_ratio = float(self.bg_copy_ratio_combo.currentData())
        settings.compare_mode = str(self.compare_mode_combo.currentData())
        settings.compare_speed_hz = int(self.compare_speed_combo.currentData())
        settings.wheel_mode = str(self.wheel_mode_combo.currentData())
        settings.issue_scope = str(self.issue_scope_combo.currentData())
        settings.show_layer_outline = self.layer_outline_check.isChecked()
        settings.position_mode = str(self.position_combo.currentData())
        settings.recursive_scan = self.recursive_check.isChecked()
        settings.generate_pdf_on_complete = self.pdf_check.isChecked()
        settings.report_show_options = self.report_options_check.isChecked()
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
