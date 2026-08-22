"""主窗口：打开流程、双重导航、快捷键、自动对比、问题、统计、自动保存、返修单。

对应需求：§5～§9、§11～§16、§21～§27、§30～§46、§59、§61、§66。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QToolBar,
    QWidget,
)

from mangaproof import APP_NAME, __version__
from mangaproof.camera.centering import layer_visual_bounds
from mangaproof.compare.controller import BG_ONLY, ORIGINAL, CompareController
from mangaproof.config.settings import (
    DISPLAY_RATIOS,
    Settings,
    SettingsManager,
)
from mangaproof.psd.document import PSDDocument
from mangaproof.psd.image_cache import LayerImageCache
from mangaproof.psd.loader import (
    NoCompositeError,
    PSDReadError,
)
from mangaproof.report.generator import (
    default_report_name,
    generate_report,
    resolve_report_path,
)
from mangaproof.review import navigator, persistence
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
from mangaproof.ui.dialogs import IssueDialog, ReportDialog
from mangaproof.ui.file_panel import FilePanel
from mangaproof.ui.issue_panel import IssuePanel
from mangaproof.ui.layer_panel import LayerPanel
from mangaproof.ui.license_dialog import LicenseDialog
from mangaproof.ui.preloader import KIND_EXTRA, KIND_OPEN, KIND_PRELOAD, PreloadWorker
from mangaproof.ui.settings_dialog import SettingsDialog
from mangaproof.ui.statistics_panel import StatisticsPanel
from mangaproof.ui.task_loader import (
    KIND_CANCELLED,
    KIND_MISMATCH,
    KIND_NO_FILES,
    TaskLoadWorker,
)
from mangaproof.ui.viewer_widget import SOURCE_BG, SOURCE_MERGED, ViewerWidget

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


class MainWindow(QMainWindow):
    def __init__(self, settings_manager: SettingsManager):
        super().__init__()
        self.settings_manager = settings_manager
        self.settings: Settings = settings_manager.settings

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

        # PSD 预加载线程（切换大文件不卡顿）
        self._preload = PreloadWorker(lambda rel: self._docs.get(rel), self)
        self._preload.task_done.connect(self._on_preload_done)
        self._preload.start()
        self._pending_open_rel = ""   # 异步打开中的文件（快速切换时替换）
        self._open_restore = False
        self._open_dialog = None      # 切换文件的忙碌进度框
        self._preload_targets: set = set()   # 预加载队列中未完成的文件

        self._compare = CompareController(self)
        self._compare.display_changed.connect(self._on_compare_display_changed)
        self._compare.running_changed.connect(self._on_compare_running_changed)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self.save_task)

        self._shortcuts: List[QShortcut] = []
        self._updating_panels = False

        self._build_ui()
        self._build_menus()
        self._rebuild_shortcuts()
        self._update_save_label(initial=True)
        self._refresh_enabled_state()

    # ================================================================= UI

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1440, 900)

        # ---- 中央 Viewer ----
        self.viewer = ViewerWidget()
        self.viewer.rect_drawn.connect(self._on_rect_drawn)
        self.viewer.issue_drawn.connect(self._on_issue_drawn)
        self.viewer.camera_changed.connect(self._on_camera_changed)
        self.viewer.pending_changed.connect(self._on_pending_changed)
        self.setCentralWidget(self.viewer)

        # ---- 左侧：文件列表 + 统计 ----
        self.file_panel = FilePanel()
        self.file_panel.file_activated.connect(self._on_file_activated)
        self.stats_panel = StatisticsPanel()
        self.stats_panel.layer_chip_clicked.connect(self._on_chip_clicked)
        left_container = QWidget()
        from PySide6.QtWidgets import QVBoxLayout
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
        self.issue_panel.custom_comment_requested.connect(self._on_custom_comment)
        self.issue_panel.edit_issue_requested.connect(self._on_edit_issue)
        self.issue_panel.delete_issue_requested.connect(self._on_delete_issue)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.layer_panel, 3)
        right_layout.addWidget(self.issue_panel, 2)
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
        toolbar.addSeparator()
        self.action_save = self._add_tool_action(toolbar, "保存", "Ctrl+S", self.save_task)
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
        self.ratio_combo = QComboBox()
        for r in DISPLAY_RATIOS:
            self.ratio_combo.addItem(f"{int(r * 100)}%", r)
        idx = self.ratio_combo.findData(self.settings.layer_display_ratio)
        self.ratio_combo.setCurrentIndex(max(0, idx))
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        toolbar.addWidget(self.ratio_combo)

        # ---- 状态栏 ----
        # 预加载状态放在「已保存」左边（addPermanentWidget 右对齐、逆序）
        self.preload_label = QLabel("")
        self.save_label = QLabel("")
        self.zoom_label = QLabel("缩放：100%")
        self.progress_label = QLabel("")
        self.hint_label = QLabel("")
        self.statusBar().addPermanentWidget(self.save_label)
        self.statusBar().addPermanentWidget(self.preload_label)
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().addPermanentWidget(self.progress_label)
        self.statusBar().addWidget(self.hint_label)

        self._update_shortcut_hints()

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
        file_menu.addSeparator()
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_report)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        settings_menu = menubar.addMenu("设置(&S)")
        settings_action = QAction("设置…", self)
        settings_action.triggered.connect(self.open_settings_dialog)
        settings_menu.addAction(settings_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于 MangaProof", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        license_action = QAction("第三方许可…", self)
        license_action.triggered.connect(self._show_licenses)
        help_menu.addAction(license_action)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME} v{__version__}</b><br><br>"
            "漫画翻译质量检查与返修标注工具。<br><br>"
            "独立于 Photoshop：不调用 Photoshop API、不修改 PSD、<br>"
            "Original 直接使用 PSD 自带 merged image。<br><br>"
            "本软件使用小米 MiSans 字体，第三方组件与许可证信息<br>"
            "见「帮助 → 第三方许可」。",
        )

    def _show_licenses(self) -> None:
        dialog = LicenseDialog(self)
        dialog.exec()

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.recent_paths
        if not recent:
            empty = QAction("（无记录）", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path_str in recent:
            action = QAction(path_str, self)
            action.triggered.connect(
                lambda _=False, p=path_str: self._open_recent(p)
            )
            self.recent_menu.addAction(action)

    def _open_recent(self, path_str: str) -> None:
        path = Path(path_str)
        if path.is_dir():
            self.open_folder(path)
        elif path.is_file():
            self.open_single(path)
        else:
            QMessageBox.warning(self, "最近打开", f"路径不存在：\n{path_str}")
            self._rebuild_recent_menu()

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
            f"{d(kb.get('save_task', 'Ctrl+S'))} 保存"
        )
        # 工具栏/菜单按钮同步显示当前绑定（需求 §30）
        self.action_open_psd.setText(f"打开 PSD ({d(kb.get('open_psd', 'Ctrl+O'))})")
        self.action_open_folder.setText(f"打开文件夹 ({d(kb.get('open_folder', 'Ctrl+Shift+O'))})")
        self.action_save.setText(f"保存 ({d(kb.get('save_task', 'Ctrl+S'))})")
        self.action_report.setText(f"生成返修单 ({d(kb.get('generate_report', 'Ctrl+R'))})")
        self.action_redraw.setText(f"红框模式 ({d(kb.get('redraw_mode', 'R'))})")
        self.action_compare.setText(
            f"{'停止自动对比' if self._compare.is_running else '自动对比'} ({d(kb.get('toggle_compare', 'Space'))})"
        )

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
            self._shortcuts.append(sc)

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

        # 问题类型快捷键（需求 §35）：文本输入框聚焦时不触发
        issue_guard = self._focus_not_text_input
        for item in self.settings.issue_types:
            key = item.get("key", "")
            name = item["name"]
            if key:
                bind(key, lambda n=name: self._on_issue_key(n), guard=issue_guard)

        self._update_shortcut_hints()
        self._update_issue_panel_shortcuts()

    def _update_issue_panel_shortcuts(self) -> None:
        """问题面板按钮动态显示当前绑定（需求 §30）。"""
        kb = self.settings.keybindings
        d = self._display_key
        bindings = {
            "pass": d(kb.get("pass_layer", "Return")),
            "fail": d(kb.get("fail_layer", "/")),
            "redraw": d(kb.get("redraw_mode", "R")),
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
        path_str, _ = QFileDialog.getOpenFileName(
            self, "打开单个 PSD", "", "PSD/PSB 文件 (*.psd *.psb)"
        )
        if path_str:
            self.open_single(Path(path_str))

    def open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "打开漫画文件夹", "")
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
        self._compare.stop()
        self._load_mode = mode
        self._load_path = Path(path)

        worker = TaskLoadWorker(
            mode, path,
            recursive=self.settings.recursive_scan,
            force_fresh=force_fresh,
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
        self.settings_manager.add_recent(str(self._load_path))
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

        self.file_panel.set_task_info(task.task_name, task.task_type)
        self.file_panel.set_files([r.relative_path for r in task.files])
        self._preload_targets.clear()
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

    # ================================================================= 文档管理

    def _ensure_doc(self, rel: str) -> Optional[PSDDocument]:
        if self._base_dir is None:
            return None
        doc = self._docs.get(rel)
        if doc is None:
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
        self._compare.stop()
        self._request_open_file(rel, restore=False)

    def _request_open_file(self, rel: str, restore: bool) -> None:
        """打开文件入口：已预加载走快路径；未命中交后台线程 + 进度框。

        快速连续切换时，新请求会替换未处理的旧请求（见 PreloadWorker）。
        """
        doc = self._docs.get(rel)
        if doc is None:
            QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")
            return
        self._open_restore = restore
        if doc.has_merged():
            self._open_file_internal(rel, restore)
            self._refresh_all_panels()
            self._mark_dirty()
            self.viewer.setFocus()
            self._schedule_preloads(rel)
            return
        # 未预加载：后台提取 + 非模态忙碌进度框（不阻塞继续切换）
        self._pending_open_rel = rel
        index = self._choose_layer_index(rel, restore)
        layer_id = ""
        if index is not None:
            ids = self._layer_ids_by_file.get(rel, [])
            if index < len(ids):
                layer_id = ids[index]
        self._preload.submit_open(rel, layer_id)
        self._show_open_progress(rel)

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
        """打开 PSD 并选择图层（快路径：图像已缓存，需求 §12、§13）。"""
        doc = self._docs.get(rel)
        if doc is None:
            QMessageBox.warning(self, "打开 PSD", f"无法读取该 PSD 文件：{rel}")
            return
        self._current_file = rel
        self.viewer.set_document(doc)
        self.viewer.set_source(SOURCE_MERGED)

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
            self.viewer.set_issues([])
            self.viewer.set_layer_outline(None)
            return

        index = self._choose_layer_index(rel, restore)
        self._select_layer_internal(index)

    # -- 预加载与异步打开（大 PSD 切换不卡顿） -----------------------------

    def _on_preload_done(self, rel: str, kind: str, ok: bool) -> None:
        if kind == KIND_PRELOAD:
            # 阶段 A（merged）完成：切换文件已可用
            self._preload_targets.discard(rel)
            self._update_preload_label()
            return
        if kind == KIND_EXTRA:
            return  # 阶段 B（背景图/图层像素）完成，无需 UI 动作
        # open 结果：快速切换后旧文件的结果直接忽略
        if rel != self._pending_open_rel:
            return
        self._close_open_progress()
        self._pending_open_rel = ""
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

    def _on_open_progress_cancelled(self) -> None:
        self._preload.cancel_open()
        self._pending_open_rel = ""
        self._close_open_progress()
        self.statusBar().showMessage("已取消打开", 3000)

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
        candidates: List[str] = []
        for j in (i + 1, i + 2, i + 3, i - 1):
            if 0 <= j < len(order):
                candidates.append(order[j])

        def target_layer_of(target_rel: str) -> str:
            index = self._choose_layer_index(target_rel, restore=False)
            if index is None:
                return ""
            ids = self._layer_ids_by_file.get(target_rel, [])
            return ids[index] if index < len(ids) else ""

        merged_jobs: List[Tuple[str, str]] = []
        extra_jobs: List[Tuple[str, str]] = []
        # 当前文件补背景图与当前图层像素（自动对比免等待）
        cur_lid = ""
        if self._current_file == rel and self._current_index >= 0:
            ids = self._layer_ids_by_file.get(rel, [])
            if self._current_index < len(ids):
                cur_lid = ids[self._current_index]
        extra_jobs.append((rel, cur_lid))

        for c in candidates:
            doc = self._docs.get(c)
            layer_id = target_layer_of(c)
            if doc is None or not doc.has_merged():
                merged_jobs.append((c, layer_id))
            extra_jobs.append((c, layer_id))

        self._preload_targets = {r for r, _ in merged_jobs}
        self._preload.set_preloads(merged_jobs, extra_jobs)
        self._update_preload_label()

        # 内存策略：释放窗口外文档的 merged/bg（图层树与 LRU 保留）。
        # release_images 非阻塞：后台仍在提取的文档自动跳过，下轮再回收。
        keep = {rel, *candidates}
        for r in order:
            if r in keep:
                continue
            doc = self._docs.get(r)
            if doc is not None:
                doc.release_images()

    def _update_preload_label(self) -> None:
        if self.task is None:
            self.preload_label.setText("")
            return
        n = len(self._preload_targets)
        if n > 0:
            self.preload_label.setText(f"预加载中…（{n} 个）")
            self.preload_label.setStyleSheet("color: #f5a623;")
        else:
            self.preload_label.setText("预加载完成")
            self.preload_label.setStyleSheet("color: #4caf50;")

    def _select_layer_internal(self, index: int) -> None:
        """切换图层统一行为（需求 §12）：停对比 → Original → 切换 → 定位 → 缩放。"""
        self._compare.stop()
        doc = self.current_doc
        if doc is None or not (0 <= index < len(doc.layers)):
            self._current_index = -1
            return
        self._current_index = index
        info = doc.layers[index]
        if self.task is not None:
            self.task.current_file = self._current_file
            self.task.current_layer = info.id

        self.viewer.set_issues(
            self.task.issues_for(self._current_file, info.id) if self.task else []
        )
        self.viewer.set_layer_outline(layer_visual_bounds(info))
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

    def _on_layer_activated(self, index: int) -> None:
        if self._updating_panels:
            return
        self._select_layer_internal(index)
        self._refresh_layer_selection()
        self._mark_dirty()
        self.viewer.setFocus()

    def prev_layer(self) -> None:
        if self.current_doc is None or self._current_index <= 0:
            return
        self._select_layer_internal(self._current_index - 1)
        self._refresh_layer_selection()
        self._mark_dirty()

    def next_layer(self) -> None:
        if self.current_doc is None or self._current_index >= len(self.current_doc.layers) - 1:
            return
        self._select_layer_internal(self._current_index + 1)
        self._refresh_layer_selection()
        self._mark_dirty()

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
        self._compare.stop()
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
        self._compare.stop()
        info = self.current_doc.layers[self._current_index]
        self.task.set_status(self._current_file, info.id, FAILED)
        self.issue_panel.set_hint(
            "已标记 ✗ 未通过 — 可拖框添加问题或输入自定义批注；"
            f"{self._display_key(self.settings.binding('pass_layer') or 'Return')} 跳到下一个未监制图层。"
        )
        self._refresh_all_panels()
        self._mark_dirty()
        self.viewer.setFocus()
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
            self._select_layer_internal(idx)
            self._refresh_all_panels()
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

    def _on_all_reviewed(self) -> None:
        """全部图层已检查（需求 §44）。"""
        self.issue_panel.set_hint("")
        QMessageBox.information(
            self, "监制完成", "所有图层已经检查。\n\n任务：%s" % (self.task.task_name if self.task else "")
        )
        if self.settings.generate_pdf_on_complete and self.task is not None:
            self._generate_report(interactive=False)

    # ================================================================= 自动对比（需求 §21～§26）

    def toggle_compare(self) -> None:
        if self.current_doc is None:
            return
        if self._compare.is_running:
            self._compare.stop()
            return
        # 预取 bg 图像，避免闪切中途卡顿（需求 §59：不重新读取）
        if self.current_doc.bg_image() is None:
            self.statusBar().showMessage("未找到可用背景图层，无法自动对比", 3000)
            return
        self._compare.start()

    def _on_compare_display_changed(self, state: str) -> None:
        self.viewer.set_source(SOURCE_BG if state == BG_ONLY else SOURCE_MERGED)

    def _on_compare_running_changed(self, running: bool) -> None:
        self.action_compare.setChecked(running)
        self.action_compare.setText("停止自动对比 (Space)" if running else "自动对比 (Space)")
        if not running:
            self.viewer.set_source(SOURCE_MERGED)

    def _on_compare_action_toggled(self, checked: bool) -> None:
        if checked != self._compare.is_running:
            self.toggle_compare()

    # ================================================================= 问题（需求 §31～§40）

    def _on_issue_key(self, type_name: str) -> None:
        """方式 A：快捷键选类型 → 拖框（需求 §35、§37）。"""
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        if self._compare.is_running:
            self._compare.stop()   # 需求 §40：停止对比后创建
        self.viewer.set_pending_type(type_name)
        self.issue_panel.set_hint(
            f"请在画布上拖拽红框：{type_name}"
            f"（{self._display_key(self.settings.binding('cancel_operation') or 'Esc')} 取消）"
        )
        self.viewer.setFocus()

    def _on_issue_drawn(self, issue_type: str, x: float, y: float, w: float, h: float) -> None:
        dialog = IssueDialog(
            self.settings.issue_type_names(),
            self,
            default_type=issue_type,
            rect=(x, y, w, h),
            title=f"添加问题：{issue_type}",
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(
                *dialog.result_values(), rect=(x, y, w, h)
            )
        self.issue_panel.set_hint("")

    def _on_rect_drawn(self, x: float, y: float, w: float, h: float) -> None:
        """方式 B：先拖框 → 选择类型（需求 §37）。"""
        if self._compare.is_running:
            self._compare.stop()
        dialog = IssueDialog(self.settings.issue_type_names(), self, rect=(x, y, w, h))
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(*dialog.result_values(), rect=(x, y, w, h))

    def _on_custom_comment(self) -> None:
        """自定义批注（需求 §36），无红框。"""
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        if self._compare.is_running:
            self._compare.stop()
        dialog = IssueDialog(
            self.settings.issue_type_names(), self, default_type="其他",
            title="自定义批注",
        )
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            self._commit_new_issue(*dialog.result_values(), rect=(0, 0, 0, 0))

    def _refresh_viewer_issues(self) -> None:
        """问题变化后刷新 Viewer 红框 Overlay（需求 §39）。"""
        if self.task is not None and self.current_doc is not None and self._current_index >= 0:
            info = self.current_doc.layers[self._current_index]
            self.viewer.set_issues(
                self.task.issues_for(self._current_file, info.id)
            )

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
        self.viewer.setFocus()

    def _on_edit_issue(self, issue_id: str) -> None:
        if self.task is None:
            return
        issue = next((i for i in self.task.issues if i.issue_id == issue_id), None)
        if issue is None:
            return
        dialog = IssueDialog(self.settings.issue_type_names(), self, issue=issue, title="编辑问题")
        if dialog.exec() == IssueDialog.DialogCode.Accepted:
            issue_type, comment = dialog.result_values()
            issue.type = issue_type
            issue.comment = comment
            self._refresh_all_panels()
            self._refresh_viewer_issues()
            self._mark_dirty()

    def _on_delete_issue(self, issue_id: str) -> None:
        if self.task is None:
            return
        self.task.remove_issue(issue_id)
        self._refresh_all_panels()
        self._refresh_viewer_issues()
        self._mark_dirty()

    def toggle_redraw_mode(self) -> None:
        if self.task is None or self.current_doc is None:
            return
        if self._compare.is_running:
            self._compare.stop()
        self.viewer.set_redraw_mode(not self.viewer.redraw_mode)

    def _on_redraw_mode_toggled(self, checked: bool) -> None:
        if checked != self.viewer.redraw_mode:
            self.viewer.set_redraw_mode(checked)

    def _on_add_issue_requested(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            return
        if self._compare.is_running:
            self._compare.stop()
        self.viewer.set_redraw_mode(True)
        self.issue_panel.set_hint(
            f"拖框模式：在画布上拖拽红框"
            f"（{self._display_key(self.settings.binding('cancel_operation') or 'Esc')} 取消）"
        )
        self.viewer.setFocus()

    def _on_pending_changed(self) -> None:
        self.action_redraw.setChecked(self.viewer.redraw_mode)
        if not self.viewer.any_issue_mode():
            self.issue_panel.set_hint("")

    def cancel_operation(self) -> None:
        """Esc：取消/退出当前批注操作（需求 §30）。"""
        if self.viewer.any_issue_mode():
            self.viewer.cancel_pending()
            self.issue_panel.set_hint("")
            return
        if self._compare.is_running:
            self._compare.stop()

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

    def save_task(self) -> None:
        if self.task is None:
            return
        path = self.progress_file_path()
        if path is None:
            return
        try:
            persistence.save_task(self.task, path)
        except OSError as exc:
            log.exception("保存任务失败")
            self._update_save_label(initial=False, error=str(exc))
            return
        self._update_save_label(initial=False, saved=True)
        log.info("任务已保存：%s", path)

    def _update_save_label(self, initial: bool = False, saved: bool = False, error: str = ""):
        if initial:
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
            incomplete = (
                self.task.count_all(
                    {rel: len(ids) for rel, ids in self._layer_ids_by_file.items()}
                )["unreviewed"]
                > 0
            )
            dialog = ReportDialog(name, self.task.task_name, incomplete, self)
            if dialog.exec() != ReportDialog.DialogCode.Accepted:
                return
            name = dialog.report_name()

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

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            generate_report(
                self.task,
                self._layer_ids_by_file,
                out_path,
                image_provider=lambda rel: self._docs.get(rel),
            )
        except Exception:
            log.exception("生成返修单失败")
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "生成返修单", "生成返修单失败，详情见 logs/mangaproof.log。")
            return
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(self, "生成返修单", f"已生成：\n{out_path}")
        log.info("返修单已生成：%s", out_path)

    # ================================================================= 设置

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            dialog.apply_to(self.settings)
            self.settings_manager.save()
            self._rebuild_shortcuts()
            idx = self.ratio_combo.findData(self.settings.layer_display_ratio)
            self.ratio_combo.setCurrentIndex(max(0, idx))
            self.recenter_current_layer()
            # 控制台开关即时生效（打包产物下隐藏/恢复控制台窗口）
            from mangaproof.console import apply_console_visibility

            apply_console_visibility(self.settings)

    def _on_ratio_changed(self, index: int) -> None:
        ratio = float(self.ratio_combo.itemData(index))
        if abs(ratio - self.settings.layer_display_ratio) < 1e-6:
            return
        self.settings.layer_display_ratio = ratio
        self.settings_manager.save()
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
        layer_counts = {rel: len(v) for rel, v in self._layer_ids_by_file.items()}
        self.stats_panel.set_total(self.task.count_all(layer_counts))

    def _refresh_issue_panel(self) -> None:
        if self.task is None or self.current_doc is None or self._current_index < 0:
            self.issue_panel.set_current("", UNREVIEWED, [])
            self.issue_panel.set_buttons_enabled(False)
            return
        info = self.current_doc.layers[self._current_index]
        status = self.task.status_of(self._current_file, info.id)
        issues = self.task.issues_for(self._current_file, info.id)
        self.issue_panel.set_current(info.name, status, issues)
        self.issue_panel.set_buttons_enabled(True)

    def _refresh_enabled_state(self) -> None:
        has_task = self.task is not None
        self.action_save.setEnabled(has_task)
        self.action_report.setEnabled(has_task)
        self.action_redraw.setEnabled(has_task)
        self.action_compare.setEnabled(has_task)
        self.action_recenter.setEnabled(has_task)
        self.ratio_combo.setEnabled(has_task)
        if not has_task:
            self.issue_panel.set_buttons_enabled(False)

    def _refresh_title(self) -> None:
        if self.task is None:
            self.setWindowTitle(f"{APP_NAME} v{__version__}")
            self.progress_label.setText("")
            return
        layer_counts = {rel: len(v) for rel, v in self._layer_ids_by_file.items()}
        counts = self.task.count_all(layer_counts)
        self.progress_label.setText(
            f"监制进度：{counts['reviewed']} / {counts['total']}　"
            f"通过 {counts['passed']}　未通过 {counts['failed']}　未监制 {counts['unreviewed']}"
        )
        self.setWindowTitle(
            f"{APP_NAME} v{__version__} — {self.task.task_name} — {self._current_file}"
        )

    def _on_camera_changed(self) -> None:
        self.zoom_label.setText(f"缩放：{self.viewer.camera.zoom * 100:.0f}%")

    def _on_chip_clicked(self, index: int) -> None:
        if 0 <= index < len(self._layer_ids_by_file.get(self._current_file, [])):
            self._select_layer_internal(index)
            self._refresh_layer_selection()
            self._mark_dirty()

    # ================================================================= 生命周期

    def closeEvent(self, event) -> None:
        # 后台加载仍在运行 → 请求取消并等待其退出，避免线程残留
        if self._loader is not None and self._loader.isRunning():
            self._loader.request_cancel()
            self._loader.wait(5000)
        self._preload.stop()
        self._preload.wait(8000)
        if self.task is not None:
            self._autosave_timer.stop()
            self.save_task()
        self.settings_manager.save()
        super().closeEvent(event)
