# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""主窗口：打开流程、双重导航、快捷键、自动对比、问题、统计、自动保存、返修单。

对应需求：§5～§9、§11～§16、§21～§27、§30～§46、§59、§61、§66。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mangaproof import APP_NAME, __copyright__, __license__, __version__
from mangaproof.camera.centering import auto_box_rect, layer_visual_bounds
from mangaproof.compare.controller import BG_ONLY, ORIGINAL, CompareController, hz_to_interval_ms
from mangaproof.config.recent import RecentManager
from mangaproof.config.settings import (
    DISPLAY_RATIOS,
    Settings,
    SettingsManager,
    android_ui_scaling,
    effective_memory_policy,
    effective_ui_scale,
    first_run_banner,
    normalize_key,
    preload_window_offsets,
    shortcut_conflicts,
)
from mangaproof.psd.document import PSDDocument
from mangaproof.psd.image_cache import LayerImageCache
from mangaproof.psd.loader import (
    NoCompositeError,
    PSDReadError,
)
from mangaproof.report.generator import (
    default_report_name,
    resolve_report_path,
)
from mangaproof.review import navigator, persistence
from mangaproof.review.issue import Issue
from mangaproof.review.numbering import apply_numbering
from mangaproof.review.persistence import (
    backup_progress_file,
    progress_path_for_folder,
    progress_path_for_single,
)
from mangaproof.review.state import (
    FAILED,
    PASSED,
    UNREVIEWED,
    TaskState,
)
from mangaproof.storage import picker
from mangaproof.ui.dialogs import IssueDialog, ReportDialog
from mangaproof.ui.file_panel import FilePanel
from mangaproof.ui.issue_panel import IssuePanel
from mangaproof.ui.layer_panel import LayerPanel
from mangaproof.ui.nav_pad import NavPad
from mangaproof.ui.license_dialog import AppLicenseDialog, LicenseDialog
from mangaproof.ui.numbering_worker import (
    KIND_CANCELLED as NUMBERING_CANCELLED,
    NumberingWorker,
)
from mangaproof.ui.preloader import (
    KIND_EXTRA,
    KIND_OPEN,
    KIND_PRELOAD,
    WARM_ALL,
    PreloadWorker,
)
from mangaproof.ui.report_worker import (
    KIND_CANCELLED as REPORT_CANCELLED,
    ReportWorker,
)
from mangaproof.ui.settings_dialog import SettingsDialog
from mangaproof.ui.statistics_panel import StatisticsPanel
from mangaproof.ui.update_dialog import UpdateDialog
from mangaproof.ui.task_loader import (
    KIND_CANCELLED,
    KIND_MISMATCH,
    KIND_NO_FILES,
    TaskLoadWorker,
)
from mangaproof.ui.theme import COLOR_ACCENT, COLOR_BG_WIDGET, COLOR_BORDER, COLOR_TEXT
from mangaproof.ui.viewer_widget import SOURCE_BG, SOURCE_MERGED, ViewerWidget
from mangaproof.utils.platform import is_android_strict
from mangaproof.ui.widgets import NoWheelComboBox

log = logging.getLogger("mangaproof.ui.main_window")

AUTOSAVE_DEBOUNCE_MS = 1500

_REBIND_WARNING = (
    "⚠ 重要提醒\n\n"
    "MangaProof 将尝试把当前选择的文件/文件夹重新绑定到已有监制记录。\n"
    "如果选择了错误的 PSD 或错误的文件夹，已有的通过/未通过状态、"
    "红框与批注可能对应到错误内容。\n"
    "程序会先进行文件身份验证，验证失败时不会自动恢复任务。"
)

_TEXT_INPUT_TYPES = ("QLineEdit", "QTextEdit", "QPlainTextEdit", "QComboBox")

# 首次使用引导（启动时还没有 settings.json）：
# 只把「设置页面」摆到用户面前让他自己过一遍，不预设、不推荐、不代替决策——
# 默认值已经在 config/settings.py 里定好，用户不改也照常工作。
FIRST_RUN_INTRO = (
    "首次使用：这里是全部设置项，按自己的习惯调整即可。\n"
    "每一项都有说明文字；全部保持默认也可以，随时可在 设置 →「设置…」里再改。"
)
# 提醒条文案按平台给（config/settings.py 的 first_run_banner）：Android 上
# 内存策略锁定为激进、不可调，文案里不提它，免得指向一个改不了的设置项。


# 内存回收策略三档预算（文档结构卸载三档一致，见 _schedule_preloads）：
# - bg_qimage_bytes：预生成 bg QImage 池字节上限（merged 不受限，窗口有界）
# - lru_bytes：共享图层像素 LRU 预算
_MEMORY_POLICIES = {
    "aggressive": {"bg_qimage_bytes": 68 * 1024 * 1024, "lru_bytes": 256 * 1024 * 1024},
    "balanced": {"bg_qimage_bytes": 512 * 1024 * 1024, "lru_bytes": 512 * 1024 * 1024},
    "relaxed": {"bg_qimage_bytes": 768 * 1024 * 1024, "lru_bytes": 768 * 1024 * 1024},
}

# 推迟驱逐的重试节奏：io 锁被预加载线程占用时，按此间隔重试，
# 上限次数避免预加载长期占锁时无限重试（上限内没等到就交给下次调度）。
_EVICT_RETRY_MS = 200
_EVICT_RETRY_MAX = 25


class MainWindow(QMainWindow):
    def __init__(self, settings_manager: SettingsManager):
        super().__init__()
        self.settings_manager = settings_manager
        self.settings: Settings = settings_manager.settings
        # 最近打开记录：独立 recent.json（与 settings.json 同目录，见 config/recent.py）
        self.recent_manager = RecentManager(
            settings_manager.recent_path,
            legacy_paths=settings_manager.legacy_recent_paths,
        )

        self.task: Optional[TaskState] = None
        self._base_dir: Optional[Path] = None

        self._docs: Dict[str, PSDDocument] = {}
        self._layer_cache = LayerImageCache()
        self._layer_ids_by_file: Dict[str, List[str]] = {}
        self._layer_names_by_file: Dict[str, List[str]] = {}

        self._current_file = ""
        self._current_index = -1
        self._warned_no_composite: set = set()

        # 后台加载状态
        self._loader = None
        self._load_dialog = None
        self._load_mode = ""
        self._load_path: Optional[Path] = None

        # 后台生成返修单状态（进度框 + 防 GUI 卡死）
        self._report_worker = None
        self._report_dialog = None

        # 后台问题编号检查状态（进度框 + 防 GUI 卡死）
        self._numbering_worker = None
        self._numbering_dialog = None

        # 「全部监制完成」是否已提示/已自动生成返修单（同一次完成只做一次；
        # 之后再改动内容会复位，下次完成/Enter 时重新生成）
        self._completion_announced = False

        # 最近一次保存失败的原因（关闭任务时据此中止关闭，避免丢进度）
        self._save_error = ""

        # PSD 预加载线程（切换大文件不卡顿）
        self._preload = PreloadWorker(lambda rel: self._docs.get(rel), self)
        self._preload.task_done.connect(self._on_preload_done)
        self._preload.start()
        self._pending_open = None  # (rel, "file") 或 (rel, "layer", index)
        self._open_restore = False
        self._open_dialog = None      # 切换文件的忙碌进度框
        self._preload_targets: set = set()   # 阶段 A 未完成（merged）
        self._extra_targets: set = set()     # 阶段 B 未完成（背景图/图层）
        self._pending_qimages: Dict[str, Dict[str, object]] = {}  # 后台预热显示图
        self._preload_scheduled = False      # 是否已开始调度（未调度时状态栏留空）
        # 内存策略状态（_apply_memory_policy 热应用）
        self._bg_qimage_quota = _MEMORY_POLICIES["balanced"]["bg_qimage_bytes"]
        self._pinned_bg_key = None           # 当前文档钉住的 LRU 键 (path, layer_id)
        self._keep_set: set = set()          # 最近一次预加载调度的窗口集合
        self._evict_pending: set = set()     # 因 io 锁被占而推迟驱逐的文档
        self._evict_retries = 0              # 推迟驱逐的连续重试计数
        self._evict_timer = QTimer(self)     # 推迟驱逐的重试定时器
        self._evict_timer.setSingleShot(True)
        self._evict_timer.setInterval(_EVICT_RETRY_MS)
        self._evict_timer.timeout.connect(self._drain_pending_evictions)

        self._compare = CompareController(self)
        self._compare.display_changed.connect(self._on_compare_display_changed)
        self._compare.running_changed.connect(self._on_compare_running_changed)
        self._compare.set_interval_ms(hz_to_interval_ms(self.settings.compare_speed_hz))
        self._applying_compare_ui = False   # 程序内部改按钮状态时抑制 toggled 处理

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self.save_task)

        self._shortcuts: List[QShortcut] = []
        self._updating_panels = False
        # 首次使用提醒条是否被本次会话手动关闭（不落盘：下次启动仍会提示）
        self._settings_banner_dismissed = False
        # Android 专有浮动方向键：仅 Android 上创建，桌面恒为 None
        self.nav_pad: Optional[NavPad] = None

        self._build_ui()
        self._build_menus()
        self._rebuild_shortcuts()
        self._update_save_label(initial=True)
        self._refresh_enabled_state()
        self._apply_memory_policy()   # 按设置档位初始化 LRU 预算与 bg QImage 配额
        self._refresh_settings_banner()   # 首次使用（无配置文件）才显示提醒条
        self._setup_nav_pad()         # Android 专有：画布右下角的方向键

    # ================================================================= UI

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1440, 900)

        # ---- 中央 Viewer ----
        self.viewer = ViewerWidget()
        self.viewer.set_wheel_mode(self.settings.wheel_mode)
        self.viewer.rect_drawn.connect(self._on_rect_drawn)
        self.viewer.issue_drawn.connect(self._on_issue_drawn)
        self.viewer.camera_changed.connect(self._on_camera_changed)
        self.viewer.pending_changed.connect(self._on_pending_changed)

        # 首次使用提醒条（仅在没有配置文件时出现）+ 画布一起放进中央控件
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_settings_banner())
        central_layout.addWidget(self.viewer, 1)
        self.setCentralWidget(central)

        # ---- 左侧：文件列表 + 统计 ----
        self.file_panel = FilePanel()
        self.file_panel.file_activated.connect(self._on_file_activated)
        self.stats_panel = StatisticsPanel()
        self.stats_panel.layer_chip_clicked.connect(self._on_chip_clicked)
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.file_panel, 2)
        left_layout.addWidget(self.stats_panel, 3)
        self.left_dock = QDockWidget("任务", self)
        self.left_dock.setWidget(left_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        # ---- 右侧：图层列表 + 问题 ----
        self.layer_panel = LayerPanel()
        self.layer_panel.layer_activated.connect(self._on_layer_activated)
        self.issue_panel = IssuePanel()
        self.issue_panel.status_change_requested.connect(self._on_status_change_requested)
        self.issue_panel.add_issue_requested.connect(self._on_add_issue_requested)
        self.issue_panel.continuous_toggled.connect(self._on_continuous_toggled)
        self.issue_panel.auto_box_requested.connect(self.auto_box_current_layer)
        self.issue_panel.custom_comment_requested.connect(self._on_custom_comment)
        self.issue_panel.edit_issue_requested.connect(self._on_edit_issue)
        self.issue_panel.delete_issue_requested.connect(self._on_delete_issue)
        self.issue_panel.set_continuous(self.settings.continuous_annotation)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        # 拉伸比与左侧 file_panel:stats_panel（2:3）一致：
        # 图层列表竖向占用与左侧 PSD 文件选择对齐
        right_layout.addWidget(self.layer_panel, 2)
        right_layout.addWidget(self.issue_panel, 3)
        self.right_dock = QDockWidget("图层与问题", self)
        self.right_dock.setWidget(right_container)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.right_dock)

        # ---- 工具栏（按钮均显示快捷键，需求 §29、§30） ----
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_open_psd = self._add_tool_action(
            toolbar, "打开 PSD", "Ctrl+O", self.open_psd_dialog
        )
        self.action_open_folder = self._add_tool_action(
            toolbar, "打开文件夹", "Ctrl+Shift+O", self.open_folder_dialog
        )
        self.action_close = self._add_tool_action(
            toolbar, "关闭任务", "Ctrl+W", self.close_task
        )
        self.action_close.setToolTip(
            "关闭当前任务（文件夹 / 单个 PSD），回到未打开状态\n"
            "进度已自动保存，随时可从「文件 → 最近打开」回来继续"
        )
        toolbar.addSeparator()
        self.action_save = self._add_tool_action(toolbar, "保存", "Ctrl+S", self.save_task)
        self.action_renumber = self._add_tool_action(
            toolbar, "检查问题编号", "", self.check_issue_numbers
        )
        self.action_renumber.setToolTip(
            "按 PSD / 图层顺序检查并重排问题编号\n"
            "（修正删除问题、回头补问题造成的空号/跳号；显式触发，不影响标记性能）"
        )
        self.action_report = self._add_tool_action(
            toolbar, "生成返修单", "Ctrl+R", self.generate_report_dialog
        )
        toolbar.addSeparator()
        self.action_redraw = QAction("红框模式", self)
        self.action_redraw.setCheckable(True)
        self.action_redraw.toggled.connect(self._on_redraw_mode_toggled)
        toolbar.addAction(self.action_redraw)
        self.action_compare = QAction("自动对比", self)
        self.action_compare.setCheckable(True)
        self.action_compare.toggled.connect(self._on_compare_action_toggled)
        toolbar.addAction(self.action_compare)
        self.action_recenter = self._add_tool_action(
            toolbar, "定位当前图层", "", self.recenter_current_layer
        )
        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" 显示比例 "))
        # 不响应滚轮：工具栏上误滚会静默改掉显示比例，且不易察觉
        self.ratio_combo = NoWheelComboBox()
        for r in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(r * 100)}%", r)
        idx = self.ratio_combo.findData(self.settings.layer_display_ratio)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        toolbar.addWidget(self.ratio_combo)

        # ---- 状态栏 ----
        # 两阶段预加载独立显示（阶段 A 图像在左、阶段 B 图层预热在右），
        # 位于「已保存」左边（addPermanentWidget 右对齐、先加者在最右）
        self.preload_label = QLabel("")    # 阶段 A：图像预加载
        self.warmup_label = QLabel("")     # 阶段 B：图层预热
        self.save_label = QLabel("")
        self.zoom_label = QLabel("缩放：100%")
        self.hint_label = QLabel("")
        self.statusBar().addPermanentWidget(self.save_label)
        # 实测 addPermanentWidget 后加者紧邻先前者左侧：
        # 先加 A（阶段A），再加 B（阶段B）→ 视觉顺序 A | B | 已保存
        self.statusBar().addPermanentWidget(self.preload_label)
        self.statusBar().addPermanentWidget(self.warmup_label)
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().addWidget(self.hint_label)

        self._update_shortcut_hints()

    def _build_settings_banner(self) -> QWidget:
        """首次使用提醒条：只提示「可以按自己的习惯调设置」，不替用户改任何值。

        显示条件见 _refresh_settings_banner（没有 settings.json 时）。
        可关闭（本次会话有效）、可直达设置页面，不阻塞任何操作。
        """
        banner = QFrame()
        banner.setObjectName("settingsBanner")
        banner.setStyleSheet(
            f"QFrame#settingsBanner {{ background: {COLOR_BG_WIDGET};"
            f" border-bottom: 1px solid {COLOR_BORDER}; }}"
            f"QFrame#settingsBanner QLabel {{ color: {COLOR_TEXT}; }}"
        )
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(8)

        self.settings_banner_label = QLabel(first_run_banner())
        self.settings_banner_label.setWordWrap(True)
        row.addWidget(self.settings_banner_label, 1)

        self.settings_banner_button = QPushButton("打开设置…")
        self.settings_banner_button.setToolTip("打开设置页面（默认值已在代码中定好，不改也照常工作）")
        self.settings_banner_button.clicked.connect(
            lambda: self.open_settings_dialog(intro=FIRST_RUN_INTRO)
        )
        row.addWidget(self.settings_banner_button)

        self.settings_banner_close = QToolButton()
        self.settings_banner_close.setText("✕")
        self.settings_banner_close.setToolTip("本次不再提示（设置项保持默认值）")
        self.settings_banner_close.clicked.connect(self._dismiss_settings_banner)
        row.addWidget(self.settings_banner_close)

        banner.setVisible(False)
        self.settings_banner = banner
        return banner

    def _add_tool_action(self, toolbar, text: str, seq: str, slot) -> QAction:
        action = QAction(text, self)
        # 快捷键统一由 _rebuild_shortcuts 绑定（可重绑），这里只显示提示文本
        if seq:
            action.setText(f"{text} ({seq})")
        action.triggered.connect(slot)
        toolbar.addAction(action)
        return action

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction(self.action_open_psd)
        file_menu.addAction(self.action_open_folder)
        self.recent_menu = file_menu.addMenu("最近打开")
        # 启动即填充：旧版要等本次会话打开过一次任务才出现记录（菜单恒为空）
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(self.action_close)
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_renumber)
        file_menu.addAction(self.action_report)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        settings_menu = menubar.addMenu("设置(&S)")
        settings_action = QAction("设置…", self)
        # triggered 会带 checked 参数 → 用 lambda 显式调用（open_settings_dialog 有 intro 形参）
        settings_action.triggered.connect(lambda: self.open_settings_dialog())
        settings_menu.addAction(settings_action)

        # 顶栏第三项：2026-09-25 由「帮助(&H)」改名为「关于」——**不带 `&`**
        # 即不设助记符（需求量方要求解除 Alt+H 且不设新绑定）；下辖动作一个不动。
        about_menu = menubar.addMenu("关于")
        about_action = QAction("关于 MangaProof", self)
        about_action.triggered.connect(self._show_about)
        about_menu.addAction(about_action)
        # 需求 §10：「关于 → 更新」——更新入口与"关于"同处这个菜单，且**不自动触发**，
        # 只有用户主动点开才检查（不做启动自动检查，需求 §1）。
        update_action = QAction("检查更新…", self)
        update_action.triggered.connect(self._show_update_dialog)
        about_menu.addAction(update_action)
        # 本软件自身的许可（GPL-3.0-only）与第三方组件许可分开，两个入口都不占快捷键
        app_license_action = QAction("许可证…", self)
        app_license_action.triggered.connect(self._show_app_license)
        about_menu.addAction(app_license_action)
        license_action = QAction("第三方许可…", self)
        license_action.triggered.connect(self._show_licenses)
        about_menu.addAction(license_action)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME} v{__version__}</b><br><br>"
            "漫画翻译质量检查与返修标注工具。<br><br>"
            "独立于 Photoshop：不调用 Photoshop API、不修改 PSD、<br>"
            "Original 直接使用 PSD 自带 merged image。<br><br>"
            f"<b>许可证：</b>{__license__}（GNU GPL v3.0，仅此版本）<br>"
            f"{__copyright__}<br>"
            "许可证全文见「关于 → 许可证…」（随程序一同分发）。<br><br>"
            "本软件使用小米 MiSans 字体，第三方组件与许可证信息<br>"
            "见「关于 → 第三方许可」。",
        )

    def _show_app_license(self) -> None:
        dialog = AppLicenseDialog(self)
        dialog.exec()

    def _show_update_dialog(self) -> None:
        """「关于 → 检查更新…」：打开更新页面（需求 §10/§11）。

        - 对话框内部在点「保存并检查更新」或「下载更新」时提交配置（需求 §13），
          这里负责在提交后落盘；
        - 用户确认安装时，由本窗口负责"启动安装器 → 确认拉起成功 → 退出"（需求 §46）。
        """
        # CDK 明文迁移（需求 §15）：桌面端发现 settings.json 里有明文且 keyring 可用时，
        # 迁到系统凭据库并删除明文（失败则保留明文，绝不禁用更新功能）。
        from mangaproof.update import cdk_store

        if cdk_store.migrate_plaintext_cdk(self.settings.update):
            self._save_settings()

        dialog = UpdateDialog(self.settings, parent=self)
        dialog.settings_committed.connect(self._save_settings)
        dialog.install_requested.connect(self._start_update_installer)
        dialog.exec()
        # 关窗时若有后台线程没能及时退出（典型：下载阻塞在 socket 读上），
        # 对话框会把它们"脱离"出来。**必须在这里接管**：QThread 绝不能在被
        # 析构时仍在运行（Qt 会直接 abort 进程 → 闪退），而对话框是局部变量，
        # 本函数一返回就会被回收。
        self._adopt_update_workers(dialog)

    def _adopt_update_workers(self, dialog: object) -> None:
        """接管更新页脱离出来的后台线程，等它们收尾后回收（防闪退）。

        只持引用、不做任何 UI 回调：线程的信号在脱离时已被 ``disown()`` 摘掉，
        它们剩下的工作就是被取消后退出网络循环，通常在几百毫秒内结束。
        """
        workers = list(getattr(dialog, "_orphaned_workers", ()) or ())
        if not workers:
            return
        log.info("接管 %s 个仍在收尾的更新后台线程", len(workers))
        adopted: list[object] = getattr(self, "_adopted_update_workers", None) or []

        def _drop(worker=workers):
            for item in worker:
                if item in adopted:
                    adopted.remove(item)
            if not adopted:
                self._adopted_update_workers = None

        for worker in workers:
            adopted.append(worker)
            try:
                worker.finished.connect(_drop)
            except (RuntimeError, TypeError):  # pragma: no cover - 已结束/已断开
                _drop()
        self._adopted_update_workers = adopted

    def _start_update_installer(self, payload: object) -> None:
        """启动更新安装器，成功则退出主程序（需求 §40~§46）。

        顺序不能颠倒：**先确认安装器进程创建成功，再退出主程序**（需求 §46）。
        启动失败时保持运行并报告错误 —— 否则用户会既没更新、程序也没了。

        ``payload`` 是对话框递过来的 ``(包路径, SHA-256)``：哈希必须一路带到安装器
        （``--sha256``），否则安装器只能打一句"未提供 --sha256，本次不做哈希校验"
        就跳过复核（需求 §53）。
        """
        from mangaproof.update import installer as installer_module
        from mangaproof.update.errors import InstallerError
        from mangaproof.update.version import AppVersion

        package, sha256 = payload if isinstance(payload, tuple) else (payload, "")
        version = str(AppVersion.parse(__version__))
        try:
            invocation = installer_module.prepare_invocation(
                package=Path(str(package)),
                version=version,
                sha256=str(sha256 or ""),
                parent_pid=os.getpid(),
            )
            installer_module.launch(invocation)
        except InstallerError as exc:
            log.error("启动更新安装器失败：%s", exc)
            QMessageBox.critical(self, "更新失败", str(exc))
            return
        except Exception as exc:  # pragma: no cover - 兜底
            log.exception("启动更新安装器时出现未预期异常")
            QMessageBox.critical(self, "更新失败", f"启动安装器失败：\n{exc}")
            return

        self._saved_update_token = invocation.token
        log.info("安装器已启动，主程序即将退出以完成更新")
        # 让对话框先关闭、再走正常的关闭流程（closeEvent 会保存设置并收敛后台线程）
        QTimer.singleShot(0, self.close)

    def _show_licenses(self) -> None:
        dialog = LicenseDialog(self)
        dialog.exec()

    def _rebuild_recent_menu(self) -> None:
        """按 recent.json 重建「最近打开」子菜单。

        QAction 一律挂在子菜单下（而非主窗口），这样 clear() 会连同旧项
        一起销毁——否则每重建一次都会在窗口上留下永不回收的孤儿 action。
        """
        self.recent_menu.clear()
        recent = self.recent_manager.paths
        if not recent:
            empty = QAction("（无记录）", self.recent_menu)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path_str in recent:
            action = QAction(path_str, self.recent_menu)
            action.triggered.connect(
                lambda _=False, p=path_str: self._open_recent(p)
            )
            self.recent_menu.addAction(action)
        self.recent_menu.addSeparator()
        clear_action = QAction("清除最近打开记录", self.recent_menu)
        clear_action.triggered.connect(self._clear_recent)
        self.recent_menu.addAction(clear_action)

    def _open_recent(self, path_str: str) -> None:
        path = Path(path_str)
        if path.is_dir():
            self.open_folder(path)
        elif path.is_file():
            self.open_single(path)
        else:
            # 失效记录：提示的同时直接移除，不然每次点到都弹一次同样的框
            self.recent_manager.remove(path_str)
            self._rebuild_recent_menu()
            QMessageBox.warning(
                self, "最近打开",
                f"路径已不存在，已从「最近打开」中移除：\n{path_str}",
            )

    def _clear_recent(self) -> None:
        if self.recent_manager.is_empty():
            return
        self.recent_manager.clear()
        self._rebuild_recent_menu()
        self.statusBar().showMessage("已清除最近打开记录", 3000)

    # 常用键的友好显示名（与设置对话框的 Qt 键名对应）
    _DISPLAY_KEY_MAP = {
        "Up": "↑", "Down": "↓", "Left": "←", "Right": "→",
        "Return": "Enter", "Enter": "Enter",
    }

    @staticmethod
    def _display_key(key: str) -> str:
        return MainWindow._DISPLAY_KEY_MAP.get(key, key)

    def _update_shortcut_hints(self) -> None:
        kb = self.settings.keybindings
        d = self._display_key
        self.hint_label.setText(
            f"{d(kb.get('prev_psd', 'Up'))}/{d(kb.get('next_psd', 'Down'))} PSD　"
            f"{d(kb.get('prev_layer', 'Left'))}/{d(kb.get('next_layer', 'Right'))} 图层　"
            f"{d(kb.get('pass_layer', 'Return'))} 通过　"
            f"{d(kb.get('fail_layer', '/'))} 未通过　"
            f"{d(kb.get('toggle_compare', 'Space'))} 对比　"
            f"{d(kb.get('cancel_operation', 'Esc'))} 取消　"
            f"{d(kb.get('save_task', 'Ctrl+S'))} 保存　"
            f"{d(kb.get('close_task', 'Ctrl+W'))} 关闭　"
            f"Ctrl+滚轮 缩放　Alt+滚轮 左右移动"
        )
        # 空画布（未打开 / 已关闭任务）提示：随当前绑定更新，不写死默认键
        self.viewer.set_empty_hint(
            "未打开任务\n\n"
            f"{d(kb.get('open_psd', 'Ctrl+O'))}　打开单个 PSD　　"
            f"{d(kb.get('open_folder', 'Ctrl+Shift+O'))}　打开漫画文件夹\n"
            "「文件 → 最近打开」可回到打开过的任务"
        )
        # 工具栏/菜单按钮同步显示当前绑定（需求 §30）
        self.action_open_psd.setText(f"打开 PSD ({d(kb.get('open_psd', 'Ctrl+O'))})")
        self.action_open_folder.setText(f"打开文件夹 ({d(kb.get('open_folder', 'Ctrl+Shift+O'))})")
        self.action_close.setText(f"关闭任务 ({d(kb.get('close_task', 'Ctrl+W'))})")
        self.action_save.setText(f"保存 ({d(kb.get('save_task', 'Ctrl+S'))})")
        self.action_report.setText(f"生成返修单 ({d(kb.get('generate_report', 'Ctrl+R'))})")
        self.action_redraw.setText(f"红框模式 ({d(kb.get('redraw_mode', 'R'))})")
        self._update_compare_action_text()

    # ================================================================= 快捷键

    def _rebuild_shortcuts(self) -> None:
        for sc in self._shortcuts:
            sc.setParent(None)
            sc.deleteLater()
        self._shortcuts = []

        kb = self.settings.keybindings

        def bind(key: str, slot, guard=None):
            seq = self._parse_seq(key)
            if seq is None:
                return
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)

            def handler():
                if guard is not None and not guard():
                    return
                slot()

            sc.activated.connect(handler)
            # 同一序列被绑定多次时 Qt 判定为歧义：activated 不触发，
            # 只发 activatedAmbiguously（表现就是"按键完全没反应"）。
            # 这里给出明确提示，否则用户无从知道是哪个键跟谁撞了。
            sc.activatedAmbiguously.connect(
                lambda s=seq.toString(): self._on_ambiguous_shortcut(s)
            )
            self._shortcuts.append(sc)

        # 单键快捷键在文本输入框聚焦时不触发（问题类型、自动框选等）
        issue_guard = self._focus_not_text_input

        bind(kb.get("prev_psd", "Up"), self.prev_psd)
        bind(kb.get("next_psd", "Down"), self.next_psd)
        bind(kb.get("prev_layer", "Left"), self.prev_layer)
        bind(kb.get("next_layer", "Right"), self.next_layer)
        bind(kb.get("pass_layer", "Return"), self.mark_pass)
        bind(kb.get("fail_layer", "/"), self.mark_fail)
        bind(kb.get("toggle_compare", "Space"), self.toggle_compare)
        bind(kb.get("cancel_operation", "Esc"), self.cancel_operation)
        bind(kb.get("save_task", "Ctrl+S"), self.save_task)
        bind(kb.get("custom_comment", "Ctrl+Return"), self._on_custom_comment)
        bind(kb.get("redraw_mode", "R"), self.toggle_redraw_mode)
        # 自动框选：仅在拖框模式下生效（否则给提示，见 auto_box_current_layer）
        bind(kb.get("auto_box", "A"), self.auto_box_current_layer, guard=issue_guard)
        # 工具栏/菜单上标注的这三个也必须真的绑定（历史缺陷：只印了提示文本，
        # 没绑 QShortcut，按下去没有任何反应）
        bind(kb.get("open_psd", "Ctrl+O"), self.open_psd_dialog)
        bind(kb.get("open_folder", "Ctrl+Shift+O"), self.open_folder_dialog)
        bind(kb.get("close_task", "Ctrl+W"), self.close_task)
        bind(kb.get("generate_report", "Ctrl+R"), self.generate_report_dialog)

        # 问题类型快捷键（需求 §35）：文本输入框聚焦时不触发
        for item in self.settings.issue_types:
            key = item.get("key", "")
            name = item["name"]
            if key:
                bind(key, lambda n=name: self._on_issue_key(n), guard=issue_guard)

        self._warn_shortcut_conflicts()
        self._update_shortcut_hints()
        self._update_issue_panel_shortcuts()

    def _shortcut_conflict_text(self, seq: str) -> str:
        """该序列撞车的动作名（用于提示），无冲突返回空串。"""
        conflicts = shortcut_conflicts(
            self.settings.keybindings,
            self.settings.issue_types,
            self.settings.custom_comment_key,
        )
        names = conflicts.get(normalize_key(seq))
        if not names:
            return ""
        return " ／ ".join(f"{kind}：{label}" for kind, label in names)

    def _on_ambiguous_shortcut(self, seq: str) -> None:
        """歧义快捷键被按下：明确告诉用户撞在哪，而不是静默无反应。"""
        detail = self._shortcut_conflict_text(seq)
        msg = (
            f"快捷键 {seq} 被绑定到多个动作，已失效：{detail}"
            if detail
            else f"快捷键 {seq} 存在歧义，未能触发（请在设置中重新绑定）"
        )
        log.warning("%s", msg)
        self.statusBar().showMessage(msg, 6000)

    def _warn_shortcut_conflicts(self) -> None:
        """启动/改绑后检查一遍冲突，冲突则提示（不静默失效）。"""
        conflicts = shortcut_conflicts(
            self.settings.keybindings,
            self.settings.issue_types,
            self.settings.custom_comment_key,
        )
        if not conflicts:
            return
        detail = "；".join(
            f"{seq}（" + " ／ ".join(f"{kind}：{label}" for kind, label in names) + "）"
            for seq, names in sorted(conflicts.items())
        )
        log.warning("快捷键冲突：%s", detail)
        self.statusBar().showMessage(
            f"⚠ 快捷键冲突，按下不会有反应：{detail}（可在 设置 →「设置快捷键…」中改绑）",
            10000,
        )

    def _update_issue_panel_shortcuts(self) -> None:
        """问题面板按钮动态显示当前绑定（需求 §30）。"""
        kb = self.settings.keybindings
        d = self._display_key
        bindings = {
            "pass": d(kb.get("pass_layer", "Return")),
            "fail": d(kb.get("fail_layer", "/")),
            "redraw": d(kb.get("redraw_mode", "R")),
            "auto_box": d(kb.get("auto_box", "A")),
            "custom": d(self.settings.custom_comment_key or "Ctrl+Return"),
            "cancel": d(kb.get("cancel_operation", "Esc")),
        }
        tips = ["问题类型快捷键（在设置中可重绑定）："]
        for item in self.settings.issue_types:
            key = item.get("key", "")
            if key:
                tips.append(f"{key}　{item['name']}")
        tips.append(f"{bindings['custom']}　自定义批注")
        tips.append(f"{bindings['cancel']}　取消拖框")
        self.issue_panel.set_shortcut_labels(bindings, "\n".join(tips))

    @staticmethod
    def _parse_seq(key: str) -> Optional[QKeySequence]:
        if not key:
            return None
        seq = QKeySequence(key)
        if not seq.isEmpty():
            return seq
        if len(key) == 1:
            ch = key[0]
            mapping = {
                "/": QKeySequence(Qt.Key.Key_Slash),
                "\\": QKeySequence(Qt.Key.Key_Backslash),
                "`": QKeySequence(Qt.Key.Key_QuoteLeft),
            }
            if ch in mapping:
                return mapping[ch]
            if ch.isalnum():
                return QKeySequence(ch.upper())
        log.warning("无法解析快捷键：%r", key)
        return None

    def _focus_widget(self):
        return QApplication.focusWidget()

    def _focus_not_text_input(self) -> bool:
        w = self._focus_widget()
        if w is None:
            return True
        return type(w).__name__ not in _TEXT_INPUT_TYPES

    # ================================================================= 打开任务（需求 §5～§7）

    def open_psd_dialog(self) -> None:
        # 选择器按平台分流（桌面 = 原来那两行 QFileDialog，逐参数不变；
        # Android = 同一 API 但强制 DontUseNativeDialog，绕开 Qt 原生 SAF 对话框的卡死）
        # —— 见 mangaproof/storage/picker.py
        path_str = picker.pick_psd_file(self)
        if path_str:
            self.open_single(Path(path_str))

    def open_folder_dialog(self) -> None:
        folder = picker.pick_folder(self)
        if folder:
            self.open_folder(Path(folder))

    def open_single(self, path: Path) -> None:
        self._start_load("single", path, force_fresh=False)

    def open_folder(self, folder: Path) -> None:
        self._start_load("folder", folder, force_fresh=False)

    # -- 后台加载（扫描/验证/解析均在工作线程，UI 显示进度，防止假死） ----

    def _start_load(self, mode: str, path: Path, force_fresh: bool = False) -> None:
        if self._loader is not None and self._loader.isRunning():
            return
        self._compare.interrupt()
        self._load_mode = mode
        self._load_path = Path(path)

        worker = TaskLoadWorker(
            mode, path,
            recursive=self.settings.recursive_scan,
            force_fresh=force_fresh,
            layer_cache=self._layer_cache,
        )
        dialog = QProgressDialog("准备…", "取消", 0, 1, self)
        dialog.setWindowTitle("打开任务")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(460)
        dialog.setAutoClose(True)
        # autoReset 会在值到达 100% 时触发 reset 并连带 canceled 信号，
        # 由 _close_load_ui 统一关闭，无需自动重置。
        dialog.setAutoReset(False)

        worker.progress.connect(self._on_load_progress)
        worker.succeeded.connect(self._on_load_finished)
        worker.failed.connect(self._on_load_failed)
        dialog.canceled.connect(worker.request_cancel)

        self._loader = worker
        self._load_dialog = dialog
        self.action_open_psd.setEnabled(False)
        self.action_open_folder.setEnabled(False)
        dialog.show()
        worker.start()

    def _on_load_progress(self, done: int, total: int, message: str) -> None:
        dialog = self._load_dialog
        if dialog is None:
            return
        dialog.setMaximum(max(total, 1))
        dialog.setValue(min(done, total))
        # 模态进度框的 setValue 内部会 pump 事件循环，可能重入导致对话框
        # 已被关闭（self._load_dialog 置 None），需复查后再更新文案。
        if self._load_dialog is dialog:
            dialog.setLabelText(message)

    def _close_load_ui(self) -> None:
        self.action_open_psd.setEnabled(True)
        self.action_open_folder.setEnabled(True)
        if self._load_dialog is not None:
            self._load_dialog.close()
            self._load_dialog.deleteLater()
            self._load_dialog = None
        if self._loader is not None:
            self._loader.deleteLater()
            self._loader = None

    def _on_load_failed(self, message: str) -> None:
        self._close_load_ui()
        QMessageBox.critical(self, "打开失败", f"加载任务时发生错误：\n{message}")

    def _on_load_finished(self, result) -> None:
        self._close_load_ui()
        kind = result.kind

        if kind == KIND_CANCELLED:
            self.statusBar().showMessage("已取消打开", 3000)
            return
        if kind == KIND_NO_FILES:
            QMessageBox.information(self, "打开文件夹", "该文件夹中没有找到 PSD 文件。")
            return
        if kind == KIND_MISMATCH:
            reselect_file = self._load_mode == "single"
            if self._ask_discard_or_reselect(
                "进度文件验证失败", result.reason, reselect_file
            ):
                # 用户确认放弃旧进度 → 先备份再强制新建（需求 §7.6 防御）
                progress = (
                    progress_path_for_single(self._load_path)
                    if reselect_file
                    else progress_path_for_folder(self._load_path)
                )
                backup_progress_file(progress)
                self._start_load(self._load_mode, self._load_path, force_fresh=True)
            return

        # KIND_OK：恢复或新建成功
        if result.rebind and not self._strong_rebind_warning():
            return

        self._attach_task(
            result.task,
            result.base_dir,
            layer_ids_by_file=result.layer_ids_by_file,
            layer_names_by_file=result.layer_names_by_file,
            docs=result.docs,
        )
        self.recent_manager.add(self._load_path)
        self._rebuild_recent_menu()
        if result.file_errors:
            QMessageBox.warning(
                self,
                "部分 PSD 无法读取",
                "以下文件解析失败，已跳过：\n" + "\n".join(result.file_errors),
            )

    def _ask_discard_or_reselect(self, title: str, reason: str, reselect_file: bool) -> bool:
        """验证失败：禁止恢复（需求 §7.6），询问用户重新选择或放弃进度。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(f"进度文件与当前选择不匹配，禁止恢复原监制状态（需求 §7.6）。\n\n{reason}")
        reselect_btn = box.addButton("重新选择", QMessageBox.ButtonRole.ActionRole)
        box.addButton("放弃进度，新建任务", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is reselect_btn:
            if reselect_file:
                self.open_psd_dialog()
            else:
                self.open_folder_dialog()
            return False
        return clicked.text() == "放弃进度，新建任务"

    def _strong_rebind_warning(self) -> bool:
        """重新选择文件/文件夹时的醒目提醒（需求 §7.8）。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("⚠ 重要提醒")
        box.setText(_REBIND_WARNING)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.button(QMessageBox.StandardButton.Yes).setText("继续绑定并恢复")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        return box.exec() == QMessageBox.StandardButton.Yes

    def _attach_task(
        self,
        task: TaskState,
        base_dir: Path,
        layer_ids_by_file=None,
        layer_names_by_file=None,
        docs=None,
    ) -> None:
        """绑定任务并打开。

        后台加载时传入预扫描的图层数据与文档；未提供时同步兜底扫描。
        """
        self.task = task
        self._base_dir = base_dir.resolve()
        self._docs = dict(docs) if docs is not None else {}
        self._layer_ids_by_file = (
            dict(layer_ids_by_file) if layer_ids_by_file is not None else {}
        )
        self._layer_names_by_file = (
            dict(layer_names_by_file) if layer_names_by_file is not None else {}
        )
        self._current_file = ""
        self._current_index = -1

        # 打开时任务若已完成，视为「已提示过」：不因重新打开而自动生成返修单
        # （需要时按 Ctrl+R 手动生成）
        self._completion_announced = False

        # 兜底：补齐未扫描的 PSD 图层树（每个 PSD 只解析一次，需求 §59）
        for record in task.files:
            rel = record.relative_path
            if rel in self._layer_ids_by_file:
                continue
            try:
                doc = self._ensure_doc(rel)
                if doc is None:
                    log.warning("跳过无法解析的 PSD：%s", rel)
                    continue
                self._layer_ids_by_file[rel] = [info.id for info in doc.layers]
                self._layer_names_by_file[rel] = [info.name for info in doc.layers]
            except (PSDReadError, OSError) as exc:
                log.warning("解析失败 %s：%s", rel, exc)

        self._completion_announced = self._all_reviewed()

        self.file_panel.set_task_info(task.task_name, task.task_type)
        self.file_panel.set_files([r.relative_path for r in task.files])
        self._preload_targets.clear()
        self._extra_targets.clear()
        self._pending_qimages.clear()
        self._preload_scheduled = False
        self._update_preload_label()

        rel = task.current_file
        if rel not in self._layer_ids_by_file:
            rel = next(iter(self._layer_ids_by_file), "")
        if rel:
            self._request_open_file(rel, restore=True)
        self._refresh_all_panels()
        self._mark_dirty(save_immediately=False)
        # 布局完成后重新定位一次（窗口尺寸此时才确定）
        QTimer.singleShot(0, self.recenter_current_layer)
        self.viewer.setFocus()
        log.info("任务已加载：%s（%s）", task.task_name, task.task_type)

    # ================================================================= 关闭任务

    def close_task(self) -> None:
        """关闭当前任务（文件夹 / 单个 PSD），回到「未打开」状态。

        Ctrl+W /「文件 → 关闭当前任务」。任务文件**不会被删除**：状态、红框、
        批注都留在磁盘上，下次打开同一文件夹或从「最近打开」回来即可继续。

        - 先落盘再关闭：自动保存有 1.5s 防抖，标完最后一项立刻关闭会丢掉改动，
          所以这里同步保存一次；保存失败则中止关闭并说明原因（磁盘满 / 只读 /
          权限），保留现场让用户处理，不静默丢进度；
        - 停掉自动对比与拖框标注、作废预加载队列、释放文档与图层像素缓存，
          长时间开着多个任务不会积累内存。
        """
        if self.task is None:
            return
        name = self.task.task_name

        # 后台仍在读任务数据的 worker（返修单 / 编号检查）：先取消并等待，
        # 避免它们回调到已经清空的窗口状态（与 closeEvent 同一策略）
        for worker in (self._report_worker, self._numbering_worker):
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                worker.wait(5000)

        self._autosave_timer.stop()
        if not self.save_task():
            QMessageBox.warning(
                self,
                "关闭当前任务",
                "任务保存失败，已取消关闭以免丢失监制进度。\n"
                "请检查磁盘空间 / 文件权限后重试。\n\n"
                f"原因：{self._save_error}",
            )
            return

        # 交互状态复位：停对比（回 Original）→ 退出拖框/待标注
        self._compare.interrupt()
        self.viewer.cancel_pending()
        self.action_redraw.setChecked(False)

        # 预加载：作废队列与在途请求（后台线程只读已创建文档，清空 _docs 后空转）
        self._preload.set_preloads([], [])
        self._preload.cancel_open()
        self._pending_open = None
        self._open_restore = False
        self._close_open_progress()

        # 任务数据与缓存（重载荷释放：见 _release_task_data）
        self._release_task_data()

        # 画布与面板回到初始空态
        self.viewer.set_document(None)
        self.viewer.set_issues([])
        self.viewer.set_layer_outline(None)
        self.viewer.set_source(SOURCE_MERGED)
        camera = self.viewer.camera
        camera.center_x = camera.center_y = 0.0
        camera.zoom = 1.0
        self.zoom_label.setText("缩放：100%")

        self.file_panel.clear()
        self.layer_panel.set_layers([])
        self.stats_panel.clear()
        self.issue_panel.clear()

        self._update_preload_label()          # 清空状态栏两阶段提示
        self._update_save_label(initial=True)
        self._refresh_enabled_state()
        self._refresh_title()
        self.viewer.setFocus()
        self.statusBar().showMessage("已关闭当前任务", 3000)
        log.info("任务已关闭：%s", name)

    def _release_task_data(self) -> None:
        """释放与当前任务绑定的全部重载荷，回到「无任务」数据态。

        为什么单独抽一个方法
        --------------------
        窗口关闭（closeEvent）过去只停线程 + 存盘，**没有释放文档与图层像素
        缓存**：实测每关一个窗口常驻内存只涨不落（真实尺寸 PSD 下约 110 MB/
        窗口），开着多个任务或反复开关窗口会持续积累，最终把进程撑爆。
        这里与 close_task() 共用同一段释放逻辑，保证「关任务」和「关窗口」
        两条路径的释放行为不会再次分叉。

        释放对象（都是随任务生命周期存在的重载荷）：
        - _docs：每份 PSDDocument 持有 merged / 背景 numpy（全分辨率）；
        - _layer_cache：图层像素 LRU（全分辨率 RGBA）；
        - _pending_qimages：预加载预生成的 QImage；
        - 以及各查询表 / 预加载调度状态。

        幂等：无任务时调用无副作用；`task` 本身只置 None，任务文件不动
        （落盘由调用方负责，见 close_task 的「先落盘再关闭」）。
        """
        # 先开启缓存新一代：预加载线程可能仍持有旧文档并在途写入像素，
        # 递增代次后这些迟到写入会被丢弃，不会在 clear() 之后重新长回来。
        self._layer_cache.begin_release()
        self.task = None
        self._base_dir = None
        self._current_file = ""
        self._current_index = -1
        self._docs.clear()
        self._layer_ids_by_file.clear()
        self._layer_names_by_file.clear()
        self._layer_cache.clear()
        self._warned_no_composite.clear()
        self._pending_qimages.clear()
        self._preload_targets.clear()
        self._extra_targets.clear()
        self._keep_set.clear()
        self._evict_pending.clear()
        self._evict_timer.stop()
        self._evict_retries = 0
        self._pinned_bg_key = None
        self._preload_scheduled = False
        self._completion_announced = False

    # ================================================================= 文档管理

    def _ensure_doc(self, rel: str) -> Optional[PSDDocument]:
        if self._base_dir is None:
            return None
        doc = self._docs.get(rel)
        if doc is None:
            # 保留窗口之外的页不得因「迟到的预加载」重新住留：预加载线程
            # 在切页后仍可能为旧页请求文档，若无条件重建就会把刚被驱逐的
            # 文档对象重新塞回 _docs（真实尺寸下每份都很大）。
            # 需要时后续正常打开路径会重新创建，不影响功能。
            if self._preload_scheduled and rel not in self._keep_set:
                if rel != self._current_file:
                    return None
            try:
                doc = PSDDocument(self._base_dir / rel, layer_cache=self._layer_cache)
            except PSDReadError:
                log.exception("无法读取该 PSD 文件：%s", rel)
                return None
            self._docs[rel] = doc
        return doc

    @property
    def current_doc(self) -> Optional[PSDDocument]:
        return self._docs.get(self._current_file)

    # ================================================================= 导航（需求 §11～§13）

    def _on_file_activated(self, index: int) -> None:
        if self.task is None or self._updating_panels:
            return
        if 0 <= index < len(self.task.files):
            rel = self.task.files[index].relative_path
            if rel != self._current_file:
                self._switch_file(rel)

    def _switch_file(self, rel: str) -> None:
        self._compare.interrupt()
        self._request_open_file(rel, restore=False)

    def _request_open_file(self, rel: str, restore: bool) -> None:
        """打开文件入口：merged 与目标图层均已预热走快路径；
        否则交后台线程 + 进度框（UI 不冻结）。

        快速连续切换时，新请求会替换未处理的旧请求（见 PreloadWorker）。
        """
        # 先把保留窗口切到目标页：_ensure_doc 会拒绝为窗口外页面重建文档
        # （防迟到的预加载把已驱逐的大文档塞回来），所以顺序不能反。
        if self.task is not None:
            self._keep_set = self._window_keep_set(rel)
        doc = self._ensure_doc(rel)
        if doc is None:
            QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")
            return
        self._open_restore = restore
        index = self._choose_layer_index(rel, restore)
        layer_id = ""
        if index is not None:
            ids = self._layer_ids_by_file.get(rel, [])
            if index < len(ids):
                layer_id = ids[index]
        # 快路径：merged 已缓存 且 目标图层像素已预热
        #（否则 visual_bounds 会在 UI 线程提取像素造成半秒级卡顿）
        if doc.has_merged() and self._target_layer_warm(doc, layer_id):
            self._open_file_internal(rel, restore)
            self._refresh_all_panels()
            self._mark_dirty()
            self.viewer.setFocus()
            self._schedule_preloads(rel)
            return
        # 未预热：后台提取 + 非模态忙碌进度框（不阻塞继续切换）
        self._pending_open = (rel, "file")
        self._preload.submit_open(rel, layer_id)
        self._show_open_progress(rel)

    @staticmethod
    def _target_layer_warm(doc: PSDDocument, layer_id: str) -> bool:
        """目标图层的视觉边界是否已计算（无需 UI 线程提取像素）。"""
        if not layer_id:
            return True
        info = doc.layer_by_id(layer_id)
        return info is None or info.has_visual_bounds()

    def _choose_layer_index(self, rel: str, restore: bool) -> Optional[int]:
        """目标图层选择（需求 §13）：restore 用上次位置；
        否则优先第一个未监制，全部完成则回上次位置。"""
        layer_ids = self._layer_ids_by_file.get(rel, [])
        if not layer_ids:
            return None
        if restore:
            index = None
            if self.task and self.task.current_layer in layer_ids:
                index = layer_ids.index(self.task.current_layer)
            if index is None:
                index = navigator.first_unreviewed_index(
                    layer_ids, self.task.reviews, rel
                )
            return 0 if index is None else index
        index = navigator.first_unreviewed_index(layer_ids, self.task.reviews, rel)
        if index is None:
            index = None
            if self.task and self.task.current_layer in layer_ids:
                index = layer_ids.index(self.task.current_layer)
            return 0 if index is None else index
        return index

    def _open_file_internal(self, rel: str, restore: bool) -> None:
        """打开 PSD 并选择图层（快路径：图像已缓存，需求 §12、§13）。

        文档对象可能已被内存策略驱逐（窗口外），此处惰性重建。
        """
        doc = self._ensure_doc(rel)
        if doc is None:
            QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")
            return
        self._current_file = rel
        self._pin_current_bg()
        self.viewer.set_document(doc)
        self.viewer.set_source(SOURCE_MERGED)
        # 注入后台预热好的显示图像（首帧免 QImage 转换，消除切换卡顿）
        warm_images = self._pending_qimages.pop(rel, None)
        if warm_images:
            if warm_images.get("merged") is not None:
                self.viewer.inject_qimage(doc, SOURCE_MERGED, warm_images["merged"])
            if warm_images.get("bg") is not None:
                self.viewer.inject_qimage(doc, SOURCE_BG, warm_images["bg"])

        # Original 必须来自 PSD 自带 merged image（需求 §2.3、§61）
        if rel not in self._warned_no_composite:
            try:
                doc.merged_np()
            except NoCompositeError:
                self._warned_no_composite.add(rel)
                QMessageBox.warning(
                    self,
                    "无 Original",
                    "该 PSD 不包含可用的 merged/composite image，\n"
                    "本程序无法提供 Original 显示（不进行程序重新合成）。",
                )
            except Exception:
                self._warned_no_composite.add(rel)
                QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")

        layer_ids = self._layer_ids_by_file.get(rel, [])
        self.layer_panel.set_layers(self._layer_names_by_file.get(rel, []))

        if not layer_ids:
            self._current_index = -1
            self.viewer.set_issues(self._viewer_issues())
            self._refresh_viewer_outline()
            return

        index = self._choose_layer_index(rel, restore)
        self._select_layer_internal(index)
        # 文件与图层都落定后再刷新方向键：_switch_file 是异步的，"刚设置
        # _current_file"那一刻任务/图层状态还没确定，早刷新会算出错误的上/下边界。
        self._refresh_nav_pad()

    # -- 预加载与异步打开（大 PSD 切换不卡顿） -----------------------------

    def _on_preload_done(self, rel: str, kind: str, ok: bool, images=None) -> None:
        if images and (rel in self._keep_set or rel == self._current_file):
            # 被驱逐文档的在途结果不再入池（避免窗口外残留）
            # 重插字典键保持「最近预生成」序，供配额驱逐按最久未用挑选
            slot = self._pending_qimages.pop(rel, {})
            for source in ("merged", "bg"):
                if images.get(source) is not None:
                    slot[source] = images[source]
            if slot:
                self._pending_qimages[rel] = slot
                self._trim_bg_qimages()
        # 预加载目标告一段落：重试被 io 锁推迟的驱逐
        self._drain_pending_evictions()
        if kind == KIND_PRELOAD:
            # 阶段 A（merged）完成：切换文件已可用
            self._preload_targets.discard(rel)
            self._update_preload_label()
            return
        if kind == KIND_EXTRA:
            # 阶段 B（背景图/目标图层像素）完成
            self._extra_targets.discard(rel)
            self._update_preload_label()
            if rel == self._current_file:
                self._pin_current_bg()   # bg id 此刻已解析，补钉当前页
            return
        # open 结果：统一按单一 pending 标记分派（文件 / 图层），过期结果丢弃
        pending = self._pending_open
        if pending is None or pending[0] != rel:
            return
        self._pending_open = None
        self._close_open_progress()
        if pending[1] == "layer":
            _, _, index = pending
            if ok and self.current_doc is not None:
                self._select_layer_internal(index)
                self._refresh_layer_selection()
                self._mark_dirty()
                # 慢路径也把焦点还给画布：与快路径（_request_layer_switch
                # 命中已预热边界）保持一致。进度框是非模态的，关闭后焦点
                # 可能停在按钮上，单键快捷键（Enter/Space/←→）就按不动了。
                self.viewer.setFocus()
            return
        # 文件打开
        if not ok:
            QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")
            return
        self._open_file_internal(rel, self._open_restore)
        self._refresh_all_panels()
        self._mark_dirty()
        self.viewer.setFocus()
        self._schedule_preloads(rel)

    def _show_open_progress(self, rel: str) -> None:
        if self._open_dialog is None:
            dialog = QProgressDialog("正在加载…", "取消", 0, 0, self)
            dialog.setWindowTitle("切换 PSD")
            # 非模态：用户可继续用键盘快速连续切换
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setMinimumDuration(0)
            dialog.setMinimumWidth(400)
            dialog.setAutoClose(True)
            dialog.canceled.connect(self._on_open_progress_cancelled)
            self._open_dialog = dialog
        self._open_dialog.setLabelText(
            f"正在加载 {rel}…\n（提取 merged image、背景图层与目标图层像素）"
        )
        self._open_dialog.show()

    def _close_open_progress(self) -> None:
        dialog = self._open_dialog
        if dialog is None:
            return
        self._open_dialog = None
        # 先断开 canceled：close() 可能触发该信号导致重入
        try:
            dialog.canceled.disconnect(self._on_open_progress_cancelled)
        except (RuntimeError, TypeError):
            pass
        dialog.close()
        dialog.deleteLater()
        # 进度框会抢走窗口激活态：非模态框关闭后系统不一定会还给主窗口，
        # 窗口不激活则所有 WindowShortcut 单键快捷键（Enter/Space/←→）全部失效
        #（实测按 ←/→ 触发一次后台提取后，后续 Enter 完全无响应）。
        # 这里显式把激活态与焦点交还主窗口 / 画布。
        self.activateWindow()
        self.raise_()
        self.viewer.setFocus()

    def _on_open_progress_cancelled(self) -> None:
        self._preload.cancel_open()
        self._pending_open = None
        self._close_open_progress()
        self.statusBar().showMessage("已取消打开", 3000)

    def _window_keep_set(self, rel: str) -> set:
        """以 rel 为当前页的驱逐保留窗口 = 当前页 + 全部邻域（与预热范围同源）。

        与 _current_keep_set 的区别：那个以「已生效」的 _current_file 为准，
        这个接受调用方指定的目标页——切页时必须**先**用目标页刷新窗口，
        否则目标页会被当成窗口外页面拒绝创建（见 _ensure_doc 的迟到预加载防护）。
        """
        if self.task is None:
            return set()
        order = [r.relative_path for r in self.task.files]
        try:
            i = order.index(rel)
        except ValueError:
            return {rel}
        return {
            order[i + d]
            for d in preload_window_offsets()
            if 0 <= i + d < len(order)
        }

    def _schedule_preloads(self, rel: str) -> None:
        """预加载当前文件邻域（后 3 个 + 前 1 个），并回收窗口外大图内存。

        两阶段队列（见 PreloadWorker）：
        - 阶段 A：候选文件的 merged image（切换关键路径，优先铺开）；
        - 阶段 B：背景图（自动对比用）+ 目标图层像素（定位缩放用），
          当前文件优先。
        """
        if self.task is None:
            return
        order = [r.relative_path for r in self.task.files]
        try:
            i = order.index(rel)
        except ValueError:
            return
        # 邻域（平台分档，见 config/settings.preload_window_offsets）：
        # 桌面 = 后 3 + 前 1；Android = 后 1 + 前 1。
        offsets = preload_window_offsets()
        candidates: List[str] = [
            order[i + d]
            for d in offsets
            if d != 0 and 0 <= i + d < len(order)
        ]
        # 驱逐保留窗口 = 当前页 + 全部邻域（与预热范围同源）
        keep = self._window_keep_set(rel)

        def target_layer_of(target_rel: str) -> str:
            index = self._choose_layer_index(target_rel, restore=False)
            if index is None:
                return ""
            ids = self._layer_ids_by_file.get(target_rel, [])
            return ids[index] if index < len(ids) else ""

        merged_jobs: List[Tuple[str, str]] = []
        extra_jobs: List[Tuple[str, str]] = []
        # 当前文件与邻域文件：后台预热背景图 + 全部图层的视觉边界
        #（任意文件的任意 ←→ 图层切换免等待）；已完成的部分跳过，
        # 避免重复排队。流式加载/驱逐后窗口内邻域文档可能缺失，
        # 此处惰性创建保证预加载覆盖（否则新邻居永不入队，切换必走慢路径）。
        for c in [rel, *candidates]:
            doc = self._docs.get(c)
            if doc is None:
                doc = self._ensure_doc(c)
                if doc is None:
                    continue
            if not doc.has_merged():
                merged_jobs.append((c, target_layer_of(c)))
            if not doc.has_bg() or not self._all_layers_warm(doc):
                extra_jobs.append((c, WARM_ALL))

        self._preload_targets = {r for r, _ in merged_jobs}
        self._extra_targets = {r for r, _ in extra_jobs}
        self._keep_set = keep
        self._preload.set_preloads(merged_jobs, extra_jobs)
        self._preload_scheduled = True
        self._update_preload_label()

        # 内存策略：释放窗口外文档的 merged/bg 与预热显示图
        #（图层树与 LRU 保留）。release_images 非阻塞：后台仍在
        # 提取的文档自动跳过，下轮再回收。
        for r in order:
            if r in keep:
                continue
            doc = self._docs.get(r)
            if doc is not None:
                doc.release_images()
            self._pending_qimages.pop(r, None)
        # 内存策略：窗口外完整文档对象驱逐（psd-tools 结构 ≈ 文件大小，
        # 是最大的随书增长内存池）。驱逐后重看时 _ensure_doc 惰性重建。
        self._evict_outside_window(keep)

    @staticmethod
    def _all_layers_warm(doc: PSDDocument) -> bool:
        """文档全部图层的视觉边界是否已计算（图层切换免提取）。

        已标记「全量预热完成」的文档直接视为就绪（个别失败图层不重试）。
        """
        return doc.all_layers_warmed or all(
            info.has_visual_bounds() for info in doc.layers
        )

    # -- 内存策略（三档热应用 / 驱逐 / 钉住 / bg QImage 池配额） -----------

    def _apply_memory_policy(self) -> None:
        """按设置档位热应用 LRU 预算与 bg QImage 池配额（无需重启）。

        档位先过 effective_memory_policy()：它负责平台侧规整（Android 恒激进）
        与非法值回落。设置对象本身已是规整后的值，这里再兜一层，防的是将来
        有人绕过设置读取直接改字段。
        """
        policy = effective_memory_policy(self.settings.memory_policy)
        cfg = _MEMORY_POLICIES.get(policy)
        if cfg is None:   # effective_memory_policy 保证不会走到这里，防御性回落
            log.warning("未知内存策略 %r，回落 balanced", policy)
            cfg = _MEMORY_POLICIES["balanced"]
        self._layer_cache.set_max_bytes(cfg["lru_bytes"])
        self._bg_qimage_quota = cfg["bg_qimage_bytes"]
        self._trim_bg_qimages()

    def _trim_bg_qimages(self) -> None:
        """bg QImage 池超配额时按「最久未预生成」驱逐（当前页除外）。

        merged QImage 不参与配额（窗口有界，且是切页关键路径）。
        """
        total = 0
        for slot in self._pending_qimages.values():
            bg = slot.get("bg")
            if bg is not None:
                total += int(bg.sizeInBytes())
        if total <= self._bg_qimage_quota:
            return
        for rel in list(self._pending_qimages):   # 插入序 = 最旧在前
            if rel == self._current_file:
                continue
            slot = self._pending_qimages.get(rel)
            if slot is None or slot.get("bg") is None:
                continue
            total -= int(slot["bg"].sizeInBytes())
            del slot["bg"]
            if not slot:
                self._pending_qimages.pop(rel, None)
            if total <= self._bg_qimage_quota:
                return

    def _pin_current_bg(self) -> None:
        """钉住当前文档的 bg 条目（LRU 淘汰时跳过），解钉旧页。

        bg id 未解析（None）时跳过：LRU 条目尚不存在，等阶段 B
        完成后由 _on_preload_done 补钉；钉住集与条目存在性解耦。
        """
        if self._pinned_bg_key is not None:
            self._layer_cache.unpin(*self._pinned_bg_key)
            self._pinned_bg_key = None
        doc = self.current_doc
        if doc is None:
            return
        bg_id = doc.resolved_bg_layer_id()
        if bg_id:
            key = (str(doc.path), bg_id)
            self._layer_cache.pin(*key)
            self._pinned_bg_key = key

    def _current_keep_set(self) -> set:
        """驱逐保留窗口：当前页 + 邻域（范围按平台，见 preload_window_offsets）。

        桌面 = 当前 + 后 3 + 前 1 + 前 2 松弛（共 6 页）；Android = 前 1 + 当前
        + 后 1（共 3 页）。
        """
        if self.task is None or not self._current_file:
            return set()
        return self._window_keep_set(self._current_file)

    def _evict_outside_window(self, keep: set) -> None:
        """驱逐窗口外的完整文档对象（psd-tools 结构 ≈ 文件大小，
        是唯一随书本页数线性增长的内存池）。

        保护规则：当前页 / 在途打开目标 / io 锁被预加载线程占用
        的文档跳过本轮（下次调度再试）；监制进度数据（task 模型
        与图层 id/名称缓存）与文档对象解耦，驱逐不影响进度。
        """
        for rel in list(self._docs):
            if rel in keep or rel == self._current_file:
                self._evict_pending.discard(rel)
                continue
            pending = self._pending_open
            if pending is not None and pending[0] == rel:
                continue
            doc = self._docs.get(rel)
            if doc is None:
                self._evict_pending.discard(rel)
                continue
            # 非阻塞拿锁：后台正在提取则跳过，避免 UI 卡在锁上
            if not doc._io_lock.acquire(blocking=False):
                # 锁被预加载线程占着 → 记入待驱逐集，等该线程交还后再收。
                # 只跳过不重试会让窗口外文档永久驻留（实测切到 p08 后
                # p01 因预加载在途而留在 _docs），内存窗口就形同虚设。
                self._evict_pending.add(rel)
                continue
            try:
                self._docs.pop(rel, None)
                self._pending_qimages.pop(rel, None)
                self._evict_pending.discard(rel)
                doc.release()   # 释放 merged/bg + 按路径逐出共享 LRU 条目
            finally:
                doc._io_lock.release()

    def _drain_pending_evictions(self) -> None:
        """重试因 io 锁被占而推迟的驱逐（无待驱逐项时零开销）。

        预加载线程是一次性的短任务，完成一个目标后锁即交还；每当预加载
        结果回到主线程就重试一次，窗口外文档随即被回收，不会永久驻留。

        单靠回调还不够：文档的 io 锁覆盖整段像素提取，若预加载线程手上
        还压着整队邻域任务，排队期间窗口外文档会一直挂着。所以待驱逐集
        非空时再挂一个短定时器持续重试——有界（最多 _EVICT_RETRY_MAX 次），
        锁一空出来就收，避免"只在队列全部跑完才回收"。
        """
        if not self._evict_pending:
            self._evict_retries = 0
            return
        self._evict_outside_window(self._current_keep_set())
        if self._evict_pending and self._evict_retries < _EVICT_RETRY_MAX:
            self._evict_retries += 1
            self._evict_timer.start(_EVICT_RETRY_MS)
        else:
            self._evict_retries = 0

    def _update_preload_label(self) -> None:
        """两阶段独立显示：图像预加载（A）在左、图层预热（B）在右。"""
        if self.task is None or not self._preload_scheduled:
            self.preload_label.setText("")
            self.warmup_label.setText("")
            return
        if self._preload_targets:
            self.preload_label.setText(f"图像预加载中…（{len(self._preload_targets)}）")
            self.preload_label.setStyleSheet("color: #f5a623;")
        else:
            self.preload_label.setText("图像预加载完成")
            self.preload_label.setStyleSheet("color: #4caf50;")
        if self._extra_targets:
            self.warmup_label.setText(f"图层预热中…（{len(self._extra_targets)}）")
            self.warmup_label.setStyleSheet("color: #f5a623;")
        else:
            self.warmup_label.setText("图层预热完成")
            self.warmup_label.setStyleSheet("color: #4caf50;")

    def _select_layer_internal(self, index: int) -> None:
        """切换图层统一行为（需求 §12）：停对比 → Original → 切换 → 定位 → 缩放。"""
        self._compare.interrupt()
        doc = self.current_doc
        if doc is None or not (0 <= index < len(doc.layers)):
            self._current_index = -1
            self._refresh_viewer_outline()   # 清除上一个图层残留的虚线框
            self._refresh_nav_pad()
            return
        self._current_index = index
        info = doc.layers[index]
        if self.task is not None:
            self.task.current_file = self._current_file
            self.task.current_layer = info.id
        self._refresh_nav_pad()          # 图层位置变了 → 刷新方向键置灰

        self.viewer.set_issues(self._viewer_issues())
        self._refresh_viewer_outline()
        # 自动定位 + 自动缩放（需求 §17、§20）
        self.viewer.recenter_on_layer(info, self.settings.layer_display_ratio)
        self._refresh_issue_panel()
        self.viewer.setFocus()

    def prev_psd(self) -> None:
        if self.task is None:
            return
        idx = navigator.prev_file_index(self.task, self._current_file)
        if idx is not None:
            self._switch_file(self.task.files[idx].relative_path)

    def next_psd(self) -> None:
        if self.task is None:
            return
        idx = navigator.next_file_index(self.task, self._current_file)
        if idx is not None:
            self._switch_file(self.task.files[idx].relative_path)

    def _request_layer_switch(self, index: int) -> None:
        """图层切换入口：视觉边界已预热走快路径；
        否则交后台线程预热（进度框，UI 不冻结）。"""
        doc = self.current_doc
        if doc is None or not (0 <= index < len(doc.layers)):
            return
        info = doc.layers[index]
        if info.has_visual_bounds():
            self._select_layer_internal(index)
            self._refresh_layer_selection()
            self._mark_dirty()
            self.viewer.setFocus()
            return
        # 未预热：后台提取视觉边界（merged 已缓存，仅图层像素）
        self._pending_open = (self._current_file, "layer", index)
        self._preload.submit_open(self._current_file, info.id)
        self._show_open_progress(f"{self._current_file} / {info.name}")

    def _on_layer_activated(self, index: int) -> None:
        if self._updating_panels:
            return
        self._request_layer_switch(index)

    def prev_layer(self) -> None:
        if self.current_doc is None or self._current_index <= 0:
            return
        self._request_layer_switch(self._current_index - 1)

    def next_layer(self) -> None:
        if self.current_doc is None or self._current_index >= len(self.current_doc.layers) - 1:
            return
        self._request_layer_switch(self._current_index + 1)

    def recenter_current_layer(self) -> None:
        """显式重新定位（需求 §27）。"""
        doc = self.current_doc
        if doc is None or not (0 <= self._current_index < len(doc.layers)):
            return
        self.viewer.recenter_on_layer(
            doc.layers[self._current_index], self.settings.layer_display_ratio
        )

    # ================================================================= 监制操作（需求 §14～§16）

    def mark_pass(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        self._compare.interrupt()
        info = self.current_doc.layers[self._current_index]
        status = self.task.status_of(self._current_file, info.id)
        if status == UNREVIEWED:
            self.task.set_status(self._current_file, info.id, PASSED)
            log.info("图层通过：%s/%s", self._current_file, info.id)
        self._refresh_all_panels()
        self._mark_dirty()
        self._advance_to_next_unreviewed()

    def mark_fail(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        self._compare.interrupt()
        info = self.current_doc.layers[self._current_index]
        self.task.set_status(self._current_file, info.id, FAILED)
        self._completion_announced = False   # 内容还会变（拖框批注）→ 允许再次触发
        self.issue_panel.set_hint(
            "已标记 ✗ 未通过 — 可拖框添加问题或输入自定义批注；"
            f"{self._display_key(self.settings.binding('pass_layer') or 'Return')} 跳到下一个未监制图层。"
        )
        self._refresh_all_panels()
        self._mark_dirty()
        self.viewer.setFocus()
        self._hint_if_all_reviewed()
        log.info("图层未通过：%s/%s", self._current_file, info.id)

    def _on_status_change_requested(self, status: str) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        info = self.current_doc.layers[self._current_index]
        self.task.set_status(self._current_file, info.id, status)
        self.issue_panel.set_hint("")
        self._refresh_all_panels()
        self._refresh_viewer_issues()
        self._mark_dirty()
        self.viewer.setFocus()
        if status == FAILED:
            # 未通过通常紧接着拖框/写批注：只提示，不弹窗、不提前生成
            self._completion_announced = False
            self._hint_if_all_reviewed()
        else:
            self._on_all_reviewed()

    def _advance_to_next_unreviewed(self) -> None:
        """Enter 后的自动跳转（需求 §15、§44）。"""
        if self.task is None:
            return
        doc = self.current_doc
        if doc is None:
            return
        layer_ids = self._layer_ids_by_file.get(self._current_file, [])
        idx = navigator.next_unreviewed_in_list(
            layer_ids, self.task.reviews, self._current_file, self._current_index
        )
        if idx is not None:
            # 统一走图层切换通道（快路径/异步预热）
            self._request_layer_switch(idx)
            return

        # 当前 PSD 完成 → 寻找下一个仍有未监制图层的 PSD（需求 §66）
        order = [r.relative_path for r in self.task.files]
        try:
            cur = order.index(self._current_file)
        except ValueError:
            cur = -1
        for offset in range(1, len(order) + 1):
            rel = order[(cur + offset) % len(order)]
            ids = self._layer_ids_by_file.get(rel, [])
            if any(
                self.task.status_of(rel, lid) == UNREVIEWED for lid in ids
            ):
                self._switch_file(rel)
                return

        self._on_all_reviewed()

    def _all_reviewed(self) -> bool:
        """任务是否所有可监制图层都已检查（需求 §44）。"""
        if self.task is None:
            return False
        counts = self.task.count_all(self._layer_ids_by_file)
        return counts["total"] > 0 and counts["unreviewed"] == 0

    def _on_all_reviewed(self) -> None:
        """全部图层已检查（需求 §44、§46）。

        - 同一次「完成」只提示一次、只自动生成一次返修单：连按 Enter 不会
          反复弹窗、反复生成；
        - 之后若又改动了内容（补问题、改状态等），标志复位，下次完成时会
          重新提示并按设置重新生成；
        - 是否自动生成返修单由设置「完成监制后自动生成返修单」控制（默认开）。
        """
        if not self._all_reviewed():
            self._completion_announced = False      # 有图层回到未监制 → 复位
            return
        if self._completion_announced:
            return
        self._completion_announced = True
        self.issue_panel.set_hint("")
        QMessageBox.information(
            self,
            "监制完成",
            "所有图层已经检查。\n\n任务：%s" % (self.task.task_name if self.task else ""),
        )
        if self.settings.generate_pdf_on_complete:
            self._generate_report(interactive=False)

    def _hint_if_all_reviewed(self) -> None:
        """刚标记未通过时通常还要拖框批注：只给状态栏提示，不弹窗打断。"""
        if not self._all_reviewed():
            return
        tail = "按 Enter 结束并生成返修单" if self.settings.generate_pdf_on_complete else "按 Enter 结束"
        self.statusBar().showMessage(f"所有图层已检查 — {tail}", 5000)

    # ================================================================= 自动对比（需求 §21～§26）

    def toggle_compare(self) -> None:
        if self.current_doc is None:
            return
        # 预取 bg 图像，避免闪切中途卡顿（需求 §59：不重新读取）
        if self.current_doc.bg_image() is None:
            self.statusBar().showMessage("未找到可用背景图层，无法对比", 3000)
            return
        if self.settings.compare_mode == "manual":
            # 手动挡：按一下切一次，无运行/停止状态
            self._compare.swap_once()
            return
        if self._compare.is_running:
            self._compare.stop()
            return
        self._compare.start()

    def _update_compare_action_text(self) -> None:
        key = self._display_key(self.settings.binding("toggle_compare") or "Space")
        if self.settings.compare_mode == "manual":
            self.action_compare.setText(f"对比切换 ({key})")
        elif self._compare.is_running:
            self.action_compare.setText(f"停止自动对比 ({key})")
        else:
            self.action_compare.setText(f"自动对比 ({key})")

    def _apply_compare_settings(self) -> None:
        """把设置中的对比模式/速度应用到控制器与工具栏按钮。"""
        self._compare.set_interval_ms(
            hz_to_interval_ms(self.settings.compare_speed_hz)
        )
        self._applying_compare_ui = True
        try:
            if self.settings.compare_mode == "manual":
                self._compare.interrupt()   # 切到手动挡：停止自动切换并回原图
            self.action_compare.setChecked(False)
        finally:
            self._applying_compare_ui = False
        self._update_compare_action_text()

    def _on_compare_display_changed(self, state: str) -> None:
        self.viewer.set_source(SOURCE_BG if state == BG_ONLY else SOURCE_MERGED)

    def _on_compare_running_changed(self, running: bool) -> None:
        self.action_compare.setChecked(running)
        self._update_compare_action_text()
        if not running:
            self.viewer.set_source(SOURCE_MERGED)

    def _on_compare_action_toggled(self, checked: bool) -> None:
        if self._applying_compare_ui:
            return
        if self.settings.compare_mode == "manual":
            # 手动挡：按钮不保持勾选状态，每次点击切换一次
            self._applying_compare_ui = True
            try:
                self.action_compare.setChecked(False)
            finally:
                self._applying_compare_ui = False
            self.toggle_compare()
            return
        if checked != self._compare.is_running:
            self.toggle_compare()

    # ================================================================= 问题（需求 §31～§40）

    def _on_issue_key(self, type_name: str) -> None:
        """方式 A：快捷键选类型 → 拖框（需求 §35、§37）。"""
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        self._compare.interrupt()   # 需求 §40：停止对比后创建
        self.viewer.set_pending_type(type_name)
        self.issue_panel.set_hint(self._annotation_hint(f"请在画布上拖拽红框：{type_name}"))
        self.viewer.setFocus()

    def _on_issue_drawn(
        self, issue_type: str, x: float, y: float, w: float, h: float, auto: bool = False
    ) -> None:
        dialog = IssueDialog(
            self.settings.issue_type_names(),
            self,
            default_type=issue_type,
            rect=(x, y, w, h),
            title=f"添加问题：{issue_type}",
            type_keys=self.settings.issue_key_map(),
            auto_pick=auto,
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(
                *dialog.result_values(), rect=(x, y, w, h)
            )
        self._after_issue_draw(rearm_type=issue_type)

    def _on_rect_drawn(
        self, x: float, y: float, w: float, h: float, auto: bool = False
    ) -> None:
        """方式 B：先拖框 → 选择类型（需求 §37）。

        auto=True 表示由「自动框选」触发：对话框自动展开下拉栏、
        问题类型快捷键直接选定并跳到批注框（手动拖框保持原逻辑）。
        """
        self._compare.interrupt()
        dialog = IssueDialog(
            self.settings.issue_type_names(),
            self,
            rect=(x, y, w, h),
            type_keys=self.settings.issue_key_map(),
            auto_pick=auto,
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(*dialog.result_values(), rect=(x, y, w, h))
        self._after_issue_draw(rearm_type=None)

    def auto_box_current_layer(self) -> None:
        """自动框选当前图层：按视觉边界自动生成红框（拖框模式内可用）。

        矩形 = 蓝色虚线框（图层视觉边界）上下左右各外扩 5 像素，对称外扩，
        因此中心与虚线框中心完全一致。随后走与手动拖框完全相同的流程：
        已选问题类型 → 直接建该类型；否则弹对话框选类型。
        """
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        if not self.viewer.any_issue_mode():
            self.statusBar().showMessage(
                f"自动框选：请先进入拖框模式（"
                f"{self._display_key(self.settings.binding('redraw_mode') or 'R')} 红框模式，"
                f"或按问题类型快捷键）",
                5000,
            )
            self.issue_panel.set_hint(
                self._annotation_hint(
                    "自动框选需先进入拖框模式：按 "
                    f"{self._display_key(self.settings.binding('redraw_mode') or 'R')}"
                    " 或问题类型快捷键"
                )
            )
            return
        info = self.current_doc.layers[self._current_index]
        rect = auto_box_rect(info)
        if rect is None:
            self.statusBar().showMessage(
                f"自动框选：图层「{info.name}」没有可框选的内容", 5000
            )
            return
        self._compare.interrupt()
        x, y, w, h = rect
        pending = self.viewer.pending_type
        log.info("自动框选：%s %s → %s", self._current_file, info.id, rect)
        if pending is not None:
            self._on_issue_drawn(pending, x, y, w, h, auto=True)
        else:
            self._on_rect_drawn(x, y, w, h, auto=True)

    def _annotation_hint(self, prefix: str) -> str:
        """拖框提示：说明标完是否自动退出 + 退出键。"""
        tail = (
            "连续标注中，可接着标下一个"
            if self.settings.continuous_annotation
            else "标完一个自动退出"
        )
        cancel = self._display_key(self.settings.binding("cancel_operation") or "Esc")
        return f"{prefix}（{tail}；{cancel} 取消）"

    def _after_issue_draw(self, rearm_type: Optional[str]) -> None:
        """一次拖框标注收尾（默认标完即退出，见「连续标注」开关）。

        - 连续标注关（默认）：清掉拖框/待选类型模式，回到普通浏览状态；
        - 连续标注开：保持拖框模式；类型快捷键路径则重新武装同一类型，
          便于连续标注同类问题。
        """
        self.issue_panel.set_hint("")
        if not self.settings.continuous_annotation:
            self.viewer.set_pending_type(None)
            self.viewer.set_redraw_mode(False)
            return
        if rearm_type is not None:
            self.viewer.set_pending_type(rearm_type)
            self.issue_panel.set_hint(
                self._annotation_hint(f"请在画布上拖拽红框：{rearm_type}")
            )
        else:
            self.viewer.set_redraw_mode(True)
            self.issue_panel.set_hint(self._annotation_hint("拖框模式：在画布上拖拽红框"))

    def _on_continuous_toggled(self, enabled: bool) -> None:
        """「连续标注」开关：立即生效并持久化。"""
        self.settings.continuous_annotation = bool(enabled)
        self._save_settings()
        self.statusBar().showMessage(
            "连续标注：开启（标完一个问题仍保持拖框模式）"
            if enabled
            else "连续标注：关闭（标完一个问题自动退出拖框模式）",
            4000,
        )
        # 提示文案随开关刷新（正在拖框时立即反映新语义）
        if self.viewer.any_issue_mode():
            if self.viewer.pending_type is not None:
                prefix = f"请在画布上拖拽红框：{self.viewer.pending_type}"
            else:
                prefix = "拖框模式：在画布上拖拽红框"
            self.issue_panel.set_hint(self._annotation_hint(prefix))

    def _on_custom_comment(self) -> None:
        """自定义批注（需求 §36），无红框。"""
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        self._compare.interrupt()
        dialog = IssueDialog(
            self.settings.issue_type_names(), self, default_type="其他",
            title="自定义批注",
            type_keys=self.settings.issue_key_map(),
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(*dialog.result_values(), rect=(0, 0, 0, 0))

    def _refresh_viewer_issues(self) -> None:
        """问题变化后刷新 Viewer 红框 Overlay（需求 §39）。

        显示范围跟随设置（见 _viewer_issues）：默认显示当前页全部问题。
        """
        self.viewer.set_issues(self._viewer_issues())

    def _refresh_viewer_outline(self) -> None:
        """刷新当前图层视觉边界虚线框（蓝框，设置中可关闭）。

        世界坐标 (left, top, right, bottom) 由 camera.centering 统一换算；
        关闭设置、无文档、无当前图层或图层无有效像素时一律传 None（不绘制）。
        """
        doc = self.current_doc
        info = (
            doc.layers[self._current_index]
            if doc is not None and 0 <= self._current_index < len(doc.layers)
            else None
        )
        outline = None
        if info is not None and self.settings.show_layer_outline:
            outline = layer_visual_bounds(info)
        self.viewer.set_layer_outline(outline)

    def _viewer_issues(self) -> List[Issue]:
        """Veiwer 当前应显示的问题集合。

        - issue_scope == "page"（默认）：当前 PSD 的全部问题（跨图层），
          翻到哪页就看全哪页的标注，便于整页通盘检查；
        - issue_scope == "layer"：仅当前图层的问题（旧版本行为）。
        """
        if self.task is None:
            return []
        if self.settings.issue_scope == "layer":
            if self.current_doc is None or self._current_index < 0:
                return []
            info = self.current_doc.layers[self._current_index]
            return self.task.issues_for(self._current_file, info.id)
        return self.task.issues_for_file(self._current_file)

    def _commit_new_issue(self, issue_type: str, comment: str, rect) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        info = self.current_doc.layers[self._current_index]
        self.task.add_issue(
            self._current_file, info.id, info.name, issue_type, comment, rect
        )
        self.task.set_status(self._current_file, info.id, FAILED)
        self._refresh_all_panels()
        self._refresh_viewer_issues()
        self._mark_dirty()
        self._completion_announced = False   # 内容已变：允许再次触发自动生成
        self.viewer.setFocus()

    def _on_edit_issue(self, issue_id: str) -> None:
        if self.task is None:
            return
        issue = next((i for i in self.task.issues if i.issue_id == issue_id), None)
        if issue is None:
            return
        dialog = IssueDialog(
            self.settings.issue_type_names(), self, issue=issue, title="编辑问题",
            type_keys=self.settings.issue_key_map(),
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            issue_type, comment = dialog.result_values()
            issue.type = issue_type
            issue.comment = comment
            self._refresh_all_panels()
            self._refresh_viewer_issues()
            self._mark_dirty()
            self._completion_announced = False

    def _on_delete_issue(self, issue_id: str) -> None:
        if self.task is None:
            return
        self.task.remove_issue(issue_id)
        self._refresh_all_panels()
        self._refresh_viewer_issues()
        self._mark_dirty()
        self._completion_announced = False

    # ================================================================= 问题编号检查/重排

    def check_issue_numbers(self) -> None:
        """按 PSD / 图层顺序检查并重排问题编号（显式触发，不在标记时维护）。

        编号在新增时取「最大值 + 1」：删除问题、或回头给前面的 PSD 补问题
        会造成空号/跳号，红框徽标与问题面板的 #编号看起来就不对。这里按
        文档顺序重排为 1..N，扫描过程在后台线程执行（进度框，界面不卡死），
        结果回主线程一次性写回并立即保存。
        """
        if self.task is None:
            QMessageBox.information(self, "检查问题编号", "请先打开 PSD 或文件夹。")
            return
        if self._numbering_worker is not None:
            # 上一轮仍在进行（或结果尚未处理）→ 忽略重复请求
            self.statusBar().showMessage("正在检查问题编号…", 3000)
            return
        if not self.task.issues:
            self.statusBar().showMessage("当前任务没有问题，无需检查编号", 3000)
            return

        self._autosave_timer.stop()   # 避免与重排后的保存交错
        worker = NumberingWorker(self.task, dict(self._layer_ids_by_file))
        dialog = QProgressDialog("准备检查…", "取消", 0, 1, self)
        dialog.setWindowTitle("检查问题编号")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(460)
        dialog.setAutoClose(True)
        # 与「打开任务」一致：autoReset 会在值到达 100% 时触发 reset 并连带
        # canceled 信号，由 _close_numbering_ui 统一关闭。
        dialog.setAutoReset(False)

        worker.progress.connect(self._on_numbering_progress)
        worker.succeeded.connect(self._on_numbering_finished)
        worker.failed.connect(self._on_numbering_failed)
        dialog.canceled.connect(worker.request_cancel)

        self._numbering_worker = worker
        self._numbering_dialog = dialog
        self.action_renumber.setEnabled(False)
        dialog.show()
        worker.start()

    def _on_numbering_progress(self, done: int, total: int, message: str) -> None:
        dialog = self._numbering_dialog
        if dialog is None:
            return
        dialog.setMaximum(max(total, 1))
        dialog.setValue(min(done, total))
        # 模态进度框的 setValue 内部会 pump 事件循环，可能重入导致对话框
        # 已被关闭（self._numbering_dialog 置 None），需复查后再更新文案。
        if self._numbering_dialog is dialog:
            dialog.setLabelText(message)

    def _close_numbering_ui(self) -> None:
        self.action_renumber.setEnabled(self.task is not None)
        if self._numbering_dialog is not None:
            # 先断开 canceled：close() 可能触发该信号导致重入
            try:
                self._numbering_dialog.canceled.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._numbering_dialog.close()
            self._numbering_dialog.deleteLater()
            self._numbering_dialog = None
        if self._numbering_worker is not None:
            worker = self._numbering_worker
            self._numbering_worker = None
            worker.deleteLater()

    def _on_numbering_failed(self, message: str) -> None:
        self._close_numbering_ui()
        QMessageBox.critical(
            self,
            "检查问题编号",
            f"检查问题编号失败：\n{message}\n\n详情见 logs/mangaproof.log。",
        )

    def _on_numbering_finished(self, result) -> None:
        self._close_numbering_ui()
        if result.kind == NUMBERING_CANCELLED:
            self.statusBar().showMessage("已取消检查问题编号", 3000)
            return
        plan = result.plan
        apply_numbering(self.task, plan)
        self._refresh_all_panels()
        self._refresh_viewer_issues()
        self.save_task()      # 编号已变更：立即落盘（不等防抖）

        if plan.changed:
            lines = [
                "问题编号已按 PSD / 图层顺序重排：",
                f"　问题总数：{plan.total}",
                f"　修正编号：{plan.fixed}",
            ]
            if plan.orphans:
                lines.append(
                    f"　归属不明：{plan.orphans}（所属 PSD / 图层已不存在，已排在最后）"
                )
            QMessageBox.information(self, "检查问题编号", "\n".join(lines))
        else:
            self.statusBar().showMessage(
                f"问题编号检查完成：{plan.total} 个编号已连续，无需调整", 5000
            )
        log.info(
            "问题编号检查完成：共 %d 个，修正 %d 个，归属不明 %d 个",
            plan.total, plan.fixed, plan.orphans,
        )

    def toggle_redraw_mode(self) -> None:
        if self.task is None or self.current_doc is None:
            return
        self._compare.interrupt()
        self.viewer.set_redraw_mode(not self.viewer.redraw_mode)
        if self.viewer.redraw_mode:
            self.issue_panel.set_hint(
                self._annotation_hint("拖框模式：在画布上拖拽红框")
            )

    def _on_redraw_mode_toggled(self, checked: bool) -> None:
        if checked != self.viewer.redraw_mode:
            self.viewer.set_redraw_mode(checked)

    def _on_add_issue_requested(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        self._compare.interrupt()
        self.viewer.set_redraw_mode(True)
        self.issue_panel.set_hint(self._annotation_hint("拖框模式：在画布上拖拽红框"))
        self.viewer.setFocus()

    def _on_pending_changed(self) -> None:
        self.action_redraw.setChecked(self.viewer.redraw_mode)
        # 自动框选只在拖框模式下可用（进入/退出模式即时反映在按钮上）
        self.issue_panel.set_auto_box_enabled(self.viewer.any_issue_mode())
        if not self.viewer.any_issue_mode():
            self.issue_panel.set_hint("")

    def cancel_operation(self) -> None:
        """Esc：取消/退出当前批注操作（需求 §30）；对比中则停止并恢复原图。"""
        if self.viewer.any_issue_mode():
            self.viewer.cancel_pending()
            self.issue_panel.set_hint("")
            return
        self._compare.interrupt()

    # ================================================================= 保存（需求 §8～§9）

    def progress_file_path(self) -> Optional[Path]:
        if self.task is None or self._base_dir is None:
            return None
        if self.task.task_type == "single":
            if self.task.files:
                return progress_path_for_single(self._base_dir / self.task.files[0].relative_path)
            return None
        return progress_path_for_folder(self._base_dir)

    def _mark_dirty(self, save_immediately: bool = False) -> None:
        self._update_save_label(initial=False)
        if save_immediately:
            self.save_task()
        else:
            self._autosave_timer.start()

    def save_task(self) -> bool:
        """保存任务，返回是否成功（无任务 / 无进度文件路径时视为成功）。

        自动保存、Ctrl+S、生成返修单都不看返回值；只有「关闭当前任务」用它
        判断要不要中止关闭——保存失败（磁盘满 / 只读 / 权限）时宁可留在原地，
        也不静默丢弃监制进度。
        """
        if self.task is None:
            return True
        path = self.progress_file_path()
        if path is None:
            return True
        try:
            persistence.save_task(self.task, path)
        except OSError as exc:
            log.exception("保存任务失败")
            self._save_error = str(exc)
            self._update_save_label(initial=False, error=str(exc))
            return False
        self._save_error = ""
        self._update_save_label(initial=False, saved=True)
        log.info("任务已保存：%s", path)
        return True

    def _update_save_label(self, initial: bool = False, saved: bool = False, error: str = ""):
        if initial:
            self._save_error = ""
            self.save_label.setText("未打开任务")
        elif error:
            self.save_label.setText(f"保存失败：{error}")
            self.save_label.setStyleSheet("color: #e53935;")
        elif saved:
            from datetime import datetime
            self.save_label.setText("已保存 " + datetime.now().strftime("%H:%M:%S"))
            self.save_label.setStyleSheet("color: #4caf50;")
        else:
            self.save_label.setText("未保存")
            self.save_label.setStyleSheet("color: #f5a623;")

    # ================================================================= 返修单（需求 §45～§54）

    def generate_report_dialog(self) -> None:
        if self.task is None or self._base_dir is None:
            QMessageBox.information(self, "生成返修单", "请先打开 PSD 或文件夹。")
            return
        self.save_task()
        self._generate_report(interactive=True)

    def _generate_report(self, interactive: bool) -> None:
        if self.task is None or self._base_dir is None:
            return
        default_name = default_report_name(
            self.task.task_type,
            self._base_dir,
            psd_file_name=(
                self.task.files[0].file_name if self.task.files else ""
            ),
        )

        name = self.settings.report_name or default_name
        if interactive:
            incomplete = self.task.count_all(self._layer_ids_by_file)["unreviewed"] > 0
            dialog = ReportDialog(
                name,
                self.task.task_name,
                incomplete,
                self,
                image_format=self.settings.report_image_format,
                jpeg_quality=self.settings.report_jpeg_quality,
                hide_clean_files=self.settings.report_hide_clean_files,
            )
            if dialog.exec() != ReportDialog.DialogCode.Accepted:
                return
            name = dialog.report_name()
            # 生成选项（图片压缩 / 总览表隐藏干净页）记回设置，下次沿用
            if (
                dialog.image_format() != self.settings.report_image_format
                or dialog.jpeg_quality() != self.settings.report_jpeg_quality
                or dialog.hide_clean_files() != self.settings.report_hide_clean_files
            ):
                self.settings.report_image_format = dialog.image_format()
                self.settings.report_jpeg_quality = dialog.jpeg_quality()
                self.settings.report_hide_clean_files = dialog.hide_clean_files()
                self._save_settings()

        out_path = resolve_report_path(self._base_dir, name, default_name)
        # 确保全部任务文件的图层 id 都已扫描（每个 PSD 只解析一次，需求 §59）
        for record in self.task.files:
            if record.relative_path not in self._layer_ids_by_file:
                doc = self._ensure_doc(record.relative_path)
                if doc is not None:
                    self._layer_ids_by_file[record.relative_path] = [
                        info.id for info in doc.layers
                    ]
                    self._layer_names_by_file[record.relative_path] = [
                        info.name for info in doc.layers
                    ]

        self._start_report(out_path)

    # -- 后台生成（进度框 + 防 GUI 卡死） ----------------------------------

    def _start_report(self, out_path: Path) -> None:
        """把返修单生成交给后台线程，UI 显示页面级进度（可取消）。"""
        if self._report_worker is not None:
            # 上一轮生成仍在进行（或结果尚未处理）→ 忽略重复请求，
            # 避免同时开两个 worker / 关掉新进度框
            self.statusBar().showMessage("正在生成返修单…", 3000)
            return

        worker = ReportWorker(
            self.task,
            dict(self._layer_ids_by_file),
            out_path,
            base_dir=self._base_dir,
            docs=self._docs,           # 已打开文档快照（worker 内部再复制一份）
            layer_cache=self._layer_cache,
            image_format=self.settings.report_image_format,
            image_quality=self.settings.report_jpeg_quality,
            hide_clean_files=self.settings.report_hide_clean_files,
        )
        dialog = QProgressDialog("准备返修单…", "取消", 0, 1, self)
        dialog.setWindowTitle("生成返修单")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(460)
        dialog.setAutoClose(True)
        # 与「打开任务」一致：autoReset 会在值到达 100% 时触发 reset 并连带
        # canceled 信号，由 _close_report_ui 统一关闭，无需自动重置。
        dialog.setAutoReset(False)

        worker.progress.connect(self._on_report_progress)
        worker.succeeded.connect(self._on_report_finished)
        worker.failed.connect(self._on_report_failed)
        dialog.canceled.connect(worker.request_cancel)

        self._report_worker = worker
        self._report_dialog = dialog
        self.action_report.setEnabled(False)
        dialog.show()
        worker.start()

    def _on_report_progress(self, done: int, total: int, message: str) -> None:
        dialog = self._report_dialog
        if dialog is None:
            return
        dialog.setMaximum(max(total, 1))
        dialog.setValue(min(done, total))
        # 模态进度框的 setValue 内部会 pump 事件循环，可能重入导致对话框
        # 已被关闭（self._report_dialog 置 None），需复查后再更新文案。
        if self._report_dialog is dialog:
            dialog.setLabelText(message)

    def _close_report_ui(self) -> None:
        self.action_report.setEnabled(self.task is not None)
        if self._report_dialog is not None:
            # 先断开 canceled：close() 可能触发该信号导致重入
            try:
                self._report_dialog.canceled.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._report_dialog.close()
            self._report_dialog.deleteLater()
            self._report_dialog = None
        if self._report_worker is not None:
            worker = self._report_worker
            self._report_worker = None
            worker.deleteLater()

    def _on_report_failed(self, message: str) -> None:
        self._close_report_ui()
        QMessageBox.critical(
            self,
            "生成返修单",
            f"生成返修单失败：\n{message}\n\n详情见 logs/mangaproof.log。",
        )

    def _on_report_finished(self, result) -> None:
        self._close_report_ui()
        if result.kind == REPORT_CANCELLED:
            self.statusBar().showMessage("已取消生成返修单", 3000)
            return
        # 报告按需重开的文档对象及时回收，避免驻留
        self._evict_outside_window(self._current_keep_set())
        QMessageBox.information(self, "生成返修单", f"已生成：\n{result.path}")
        log.info("返修单已生成：%s", result.path)

    # ================================================================= 首次使用引导

    def maybe_prompt_first_run_settings(self) -> None:
        """首次使用：把设置页面直接打开，让用户自己过一遍（不代替他做决定）。

        只在「启动时既没有 settings.json 也没有 recent.json」时触发——从没配置过、
        也从没打开过任务（老用户升级至少会有其一，不会被打扰）。只展示设置项，
        不预设、不推荐、不写文件；用户点「取消」就什么都不做，默认值照常生效。
        """
        if not self.settings_manager.is_first_use:
            return
        log.info("首次使用：打开设置页面，由用户自行决定是否调整")
        self.open_settings_dialog(intro=FIRST_RUN_INTRO)

    def _dismiss_settings_banner(self) -> None:
        """关闭首次使用提醒条（仅本次会话；不写任何文件）。"""
        self._settings_banner_dismissed = True
        self._refresh_settings_banner()

    def _refresh_settings_banner(self) -> None:
        """提醒条只在「首次使用且还没确认过设置」时出现。

        一旦 settings.json 存在（用户在设置里点了确定，或程序正常退出时落盘），
        提醒条自动收起，不再打扰。
        """
        show = (
            self.settings_manager.was_missing
            and not self.settings_manager.has_settings_file
            and not self._settings_banner_dismissed
        )
        self.settings_banner.setVisible(show)

    def _save_settings(self) -> None:
        """保存设置的统一入口（顺手刷新首次使用提醒条的状态）。"""
        self.settings_manager.save()
        self._refresh_settings_banner()

    # ================================================================= 设置

    def open_settings_dialog(self, intro: str = "") -> None:
        dialog = SettingsDialog(self.settings, self, intro=intro)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            dialog.apply_to(self.settings)
            self._save_settings()
            self._rebuild_shortcuts()
            self._apply_compare_settings()
            self._apply_memory_policy()   # 内存策略档位热应用
            self.viewer.set_wheel_mode(self.settings.wheel_mode)
            self._refresh_viewer_issues()  # 红框显示范围（整页/仅当前图层）热应用
            self._refresh_viewer_outline()  # 蓝色虚线边界框开关热应用
            idx = self.ratio_combo.findData(self.settings.layer_display_ratio)
            self.ratio_combo.setCurrentIndex(max(0, idx))
            self.recenter_current_layer()
            # 控制台开关即时生效（打包产物下隐藏/恢复控制台窗口）。
            # AllocConsole 可能阻塞数百毫秒，放后台线程避免 UI 卡顿。
            import threading

            from mangaproof.console import apply_console_visibility

            threading.Thread(
                target=apply_console_visibility,
                args=(self.settings,),
                daemon=True,
            ).start()
            self._notify_ui_scale_restart_needed()

    def _notify_ui_scale_restart_needed(self) -> None:
        """界面缩放（Android 专有）改动后提示需重启应用。

        Qt 只在启动时读一次 QT_SCALE_FACTOR（见 config/settings.apply_startup_ui_scale），
        所以本次会话改不了，必须重启才完全生效——这里如实告知，不做"假装生效"。
        桌面端恒为 1.0，不会进入提示分支。
        """
        if not android_ui_scaling():
            return
        if abs(self.settings.ui_scale - effective_ui_scale()) < 1e-9:
            return
        QMessageBox.information(
            self,
            "界面缩放",
            "界面缩放已保存。\n\n"
            f"当前显示仍为 {int(round(effective_ui_scale() * 100))}%，"
            f"新设置的 {int(round(self.settings.ui_scale * 100))}% 需要"
            "**重启应用**后完全生效。",
        )

    def _on_ratio_changed(self, index: int) -> None:
        ratio = float(self.ratio_combo.itemData(index))
        if abs(ratio - self.settings.layer_display_ratio) < 1e-6:
            return
        self.settings.layer_display_ratio = ratio
        self._save_settings()
        self.recenter_current_layer()

    # ================================================================= 面板刷新

    def _refresh_all_panels(self) -> None:
        self._updating_panels = True
        try:
            self._refresh_file_panel()
            self._refresh_layer_panel()
            self._refresh_stats_panel()
            self._refresh_issue_panel()
            self._refresh_layer_selection()
            self._refresh_enabled_state()
            self._refresh_title()
        finally:
            self._updating_panels = False

    def _refresh_file_panel(self) -> None:
        if self.task is None:
            return
        statuses = {}
        for record in self.task.files:
            rel = record.relative_path
            ids = self._layer_ids_by_file.get(rel, [])
            statuses[rel] = self.task.file_status(rel, ids)
        self.file_panel.set_file_statuses(statuses)
        try:
            row = [r.relative_path for r in self.task.files].index(self._current_file)
            self.file_panel.set_current_row(row)
        except ValueError:
            self.file_panel.clear_selection()

    def _refresh_layer_panel(self) -> None:
        ids = self._layer_ids_by_file.get(self._current_file, [])
        statuses = [
            self.task.status_of(self._current_file, lid) for lid in ids
        ] if self.task else []
        issue_counts = [
            len(self.task.issues_for(self._current_file, lid)) for lid in ids
        ] if self.task else []
        self.layer_panel.set_statuses(statuses, issue_counts)

    def _refresh_layer_selection(self) -> None:
        self.layer_panel.set_current_row(self._current_index)

    def _refresh_stats_panel(self) -> None:
        if self.task is None:
            return
        # 当前 PSD
        ids = self._layer_ids_by_file.get(self._current_file, [])
        names = self._layer_names_by_file.get(self._current_file, [])
        statuses = [self.task.status_of(self._current_file, lid) for lid in ids]
        issue_counts = [len(self.task.issues_for(self._current_file, lid)) for lid in ids]
        self.stats_panel.set_current_psd(
            self._current_file, names, statuses, issue_counts
        )
        # 总体
        self.stats_panel.set_total(self.task.count_all(self._layer_ids_by_file))

    def _refresh_issue_panel(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            self.issue_panel.clear()
            return
        info = self.current_doc.layers[self._current_index]
        status = self.task.status_of(self._current_file, info.id)
        issues = self.task.issues_for(self._current_file, info.id)
        self.issue_panel.set_current(info.name, status, issues)
        self.issue_panel.set_buttons_enabled(True)

    def _refresh_enabled_state(self) -> None:
        has_task = self.task is not None
        self.action_close.setEnabled(has_task)
        self.action_save.setEnabled(has_task)
        self.action_renumber.setEnabled(has_task)
        self.action_report.setEnabled(has_task)
        self.action_redraw.setEnabled(has_task)
        self.action_compare.setEnabled(has_task)
        self.action_recenter.setEnabled(has_task)
        self.ratio_combo.setEnabled(has_task)
        if not has_task:
            self.issue_panel.set_buttons_enabled(False)
        self._refresh_nav_pad()

    # ------------------------------------------------- Android 专有：浮动方向键

    def _setup_nav_pad(self) -> None:
        """Android 上给画布右下角加一组十字键（模拟键盘方向键）。

        为什么需要：触屏没有方向键，而画布手势已用于平移/拖框，不适合再抢来翻页；
        按钮点击**直接调用**下面四个动作方法（不伪造按键事件），行为与快捷键一致。
        桌面端不创建 —— 布局与改造前逐像素相同。
        """
        if not is_android_strict():
            return
        callbacks = {
            "prev_file": self.prev_psd,
            "next_file": self.next_psd,
            "prev_layer": self.prev_layer,
            "next_layer": self.next_layer,
        }
        self.nav_pad: Optional[NavPad] = NavPad(self.viewer, callbacks)
        self.viewer.installEventFilter(self)
        self.nav_pad.show()
        self._refresh_nav_pad()

    def eventFilter(self, obj, event) -> bool:
        """画布尺寸变化时把十字键重新贴到右下角。"""
        pad = getattr(self, "nav_pad", None)
        if pad is not None and obj is self.viewer and event.type() == QEvent.Type.Resize:
            pad.reposition()
        return super().eventFilter(obj, event)

    def _refresh_nav_pad(self) -> None:
        """按当前任务/文件/图层位置刷新十字键的置灰状态。

        未打开任务 → 四个全部禁用（并隐藏，避免在空画布上多出一组不能用的按钮）；
        到边界（第一个/最后一个）→ 对应方向禁用。
        """
        pad = getattr(self, "nav_pad", None)
        if pad is None:
            return
        if self.task is None:
            pad.set_enabled_state(
                can_prev_file=False, can_next_file=False,
                can_prev_layer=False, can_next_layer=False,
            )
            pad.setVisible(False)
            return
        files = self.task.files
        try:
            file_idx = next(i for i, f in enumerate(files) if f.relative_path == self._current_file)
        except StopIteration:
            file_idx = -1
        doc = self.current_doc
        layer_count = len(doc.layers) if doc is not None else 0
        pad.set_enabled_state(
            can_prev_file=file_idx > 0,
            can_next_file=0 <= file_idx < len(files) - 1,
            can_prev_layer=self._current_index > 0,
            can_next_layer=0 <= self._current_index < layer_count - 1,
        )
        pad.setVisible(True)
        pad.raise_()
        pad.reposition()

    def _refresh_title(self) -> None:
        if self.task is None:
            self.setWindowTitle(f"{APP_NAME} v{__version__}")
            return
        self.setWindowTitle(
            f"{APP_NAME} v{__version__} — {self.task.task_name} — {self._current_file}"
        )

    def _on_camera_changed(self) -> None:
        self.zoom_label.setText(f"缩放：{self.viewer.camera.zoom * 100:.0f}%")

    def _on_chip_clicked(self, index: int) -> None:
        if 0 <= index < len(self._layer_ids_by_file.get(self._current_file, [])):
            self._request_layer_switch(index)

    # ================================================================= 生命周期

    def closeEvent(self, event) -> None:
        # 后台加载仍在运行 → 请求取消并等待其退出，避免线程残留
        if self._loader is not None and self._loader.isRunning():
            self._loader.request_cancel()
            self._loader.wait(5000)
        # 返修单仍在生成 → 同样请求取消并等待（取消在页边界生效）
        if self._report_worker is not None and self._report_worker.isRunning():
            self._report_worker.request_cancel()
            self._report_worker.wait(5000)
        # 问题编号检查仍在进行 → 请求取消并等待
        if self._numbering_worker is not None and self._numbering_worker.isRunning():
            self._numbering_worker.request_cancel()
            self._numbering_worker.wait(5000)
        self._preload.stop()
        self._preload.wait(8000)
        self._evict_timer.stop()   # 关窗后不再重试驱逐
        if self.task is not None:
            self._autosave_timer.stop()
            self.save_task()
        # 释放文档 / 图层像素 / 预生成 QImage 等重载荷。
        # 关闭路径过去漏了这一步：窗口对象关闭后仍持有整份任务数据，
        # 每窗口常驻内存只涨不落（真实尺寸 PSD 下约 110 MB）。
        # 放在 save_task() 之后：任务数据已落盘，清空不会丢进度。
        self._release_task_data()
        self._save_settings()
        super().closeEvent(event)
