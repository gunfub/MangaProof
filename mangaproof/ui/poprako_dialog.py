# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""「同步到 Poprako…」对话框（1.2.0 需求 §10；设计文档 §10）。

四页：**账号 / 目标章节 / 页面映射 / 预览与上传**，左侧分类导航，底部一行
「上下文状态 + 关闭」。

版式遵循 `docs/设置页设计规范.md` 的完整形态（侧栏 180 / GUTTER 24 / 行距 16 /
文本与控件间距 10 / 控件右缘压基线 / 禁用时行文本一起置灰 / 下拉框禁滚轮）。

.. important::

   **本文件自带一套版式骨架**（:class:`_Row` / :class:`_Page` / 几何常量），
   **刻意不从** ``ui/settings_dialog.py`` 抽取共用模块。原因见
   ``docs/1.2.0开发需求.md`` §4.3：后续平台可能按自己的需求改界面（页面划分、
   控件组合、预览形式），共用骨架会把"改一个平台"变成"改所有平台"。
   所以这里宁可有几十行看起来重复的排版代码。

底部不是「确定 / 取消」：本窗口是同步工作区而不是设置表单——登录、选择、映射
即时生效，唯一的破坏性动作「上传」由它自己的二次确认把关（需求 §10.12）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mangaproof import APP_NAME
from mangaproof.config.settings import PoprakoSettings
from mangaproof.poprako import PLATFORM_NAME
from mangaproof.poprako.client import PoprakoClient
from mangaproof.poprako.convert import PreflightReport, build_payload
from mangaproof.poprako.credentials import (
    clear_token,
    keyring_available,
    load_token,
    save_token,
)
from mangaproof.poprako.mapping import align_in_order, auto_match
from mangaproof.poprako.models import (
    PHASE_LABELS,
    Chapter,
    Membership,
    PageArtwork,
    RemoteIssue,
    User,
    roles_text,
    stage_phases,
)
from mangaproof.review.state import TaskState
from mangaproof.ui.poprako_worker import CallWorker, UploadOutcome, UploadWorker, adopt_worker
from mangaproof.ui.theme import (
    COLOR_BG_HOVER,
    COLOR_BG_PANEL,
    COLOR_BG_SELECTED,
    COLOR_BORDER,
    COLOR_FAIL,
    COLOR_PASS,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_WARN,
)
from mangaproof.ui.widgets import NoWheelComboBox, ToggleSwitch
from mangaproof.utils.platform import is_android_strict

log = logging.getLogger("mangaproof.ui.poprako_dialog")

# ---------------------------------------------------------------------------
# 版式常量（本文件私有；与设置页视觉一致，但不共享代码）
# ---------------------------------------------------------------------------

GUTTER = 24
SCROLL_GAP = 6
NAV_WIDTH = 180
NAV_PADDING = 4
NAV_ITEM_VPAD = 8
HEADER_FONT_QSS = "font-size: 15px; font-weight: bold;"
DEFAULT_DIALOG_SIZE = (860, 560)
MINIMUM_DIALOG_SIZE = (720, 440)
ROW_SPACING = 16
ROW_LABEL_GAP = 10

#: 侧栏四项 = 四页；顺序即流程顺序
PAGES: dict[str, tuple[str, str]] = {
    "account": ("账号", "登录 Poprako；密码不保存，令牌优先存系统凭据库"),
    "target": ("目标章节", "选择要写入的团队 / 项目集 / 作品 / 章节"),
    "mapping": ("页面映射", "把本地 PSD 对应到章节的复合图页；对不上的页不会被写入"),
    "upload": ("预览与上传", "上传会用本次结果整体替换该章节的全部问题"),
}

#: 预览区最多显示的字符数（导出/复制始终是完整 JSON）
PREVIEW_LIMIT = 20_000

#: 状态行最多显示的字数（超出截断，避免把窗口撑宽）
STATUS_LIMIT = 120


class _Row(QWidget):
    """一行：左文本（统一左对齐）+ 右控件（填充型或右对齐型）。"""

    def __init__(self, text: str, control: QWidget, expand: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.label = QLabel(text)
        self.label.setObjectName("settingsRowLabel")   # 复用主题里的置灰规则
        self.control = control
        self.expand = expand

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ROW_LABEL_GAP)
        row.addWidget(self.label, 0,
                      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if expand:
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(control, 1)
        else:
            control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row.addStretch(1)
            row.addWidget(control, 0,
                          Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(control, ToggleSwitch):
            control.setAccessibleName(text)
        if control.toolTip():
            self.label.setToolTip(control.toolTip())
        control.installEventFilter(self)
        self.label.setEnabled(control.isEnabled())

    def eventFilter(self, obj, event) -> bool:   # noqa: N802（Qt 命名）
        if obj is self.control and event.type() == QEvent.Type.EnabledChange:
            self.label.setEnabled(self.control.isEnabled())
        return super().eventFilter(obj, event)


class _Page(QWidget):
    """一页：固定页头（标题 + 说明 + 分割线）+ 正文。

    正文两种形态：``enable_scroll()`` 给内容可能超一屏的页（账号 / 目标章节），
    其余页直接往 ``self.body`` 里放（表格与预览自带滚动，避免双滚动条）。
    """

    def __init__(self, title: str, hint: str, nav_height: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 1 + NAV_PADDING, 0, 0)
        page_layout.setSpacing(0)

        title_label = QLabel(title)
        title_label.setObjectName("syncPageTitle")
        title_label.setStyleSheet(HEADER_FONT_QSS)
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        title_label.setFixedHeight(nav_height)
        page_layout.addWidget(title_label)

        page_layout.addSpacing(2)
        hint_label = QLabel(hint)
        hint_label.setObjectName("syncPageHint")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        page_layout.addWidget(hint_label)

        page_layout.addSpacing(ROW_SPACING)
        divider = QFrame()
        divider.setObjectName("syncDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLOR_BORDER}; border: none;")
        page_layout.addWidget(divider)
        page_layout.addSpacing(ROW_SPACING)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(ROW_SPACING)
        page_layout.addLayout(self.body, 1)

        self.rows: Optional[QVBoxLayout] = None
        self.scroll: Optional[QScrollArea] = None
        self._content: Optional[QWidget] = None

    def enable_scroll(self) -> QVBoxLayout:
        """正文改成独立滚动区，返回「行」容器。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 14)
        content_layout.setSpacing(0)

        rows = QVBoxLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(ROW_SPACING)
        content_layout.addLayout(rows)
        scroll.setWidget(content)

        self.body.addWidget(scroll, 1)
        self.scroll, self.rows, self._content = scroll, rows, content
        scroll.verticalScrollBar().installEventFilter(self)
        return rows

    def eventFilter(self, obj, event) -> bool:   # noqa: N802（Qt 命名）
        """滚动条出现/隐藏时同步右侧让位（规范 §7）。"""
        if (
            self.scroll is not None
            and obj is self.scroll.verticalScrollBar()
            and event.type() in (QEvent.Type.Show, QEvent.Type.Hide)
            and self._content is not None
        ):
            margin = SCROLL_GAP if self.scroll.verticalScrollBar().isVisible() else 0
            self._content.layout().setContentsMargins(0, 0, margin, 14)
        return super().eventFilter(obj, event)


def _align_fields(rows: QVBoxLayout) -> None:
    """把本页填充型行的文本补到等宽（= 字段列对齐到本页最长标签）。"""
    fill_rows: list[_Row] = []
    for index in range(rows.count()):
        widget = rows.itemAt(index).widget()
        if isinstance(widget, _Row) and widget.expand:
            fill_rows.append(widget)
    if not fill_rows:
        return
    width = 0
    for row in fill_rows:
        metrics = QFontMetrics(row.label.font())
        width = max(width, metrics.horizontalAdvance(row.label.text()))
    for row in fill_rows:
        row.label.setMinimumWidth(width)


class PoprakoSyncDialog(QDialog):
    """Poprako 监制数据同步对话框。"""

    #: 任务绑定（映射/章节/上次上传记录）有变化 → 主窗口负责写任务文件
    binding_changed = Signal()
    #: 平台设置（基址/令牌/记住登录/上次选择）有变化 → 主窗口负责写 settings.json
    settings_committed = Signal()

    def __init__(
        self,
        task: TaskState,
        base_dir: Path,
        settings,
        *,
        client_factory: Optional[Callable[[str, str], PoprakoClient]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 监制数据同步")
        self.setModal(True)
        self.resize(*DEFAULT_DIALOG_SIZE)
        self.setMinimumSize(*MINIMUM_DIALOG_SIZE)

        self._task = task
        self._base_dir = Path(base_dir)
        self._settings = settings
        self._poprako: PoprakoSettings = settings.poprako
        self._client_factory = client_factory or PoprakoClient
        self._client = self._client_factory(self._poprako.base_url, "")
        self._android = is_android_strict()

        self._files = [record.relative_path for record in task.files]
        self._issue_counts: dict[str, int] = {}
        for issue in task.issues:
            self._issue_counts[issue.file] = self._issue_counts.get(issue.file, 0) + 1

        # 运行时状态
        self._user: Optional[User] = None
        self._teams: list[Membership] = []
        self._worksets: list = []
        self._comics: list = []
        self._chapters: list[Chapter] = []
        self._chapter: Optional[Chapter] = None
        self._artworks: list[PageArtwork] = []
        self._remote_issues: list[RemoteIssue] = []
        self._mapping: dict[str, str] = {}
        self._payload: dict = {"pages": []}
        self._report: Optional[PreflightReport] = None
        self._mapping_combos: dict[str, QComboBox] = {}
        self._workers: set = set()
        self._busy: Optional[str] = None
        self._notice: Optional[tuple[str, str]] = None
        self._startup_done = False
        self._finalized = False
        self._mapping_dirty = False
        self._settings_dirty = False
        self._keyring_ok: Optional[bool] = None
        self._last_upload_at = ""
        self._last_page_count = 0
        self._last_issue_count = 0

        log.info("监制数据同步：开始构造对话框（文件 %d 个）", len(self._files))
        self._build_ui()
        self._load_binding()
        self._prefill()
        log.info("监制数据同步：对话框构造完成")

    # ================================================================ 构建界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(GUTTER)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("syncNav")
        self.nav_list.setFixedWidth(NAV_WIDTH)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setStyleSheet(
            f"""
            QListWidget#syncNav {{
                background: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_BORDER};
                border-radius: 6px;
                padding: {NAV_PADDING}px;
                outline: none;
                {HEADER_FONT_QSS}
            }}
            QListWidget#syncNav::item {{
                padding: {NAV_ITEM_VPAD}px 10px;
                margin: 0px;
                border-radius: 5px;
            }}
            QListWidget#syncNav::item:selected {{
                background: {COLOR_BG_SELECTED};
                color: white;
            }}
            QListWidget#syncNav::item:hover {{
                background: {COLOR_BG_HOVER};
            }}
            """
        )
        header_font = self.nav_list.font()
        header_font.setPixelSize(15)
        header_font.setBold(True)
        self.nav_list.setFont(header_font)
        self._nav_row_height = QFontMetrics(header_font).height() + 2 * NAV_ITEM_VPAD
        body.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        self._page_widgets: dict[str, _Page] = {}
        self._build_account_page()
        self._build_target_page()
        self._build_mapping_page()
        self._build_upload_page()
        # ---- 底部：状态行 + 关闭 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self._status_label = QLabel("")
        self._status_label.setObjectName("syncStatus")
        self._status_label.setWordWrap(False)
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._style_status(COLOR_TEXT_DIM)
        bottom.addWidget(self._status_label, 1)

        self._close_btn = QPushButton("关闭")
        self._close_btn.setAutoDefault(False)
        self._close_btn.clicked.connect(self.reject)
        bottom.addWidget(self._close_btn, 0)
        root.addLayout(bottom)

        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setCurrentRow(0)


    # -- 页 1：账号 --------------------------------------------------------

    def _build_account_page(self) -> None:
        page = self._add_page("account")
        rows = page.enable_scroll()

        self._base_url_edit = QLineEdit(self._poprako.base_url)
        self._base_url_edit.setToolTip("一般不用改；必须是 http(s) 地址")
        self._add_row(rows, "服务器地址", self._base_url_edit, expand=True)

        self._qid_edit = QLineEdit(self._poprako.qid)
        self._qid_edit.setPlaceholderText("登录用的 QQ 号")
        self._add_row(rows, "QQ 号", self._qid_edit, expand=True)

        self._password_edit = QLineEdit("")
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText("密码只用于本次登录，不会保存")
        self._password_edit.returnPressed.connect(self._on_login_clicked)
        self._add_row(rows, "密码", self._password_edit, expand=True)

        self._keep_switch = ToggleSwitch()
        self._keep_switch.setChecked(bool(self._poprako.keep_signed_in))
        self._keep_switch.toggled.connect(self._on_keep_signed_in_toggled)
        self._add_row(rows, "保持登录", self._keep_switch)

        rows.addSpacing(ROW_SPACING)
        self._account_status = QLabel("未登录")
        self._account_status.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._add_row(rows, "状态", self._account_status)

        self._token_hint = QLabel("—")
        self._token_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._add_row(rows, "令牌", self._token_hint)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._logout_btn = QPushButton("退出登录")
        self._logout_btn.setAutoDefault(False)
        self._logout_btn.clicked.connect(self._on_logout_clicked)
        actions.addWidget(self._logout_btn)
        self._login_btn = QPushButton("登录")
        self._login_btn.setAutoDefault(False)
        self._login_btn.clicked.connect(self._on_login_clicked)
        actions.addWidget(self._login_btn)
        rows.addLayout(actions)
        _align_fields(rows)

    # -- 页 2：目标章节 ----------------------------------------------------

    def _build_target_page(self) -> None:
        page = self._add_page("target")
        rows = page.enable_scroll()

        self._team_combo = self._new_combo()
        self._team_combo.currentIndexChanged.connect(self._on_team_changed)
        self._add_row(rows, "团队", self._team_combo, expand=True)

        self._workset_combo = self._new_combo()
        self._workset_combo.currentIndexChanged.connect(self._on_workset_changed)
        self._add_row(rows, "项目集", self._workset_combo, expand=True)

        self._comic_combo = self._new_combo()
        self._comic_combo.currentIndexChanged.connect(self._on_comic_changed)
        self._add_row(rows, "作品", self._comic_combo, expand=True)

        self._chapter_combo = self._new_combo()
        self._chapter_combo.currentIndexChanged.connect(self._on_chapter_changed)
        self._add_row(rows, "章节", self._chapter_combo, expand=True)

        rows.addSpacing(ROW_SPACING)
        self._stage_value = self._info_value()
        self._add_row(rows, "阶段", self._stage_value)
        self._pages_value = self._info_value()
        self._add_row(rows, "复合图页", self._pages_value)
        self._existing_value = self._info_value()
        self._add_row(rows, "现存问题", self._existing_value)
        self._role_value = self._info_value()
        self._add_row(rows, "我的角色", self._role_value)

        self._warn_label = QLabel("")
        self._warn_label.setWordWrap(True)
        self._warn_label.setStyleSheet(f"color: {COLOR_WARN};")
        self._warn_label.setVisible(False)
        rows.addWidget(self._warn_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setAutoDefault(False)
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        actions.addWidget(self._refresh_btn)
        rows.addLayout(actions)
        _align_fields(rows)

    # -- 页 3：页面映射 ----------------------------------------------------

    def _build_mapping_page(self) -> None:
        page = self._add_page("mapping")

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._auto_btn = QPushButton("自动匹配")
        self._auto_btn.setAutoDefault(False)
        self._auto_btn.clicked.connect(self._on_auto_match_clicked)
        toolbar.addWidget(self._auto_btn)

        self._order_btn = QPushButton("按顺序对齐")
        self._order_btn.setAutoDefault(False)
        self._order_btn.clicked.connect(self._on_align_clicked)
        toolbar.addWidget(self._order_btn)

        self._clear_btn = QPushButton("清空映射")
        self._clear_btn.setAutoDefault(False)
        self._clear_btn.clicked.connect(self._on_clear_mapping_clicked)
        toolbar.addWidget(self._clear_btn)
        toolbar.addStretch(1)
        page.body.addLayout(toolbar)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["本地 PSD", "问题", "对应复合图页"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 240)
        page.body.addWidget(self._table, 1)

        self._mapping_summary = QLabel("")
        self._mapping_summary.setWordWrap(True)
        self._mapping_summary.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        page.body.addWidget(self._mapping_summary)

        self._remote_summary = QLabel("")
        self._remote_summary.setWordWrap(True)
        self._remote_summary.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        page.body.addWidget(self._remote_summary)

    # -- 页 4：预览与上传 --------------------------------------------------

    def _build_upload_page(self) -> None:
        page = self._add_page("upload")

        grid = QVBoxLayout()
        grid.setSpacing(ROW_SPACING)
        self._upload_target_value = self._info_value()
        grid.addWidget(_Row("目标章节", self._upload_target_value))
        self._upload_write_value = self._info_value()
        grid.addWidget(_Row("写入", self._upload_write_value))
        self._upload_clear_value = self._info_value()
        grid.addWidget(_Row("清空", self._upload_clear_value))
        self._upload_keep_value = self._info_value()
        grid.addWidget(_Row("保留", self._upload_keep_value))
        page.body.addLayout(grid)

        self._preserve_switch = ToggleSwitch()
        self._preserve_switch.setChecked(bool(self._poprako.preserve_unmapped))
        self._preserve_switch.toggled.connect(self._on_preserve_toggled)
        page.body.addWidget(
            _Row("未映射的远端页保留现有问题", self._preserve_switch)
        )

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        page.body.addWidget(self._progress)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._preview.setFont(mono)
        page.body.addWidget(self._preview, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self._copy_btn = QPushButton("复制")
        self._copy_btn.setAutoDefault(False)
        self._copy_btn.clicked.connect(self._on_copy_clicked)
        actions.addWidget(self._copy_btn)

        self._export_btn = QPushButton("导出 JSON…")
        self._export_btn.setAutoDefault(False)
        self._export_btn.clicked.connect(self._on_export_clicked)
        actions.addWidget(self._export_btn)

        self._upload_btn = QPushButton("上传")
        self._upload_btn.setAutoDefault(False)
        self._upload_btn.clicked.connect(self._on_upload_clicked)
        actions.addWidget(self._upload_btn)
        page.body.addLayout(actions)

    # -- 小工具 ------------------------------------------------------------

    def _add_page(self, key: str) -> _Page:
        title, hint = PAGES[key]
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(QSize(0, self._nav_row_height))
        self.nav_list.addItem(item)

        page = _Page(title, hint, self._nav_row_height)
        self.stack.addWidget(page)
        self._page_widgets[key] = page
        return page

    @staticmethod
    def _new_combo() -> QComboBox:
        combo = NoWheelComboBox()
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(16)
        return combo

    @staticmethod
    def _info_value() -> QLabel:
        label = QLabel("—")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        return label

    def _add_row(self, rows: QVBoxLayout, text: str, control: QWidget,
                 expand: bool = False) -> _Row:
        row = _Row(text, control, expand)
        rows.addWidget(row)
        return row

    def _on_nav_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(max(0, index))
        if index == 2:
            self._refresh_mapping_summary()
        self._refresh_status()

    # ================================================================ 异步

    def _run(self, fn: Callable[[], object], on_success: Callable[[object], None]) -> None:
        """把一个无参可调用放到后台线程，成功后回主线程处理。

        测试里直接把本方法替换成同步执行即可，无需起线程。
        """
        worker = CallWorker(fn)
        self._workers.add(worker)
        worker.succeeded.connect(lambda result: self._on_call_done(worker, on_success, result))
        worker.failed.connect(lambda message: self._on_call_failed(worker, message))
        worker.start()

    def _on_call_done(self, worker, on_success, result) -> None:
        self._release_worker(worker)
        self._set_busy(None)
        on_success(result)

    def _on_call_failed(self, worker, message: str) -> None:
        self._release_worker(worker)
        self._set_busy(None)
        self._set_notice(message, "error")

    @staticmethod
    def _release_worker(worker) -> None:
        try:
            worker.deleteLater()
        except RuntimeError:   # pragma: no cover —— 已随父对象销毁
            pass

    def _set_busy(self, text: Optional[str]) -> None:
        self._busy = text
        if text:
            self._notice = None
        self._refresh_status()
        self._refresh_gates()

    def _set_notice(self, text: str, level: str = "info") -> None:
        self._notice = (text, level)
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._busy:
            text, color = self._busy, COLOR_TEXT_DIM
        elif self._notice:
            text, level = self._notice
            color = {
                "ok": COLOR_PASS,
                "warn": COLOR_WARN,
                "error": COLOR_FAIL,
            }.get(level, COLOR_TEXT_DIM)
        else:
            text, color = self._context_summary(), COLOR_TEXT_DIM
        self._status_label.setText(self._elide(text))
        self._style_status(color)

    def _style_status(self, color: str) -> None:
        self._status_label.setStyleSheet(f"color: {color};")

    @staticmethod
    def _elide(text: str, limit: int = STATUS_LIMIT) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _context_summary(self) -> str:
        parts: list[str] = []
        if self._user is not None:
            parts.append(f"已登录 {self._user.nickname or self._user.qid}")
        else:
            parts.append("未登录")
        if self._chapter is not None:
            parts.append(self._chapter.label)
        if self._artworks:
            parts.append(f"{len(self._mapping)}/{len(self._files)} 已映射")
        if self._report is not None and self._report.pages_written:
            parts.append(f"{self._report.issues_written} 条问题")
        if self._last_upload_at:
            parts.append(f"上次同步 {self._last_upload_at}")
        return " · ".join(parts)

    # ================================================================ 启动

    def _prefill(self) -> None:
        self._base_url_edit.setText(self._poprako.base_url)
        self._qid_edit.setText(self._poprako.qid)
        self._refresh_token_hint()
        self._upload_target_value.setText("—")
        self._upload_write_value.setText("—")
        self._upload_clear_value.setText("—")
        self._upload_keep_value.setText("—")
        self._preview.setPlainText("")
        self._preview_full = ""
        self._refresh_mapping_summary()
        self._refresh_status()
        self._refresh_gates()

    def showEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        """窗口**先出现**，再去做令牌校验/拉层级。

        校验要用系统凭据库（可能慢、甚至弹系统认证），网络也可能慢；如果放在
        构造或 showEvent 里同步跑，用户看到的是"点了没反应"。这里用
        ``singleShot(0)`` 把启动流程挪到事件循环下一拍。
        """
        super().showEvent(event)
        if not self._startup_done:
            self._startup_done = True
            QTimer.singleShot(0, self._startup)

    def _startup(self) -> None:
        log.info("监制数据同步：开始校验登录状态")
        token = load_token(self._poprako)
        if not token:
            self._apply_logged_out()
            return
        self._client.token = token
        self._set_busy("正在校验登录状态…")
        self._run(self._client.me, self._on_verified)

    def _on_verified(self, user: User) -> None:
        """用缓存令牌校验成功：进入已登录态并继续拉层级。"""
        if user.nickname != self._poprako.nickname or user.qid != self._poprako.qid:
            self._poprako.nickname = user.nickname
            self._poprako.qid = user.qid
            self._settings_dirty = True
        self._apply_logged_in(user)
        self._load_teams()

    # ================================================================ 鉴权

    def _apply_logged_out(self) -> None:
        self._user = None
        self._account_status.setText("未登录")
        self._account_status.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._logout_btn.setEnabled(False)
        self._login_btn.setEnabled(True)
        self._target_page_set_enabled(False)
        self._refresh_token_hint()
        self._refresh_status()
        self._refresh_gates()

    def _apply_logged_in(self, user: User) -> None:
        self._user = user
        text = f"已登录 · {user.nickname or user.qid}"
        if user.qid:
            text += f"（qid {user.qid}）"
        self._account_status.setText(text)
        self._account_status.setStyleSheet(f"color: {COLOR_TEXT};")
        self._logout_btn.setEnabled(True)
        self._login_btn.setEnabled(True)
        self._target_page_set_enabled(True)
        self._refresh_token_hint()
        self._refresh_status()
        self._refresh_gates()

    def _target_page_set_enabled(self, enabled: bool) -> None:
        page = self._page_widgets.get("target")
        if page is not None:
            page.setEnabled(enabled)

    def _refresh_token_hint(self) -> None:
        if self._user is None:
            self._token_hint.setText("—")
            self._token_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
            return
        plaintext = bool((self._poprako.token or "").strip())
        if self._keyring_ok is None:
            # 探测本身会写一次临时键：只做一次，别每次刷新都折腾系统凭据库
            self._keyring_ok = keyring_available()
        if plaintext:
            self._token_hint.setText("settings.json（明文，keyring 不可用）")
            self._token_hint.setStyleSheet(f"color: {COLOR_WARN};")
        elif self._keyring_ok:
            self._token_hint.setText("系统凭据库（keyring）")
            self._token_hint.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        else:   # pragma: no cover —— 探测失败时会写明文
            self._token_hint.setText("settings.json（明文）")
            self._token_hint.setStyleSheet(f"color: {COLOR_WARN};")

    def _on_keep_signed_in_toggled(self, checked: bool) -> None:
        self._poprako.keep_signed_in = bool(checked)
        self._settings_dirty = True

    def _on_login_clicked(self) -> None:
        base_url = self._base_url_edit.text().strip()
        qid = self._qid_edit.text().strip()
        password = self._password_edit.text()

        if not base_url.lower().startswith(("http://", "https://")):
            self._set_notice("服务器地址必须是 http(s) 地址", "warn")
            return
        if not qid or not password:
            self._set_notice("请填写 QQ 号与密码", "warn")
            return

        self._poprako.base_url = base_url
        if self._client.base_url != base_url.rstrip("/"):
            self._client.close()
            self._client = self._client_factory(base_url, "")
        self._client.token = ""
        self._set_busy("正在登录…")

        def call() -> tuple:
            session = self._client.login(qid, password)
            return session, self._client.me()

        self._run(call, lambda result: self._on_login_done(*result))

    def _on_login_done(self, session, user: User) -> None:
        location = save_token(self._poprako, session.token)
        self._poprako.user_id = session.user_id
        self._poprako.qid = user.qid or self._poprako.qid
        self._poprako.nickname = user.nickname
        self._password_edit.clear()
        self._settings_dirty = True
        self._apply_logged_in(user)
        if location == "settings.json":
            self._set_notice("登录成功；系统凭据库不可用，令牌已存为明文", "warn")
        else:
            self._set_notice("登录成功", "ok")
        self._load_teams()

    def _on_logout_clicked(self) -> None:
        clear_token(self._poprako)
        self._poprako.user_id = ""
        self._poprako.qid = ""
        self._poprako.nickname = ""
        self._client.token = ""
        self._settings_dirty = True
        self._teams = []
        self._worksets = []
        self._comics = []
        self._chapters = []
        self._chapter = None
        self._artworks = []
        self._remote_issues = []
        self._mapping = {}
        self._clear_combos(self._workset_combo, self._comic_combo, self._chapter_combo)
        self._clear_combos(self._team_combo)
        self._refresh_chapter_info()
        self._refresh_mapping_table()
        self._refresh_payload()
        self._apply_logged_out()
        self._set_notice("已退出登录", "info")

    # ================================================================ 层级

    def _load_teams(self) -> None:
        self._set_busy("正在读取团队…")
        self._run(self._client.my_teams, self._on_teams)

    def _on_teams(self, teams: list) -> None:
        self._teams = list(teams)
        self._fill_combo(
            self._team_combo,
            [(item.display_name, item.team_id) for item in self._teams],
            self._poprako.last_team_id,
        )
        if not self._teams:
            self._set_notice("该账号还没有加入任何团队", "warn")
            self._clear_combos(self._workset_combo, self._comic_combo, self._chapter_combo)
            return
        self._on_team_changed()

    def _on_team_changed(self) -> None:
        membership = self._current_membership()
        if membership is None:
            self._clear_combos(self._workset_combo, self._comic_combo, self._chapter_combo)
            self._refresh_role_value()
            return
        if self._poprako.last_team_id != membership.team_id:
            self._poprako.last_team_id = membership.team_id
            self._settings_dirty = True
        self._refresh_role_value()
        self._set_busy("正在读取项目集…")
        self._run(
            lambda: self._client.worksets(membership.team_id),
            self._on_worksets,
        )

    def _on_worksets(self, worksets: list) -> None:
        self._worksets = list(worksets)
        self._fill_combo(
            self._workset_combo,
            [(item.name, item.id) for item in self._worksets],
            self._poprako.last_workset_id,
        )
        self._on_workset_changed()

    def _on_workset_changed(self) -> None:
        workset_id = self._workset_combo.currentData()
        if not workset_id:
            self._clear_combos(self._comic_combo, self._chapter_combo)
            return
        if self._poprako.last_workset_id != workset_id:
            self._poprako.last_workset_id = workset_id
            self._settings_dirty = True
        self._set_busy("正在读取作品…")
        self._run(lambda: self._client.comics(workset_id), self._on_comics)

    def _on_comics(self, comics: list) -> None:
        self._comics = list(comics)
        self._fill_combo(
            self._comic_combo,
            [(item.title, item.id) for item in self._comics],
            self._poprako.last_comic_id,
        )
        self._on_comic_changed()

    def _on_comic_changed(self) -> None:
        comic_id = self._comic_combo.currentData()
        if not comic_id:
            self._clear_combos(self._chapter_combo)
            return
        if self._poprako.last_comic_id != comic_id:
            self._poprako.last_comic_id = comic_id
            self._settings_dirty = True
        self._set_busy("正在读取章节…")
        self._run(lambda: self._client.chapters(comic_id), self._on_chapters)

    def _on_chapters(self, chapters: list) -> None:
        self._chapters = list(chapters)
        pending = self._pending_chapter_id()
        self._fill_combo(
            self._chapter_combo,
            [(item.label, item.id) for item in self._chapters],
            pending,
        )
        self._on_chapter_changed()

    def _pending_chapter_id(self) -> str:
        """优先恢复任务里绑定的章节。"""
        binding = self._task.integrations.get("poprako") or {}
        chapter_id = binding.get("chapter_id")
        return chapter_id if isinstance(chapter_id, str) else ""

    def _on_chapter_changed(self) -> None:
        chapter_id = self._chapter_combo.currentData()
        if not chapter_id:
            self._chapter = None
            self._artworks = []
            self._remote_issues = []
            self._refresh_chapter_info()
            self._refresh_mapping_table()
            self._refresh_payload()
            return

        if self._chapter is not None and chapter_id != self._chapter.id and self._mapping:
            if not self._confirm_switch_chapter():
                self._select_chapter(self._chapter.id)
                return

        self._set_busy("正在读取章节信息…")
        self._run(
            lambda: (
                self._client.chapter(chapter_id),
                self._client.page_artworks(chapter_id),
                self._client.issues(chapter_id),
            ),
            self._on_chapter_data,
        )

    def _on_chapter_data(self, data: tuple) -> None:
        chapter, pages, remote_issues = data
        self._chapter = chapter
        self._artworks = list(pages)
        self._remote_issues = list(remote_issues)
        self._refresh_chapter_info()
        self._ensure_mapping()
        self._refresh_mapping_table()
        self._refresh_payload()

    def _on_refresh_clicked(self) -> None:
        chapter_id = self._chapter_combo.currentData()
        if chapter_id:
            self._on_chapter_changed()
        elif self._user is not None:
            self._load_teams()

    # -- 下拉框小工具 -------------------------------------------------------

    @staticmethod
    def _fill_combo(combo: QComboBox, items: list[tuple[str, str]], preferred: str = "") -> None:
        combo.blockSignals(True)
        combo.clear()
        for text, value in items:
            combo.addItem(text, value)
        if preferred:
            index = combo.findData(preferred)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    @staticmethod
    def _clear_combos(*combos: QComboBox) -> None:
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)

    def _select_chapter(self, chapter_id: str) -> None:
        index = self._chapter_combo.findData(chapter_id)
        if index < 0:
            return
        self._chapter_combo.blockSignals(True)
        self._chapter_combo.setCurrentIndex(index)
        self._chapter_combo.blockSignals(False)

    def _current_membership(self) -> Optional[Membership]:
        team_id = self._team_combo.currentData()
        for membership in self._teams:
            if membership.team_id == team_id:
                return membership
        return None

    def _confirm_switch_chapter(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("切换章节")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("切换章节会清空当前的页面映射")
        box.setInformativeText("已手工调整过的映射不会保留。要继续吗？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("继续")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        return box.exec() == QMessageBox.StandardButton.Yes

    # ================================================================ 章节信息

    def _refresh_chapter_info(self) -> None:
        chapter = self._chapter
        if chapter is None:
            self._stage_value.setText("—")
            self._pages_value.setText("—")
            self._existing_value.setText("—")
            self._role_value.setText(roles_text(self._current_membership().roles)
                                    if self._current_membership() else "—")
            self._warn_label.setVisible(False)
            self._refresh_gates()
            return

        active = [
            f"{name}{PHASE_LABELS[phase]}"
            for name, phase in stage_phases(chapter.stages)
            if phase != "pending"
        ]
        self._stage_value.setText(" · ".join(active) if active else "未开始")
        self._pages_value.setText(f"{len(self._artworks)} 页")
        self._existing_value.setText(
            f"{len(self._remote_issues)} 条（上传会整体替换）"
        )
        self._refresh_role_value()

        warning = ""
        membership = self._current_membership()
        if chapter.is_published:
            warning = f"⚠ 该章节已发布，{PLATFORM_NAME} 不接受问题导入"
        elif membership is not None and not membership.is_reviewer:
            warning = "⚠ 你在该团队没有「监制」指派，上传会被拒绝（管理员身份不够）"
        elif not self._artworks:
            warning = "⚠ 该章节还没有复合图页，请先在平台上传 PSD 复合图"
        self._warn_label.setText(warning)
        self._warn_label.setVisible(bool(warning))
        self._refresh_gates()

    def _refresh_role_value(self) -> None:
        membership = self._current_membership()
        if membership is None:
            self._role_value.setText("—")
            return
        suffix = "（可导入）" if membership.is_reviewer else "（不可导入）"
        self._role_value.setText(f"{roles_text(membership.roles)}{suffix}")

    # ================================================================ 映射

    def _ensure_mapping(self) -> None:
        """首次进入某章时恢复/自动匹配映射。"""
        binding = self._task.integrations.get("poprako") or {}
        same_chapter = binding.get("chapter_id") == self._chapter.id if self._chapter else False
        saved = binding.get("pages") if same_chapter else None
        valid_ids = {page.id for page in self._artworks}

        if isinstance(saved, dict) and saved:
            restored = {
                str(name): str(pid)
                for name, pid in saved.items()
                if isinstance(pid, str) and pid in valid_ids and name in self._files
            }
            if len(restored) >= len(saved) * 0.5:
                self._mapping = restored
                return

        result = auto_match(self._files, self._artworks)
        self._mapping = dict(result.mapping)
        self._mapping_dirty = True
        if result.ambiguous:
            self._set_notice(
                f"自动匹配完成，但有 {len(result.ambiguous)} 个文件存在同名候选，请手动选择",
                "warn",
            )
        elif result.unmatched_local:
            self._set_notice(
                f"自动匹配完成，{len(result.mapping)}/{len(self._files)} 页已映射", "info"
            )

    def _refresh_mapping_table(self) -> None:
        table = self._table
        table.setRowCount(0)
        self._mapping_combos.clear()
        if not self._artworks:
            self._refresh_mapping_summary()
            return

        table.setRowCount(len(self._files))
        for row, name in enumerate(self._files):
            file_item = QTableWidgetItem(name)
            file_item.setToolTip(self._file_tooltip(name))
            table.setItem(row, 0, file_item)

            count = self._issue_counts.get(name, 0)
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 1, count_item)

            combo = self._new_combo()
            combo.addItem("（不同步）", None)
            for page in self._artworks:
                combo.addItem(page.display_name, page.id)
            current = self._mapping.get(name)
            index = combo.findData(current) if current else 0
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda _idx, f=name, c=combo: self._on_mapping_changed(f, c)
            )
            table.setCellWidget(row, 2, combo)
            self._mapping_combos[name] = combo

        self._refresh_mapping_summary()

    def _file_tooltip(self, name: str) -> str:
        path = self._base_dir / name
        return f"{name}\n{path}"

    def _on_mapping_changed(self, file: str, combo: QComboBox) -> None:
        page_id = combo.currentData()
        if page_id:
            for other, other_page in list(self._mapping.items()):
                if other != file and other_page == page_id:
                    del self._mapping[other]
                    other_combo = self._mapping_combos.get(other)
                    if other_combo is not None:
                        other_combo.blockSignals(True)
                        other_combo.setCurrentIndex(0)
                        other_combo.blockSignals(False)
                    self._set_notice(f"{other} 的映射已解除（与 {file} 选了同一页）", "warn")
            self._mapping[file] = page_id
        else:
            self._mapping.pop(file, None)
        self._mapping_dirty = True
        self._refresh_mapping_summary()
        self._refresh_payload()

    def _refresh_mapping_summary(self) -> None:
        if not self._artworks:
            self._mapping_summary.setText("请先在「目标章节」选择章节")
            self._mapping_summary.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
            self._remote_summary.setText("")
            return

        matched = sum(1 for name in self._files if name in self._mapping)
        missing = [name for name in self._files if name not in self._mapping]
        missing_with_issues = [(name, self._issue_counts.get(name, 0))
                               for name in missing if self._issue_counts.get(name, 0)]
        text = f"已映射 {matched}/{len(self._files)} 页"
        if missing_with_issues:
            detail = "、".join(f"{name}（{count} 条）" for name, count in missing_with_issues[:3])
            text += f" · 未映射且含问题 {len(missing_with_issues)} 个：{detail}"
            self._mapping_summary.setStyleSheet(f"color: {COLOR_WARN};")
        elif missing:
            text += f" · 未映射 {len(missing)} 个（均无问题）"
            self._mapping_summary.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        else:
            self._mapping_summary.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._mapping_summary.setText(text)

        mapped_ids = set(self._mapping.values())
        remote_unmapped = [page for page in self._artworks if page.id not in mapped_ids]
        with_issues = [
            page for page in remote_unmapped
            if any(issue.page_artwork_id == page.id for issue in self._remote_issues)
        ]
        if not remote_unmapped:
            self._remote_summary.setText("远端所有复合图页都已映射")
        elif with_issues:
            names = "、".join(page.display_name for page in with_issues[:3])
            self._remote_summary.setText(
                f"远端未映射 {len(remote_unmapped)} 页，其中 {len(with_issues)} 页有旧问题：{names}"
            )
        else:
            self._remote_summary.setText(
                f"远端未映射 {len(remote_unmapped)} 页（均无旧问题）"
            )

    def _on_auto_match_clicked(self) -> None:
        if not self._artworks:
            return
        result = auto_match(self._files, self._artworks)
        self._mapping = dict(result.mapping)
        self._mapping_dirty = True
        self._refresh_mapping_table()
        self._refresh_payload()
        if result.ambiguous:
            self._set_notice(
                f"自动匹配：{len(result.mapping)} 页命中，{len(result.ambiguous)} 个同名候选待手动选择",
                "warn",
            )
        else:
            self._set_notice(f"自动匹配：{len(result.mapping)}/{len(self._files)} 页命中", "ok")

    def _on_align_clicked(self) -> None:
        if not self._artworks:
            return
        self._mapping = align_in_order(self._files, self._artworks)
        self._mapping_dirty = True
        self._refresh_mapping_table()
        self._refresh_payload()
        self._set_notice(f"已按顺序对齐：{len(self._mapping)} 页", "info")

    def _on_clear_mapping_clicked(self) -> None:
        self._mapping = {}
        self._mapping_dirty = True
        self._refresh_mapping_table()
        self._refresh_payload()
        self._set_notice("已清空映射", "info")

    # ================================================================ 预览

    def _on_preserve_toggled(self, checked: bool) -> None:
        self._poprako.preserve_unmapped = bool(checked)
        self._settings_dirty = True
        self._refresh_payload()

    def _refresh_payload(self) -> None:
        if self._chapter is None or not self._artworks:
            self._payload = {"pages": []}
            self._report = None
            self._preview_full = ""
            self._preview.setPlainText("")
            self._upload_target_value.setText(self._chapter.label if self._chapter else "—")
            self._upload_write_value.setText("—")
            self._upload_clear_value.setText("—")
            self._upload_keep_value.setText("—")
            self._refresh_gates()
            return

        payload, report = build_payload(
            files=self._files,
            issues=self._task.issues,
            base_dir=self._base_dir,
            mapping=self._mapping,
            pages=self._artworks,
            remote_issues=self._remote_issues,
            preserve_unmapped=bool(self._poprako.preserve_unmapped),
        )
        self._payload = payload
        self._report = report

        self._upload_target_value.setText(self._chapter.label)
        self._upload_write_value.setText(
            f"{report.pages_written} 页 · {report.issues_written} 条问题"
        )
        if report.pages_cleared:
            names = "、".join(name for name, _count in report.pages_cleared[:5])
            self._upload_clear_value.setText(
                f"{len(report.pages_cleared)} 页（{names}）"
            )
            self._upload_clear_value.setStyleSheet(f"color: {COLOR_WARN};")
        else:
            self._upload_clear_value.setText("0 页")
            self._upload_clear_value.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
        self._upload_keep_value.setText(
            f"{report.pages_preserved} 页 · {report.issues_preserved} 条旧问题"
            if report.pages_preserved
            else "0 页"
        )

        self._preview_full = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(self._preview_full) > PREVIEW_LIMIT:
            self._preview.setPlainText(
                self._preview_full[:PREVIEW_LIMIT] + "\n…（预览已截断，导出/复制为完整内容）"
            )
        else:
            self._preview.setPlainText(self._preview_full)
        self._refresh_gates()

    def _on_copy_clicked(self) -> None:
        if not self._preview_full:
            return
        QApplication.clipboard().setText(self._preview_full)
        self._set_notice("已复制完整 JSON 到剪贴板", "ok")

    def _on_export_clicked(self) -> None:
        if not self._preview_full:
            return
        default = self._base_dir / f"{self._export_stem()}.poprako-issues.json"
        options = QFileDialog.Option.DontUseNativeDialog if self._android else QFileDialog.Option(0)
        path, _selected = QFileDialog.getSaveFileName(
            self, "导出 Poprako 导入 JSON", str(default), "JSON (*.json)", options=options
        )
        if not path:
            return
        try:
            Path(path).write_text(self._preview_full, encoding="utf-8")
        except OSError as exc:
            self._set_notice(f"导出失败：{exc}", "error")
            return
        self._set_notice(f"已导出：{Path(path).name}", "ok")

    def _export_stem(self) -> str:
        name = self._task.task_name or "mangaproof"
        return name.replace("/", "_").replace("\\", "_")

    # ================================================================ 门禁

    def _gate_reasons(self) -> list[str]:
        """上传按钮禁用的原因（空列表 = 可上传）。"""
        if self._busy:
            return [self._busy]
        if self._user is None:
            return ["请先在「账号」页登录"]
        if self._chapter is None:
            return ["请先在「目标章节」选择章节"]
        if self._chapter.is_published:
            return ["该章节已发布，Poprako 不接受问题导入"]
        membership = self._current_membership()
        if membership is not None and not membership.is_reviewer:
            return ["你在该团队没有「监制」指派，上传会被拒绝"]
        if not self._artworks:
            return ["该章节还没有复合图页"]
        if self._report is None:
            return ["转换结果还没有生成"]
        return list(self._report.blocking)

    def _refresh_gates(self) -> None:
        reasons = self._gate_reasons()
        can_upload = not reasons
        self._upload_btn.setEnabled(can_upload)
        self._upload_btn.setToolTip(
            "把本次结果写入该章节" if can_upload else "\n".join(reasons)
        )
        has_chapter = self._chapter is not None and bool(self._artworks)
        self._auto_btn.setEnabled(has_chapter)
        self._order_btn.setEnabled(has_chapter)
        self._clear_btn.setEnabled(has_chapter and bool(self._mapping))
        self._copy_btn.setEnabled(bool(self._preview_full))
        self._export_btn.setEnabled(bool(self._preview_full))
        self._refresh_btn.setEnabled(self._user is not None and not self._busy)
        self._login_btn.setEnabled(not self._busy)

    # ================================================================ 上传

    def _confirm_upload(self) -> bool:
        report = self._report
        if report is None:
            return False
        lines = [f"写入：{report.pages_written} 页 · {report.issues_written} 条问题"]
        if report.pages_cleared:
            names = "、".join(name for name, _count in report.pages_cleared[:5])
            lines.append(f"清空：{len(report.pages_cleared)} 页（{names}）")
        if report.pages_preserved:
            lines.append(
                f"保留：{report.pages_preserved} 页现有问题（{report.issues_preserved} 条）"
            )
        lines.append("该操作会整体替换这一章的全部问题，且不可撤销。")

        box = QMessageBox(self)
        box.setWindowTitle("确认上传")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"将把本次结果写入「{self._chapter.label}」")
        box.setInformativeText("\n".join(lines))
        checkbox = QCheckBox("我确认整体替换该章问题")
        box.setCheckBox(checkbox)
        upload_button = box.addButton("上传", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not upload_button:
            return False
        if not checkbox.isChecked():
            self._set_notice("请先勾选「我确认整体替换该章问题」再上传", "warn")
            return False
        return True

    def _on_upload_clicked(self) -> None:
        if self._gate_reasons() or not self._confirm_upload():
            return
        self._notice = None
        self._set_busy("正在上传…")
        self._progress.setRange(0, 0)
        worker = UploadWorker(self._client, self._chapter.id, dict(self._payload))
        self._workers.add(worker)
        worker.succeeded.connect(
            lambda outcome: self._on_upload_done(worker, outcome)
        )
        worker.failed.connect(lambda message: self._on_upload_failed(worker, message))
        worker.start()

    def _on_upload_done(self, worker, outcome: UploadOutcome) -> None:
        self._workers.discard(worker)
        self._release_worker(worker)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._set_busy(None)

        result = outcome.result
        self._last_upload_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_page_count = result.imported_page_count
        self._last_issue_count = result.imported_issue_count
        self._mapping_dirty = True

        if outcome.counts_match:
            self._set_notice(
                f"已同步：{result.imported_page_count} 页 {result.imported_issue_count} 条",
                "ok",
            )
        else:
            self._set_notice(
                "上传已返回成功，但回读校验与提交的条数不一致，请在平台核对",
                "warn",
            )
        self._refresh_status()

    def _on_upload_failed(self, worker, message: str) -> None:
        self._workers.discard(worker)
        self._release_worker(worker)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._set_busy(None)
        self._set_notice(message, "error")

    # ================================================================ 绑定

    def _load_binding(self) -> None:
        raw = self._task.integrations.get("poprako") or {}
        if not isinstance(raw, dict):
            raw = {}
        pages = raw.get("pages")
        if isinstance(pages, dict):
            self._mapping = {
                str(name): str(pid)
                for name, pid in pages.items()
                if isinstance(pid, str)
            }
        self._last_upload_at = str(raw.get("last_upload_at") or "")
        self._last_page_count = int(raw.get("last_page_count") or 0)
        self._last_issue_count = int(raw.get("last_issue_count") or 0)

    def _binding_dict(self) -> dict:
        membership = self._current_membership()
        return {
            "team_id": membership.team_id if membership else "",
            "workset_id": self._workset_combo.currentData() or "",
            "comic_id": self._comic_combo.currentData() or "",
            "chapter_id": self._chapter.id if self._chapter else "",
            "chapter_label": self._chapter.label if self._chapter else "",
            "pages": dict(self._mapping),
            "last_upload_at": self._last_upload_at,
            "last_page_count": self._last_page_count,
            "last_issue_count": self._last_issue_count,
        }

    def _finalize(self) -> None:
        """关闭窗口：托管还在跑的线程，回写绑定与设置。"""
        if self._finalized:
            return
        self._finalized = True

        for worker in list(self._workers):
            if worker.isRunning():
                adopt_worker(worker)
            else:
                self._release_worker(worker)
        self._workers.clear()

        if self._chapter is not None or self._mapping_dirty:
            self._task.integrations["poprako"] = self._binding_dict()
            self.binding_changed.emit()
        if self._settings_dirty:
            self.settings_committed.emit()

    def done(self, result: int) -> None:   # noqa: D102（Qt 命名）
        self._finalize()
        super().done(result)

    def closeEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        self._finalize()
        super().closeEvent(event)
