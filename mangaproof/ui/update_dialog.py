# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新页面（需求 §10、§11、§13、§29~§34）。

界面结构按需求 §11（按钮行于 2026-09-24 改为"两个语义固定的动作按钮"）：

.. code-block:: text

    更新分支        [ stable ▼ ]
    更新渠道        [ Cloudflare R2 ▼ ]
    MirrorChyan CDK [                    ]
    代理            [                    ] [ 测试代理 ]
    下载限速        [ 不限速 ▼ ]
    --------------------------------
    （状态/进度区）
    [ 保存并检查更新 ]      [ 下载更新 ] [ 取消 ]

三条容易做错的约束，这里刻意用代码固化：

1. **按钮左右位置固定**（需求 §11.7：「保存并检查更新」在左、「取消」在右）。
   因此**不用** ``QDialogButtonBox`` —— 它会按平台规范重排（macOS 会把主按钮放右侧）。
2. **保存绑定在语义固定的按钮上**（需求 §13）：左侧按钮**永远是**"保存配置 +
   检查更新"，右侧按钮按状态在「下载更新 / 安装更新」之间切换，且**下载前同样
   先保存**。旧版把"检查 / 下载 / 安装"三个动作挤在同一个会改文案的按钮上、
   只在"检查"那一支保存，于是"检查完之后再改设置"掉进缝里：代理与限速既不会
   下发给下载线程、也不会写盘，分支与渠道被静默忽略。现在改动的这套语义由
   :meth:`UpdateDialog._sync_actions` 单点推导，不允许再散落 setText。
   右侧按钮**常驻置灰**（不做 show/hide），与进度条常驻占位同一个理由：
   布局一次算准，状态切换不重排、窗口不跳。
3. **表单区不允许被动态内容压扁**：状态文案可长可短，而 Qt 只会按"窗口大小
   不变"重新分配高度——空间不够时 QFormLayout 会强行压缩行高，实测「代理」
   那一行（内含按钮，行高最大）会被压到 12px。对策见
   :meth:`UpdateDialog._fit_status_height`：状态区固定高度并可滚动、进度条
   常驻占位、窗口最小高度一次算准，于是表单各行高度恒定。

另外两条纯 UI 约定（与设置页保持一致）：

- 下拉框一律用 :class:`~mangaproof.ui.widgets.NoWheelComboBox`：滚轮误改分支/
  渠道/限速后很难察觉，改值只走显式交互；
- 「代理」行两端与其他输入行严格对齐，见 :meth:`UpdateDialog._proxy_row`。

视觉沿用现有 MangaProof 风格（需求 1.1.1）：不写死颜色、不自建样式表，
对话框自动继承 ``ui/theme.py`` 的全局暗色主题；进度条用 ``QProgressBar``
（检查阶段为不确定模式，需求 §31）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mangaproof import APP_NAME
from mangaproof.config.settings import (
    R2_PUBLIC_BASE,
    SPEED_LIMIT_CHOICES,
    UPDATE_BRANCHES,
    UPDATE_CHANNELS,
    Settings,
    UpdateSettings,
)
from mangaproof.ui.update_worker import (
    CheckOutcome,
    ProxyTestWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)
from mangaproof.update import cdk_store
from mangaproof.update.errors import UpdateError
from mangaproof.update.humanize import human_size, human_speed
from mangaproof.update.models import CheckResult
from mangaproof.ui.widgets import NoWheelComboBox

log = logging.getLogger("mangaproof.ui.update_dialog")

#: 状态区固定高度（px）：内容超过就在这一块里滚动。
#: 必须是**固定**高度而不是只设上限：Qt 在窗口大小不变的前提下重排布局，
#: 后出现的进度条会从状态区"抢"高度，抢不到就去压缩 QFormLayout 的行高——
#: 实测「代理」那一行（含按钮，行高最大）会被压到 12px，看起来就是"被压扁"。
#: 固定住状态区，表单各行高度就恒定，对话框最小高度也就能一次算准。
#: 取值权衡：够放"发现新版本 + 文件 + 大小"而不用滚动，又不至于让空闲窗口太高。
STATUS_AREA_HEIGHT = 180

#: 关窗/取消时等待后台线程自行退出的时长（ms）。
#: 只等"一定很快退出"的线程；等不到就脱离交给主窗口托管，**绝不长时间阻塞 UI**
#: （原来的 2000ms 等待就是"卡死一小会"的来源）。
_ABORT_WAIT_MS = 250

#: 渠道显示名（值 → UI 文案）
CHANNEL_LABELS: dict[str, str] = {
    "r2": "Cloudflare R2",
    "github": "GitHub Release",
    "mirrorchyan": "MirrorChyan（需 CDK）",
}

#: 分支显示名
BRANCH_LABELS: dict[str, str] = {
    "stable": "stable（正式版）",
    "beta": "beta（测试版）",
    "alpha": "alpha（内测版）",
}

#: 底部左侧按钮文案：**恒定不变**。它的语义只有一个——保存当前配置并检查更新；
#: 绝不再像旧版那样变成"立即更新 / 立即安装并重启"（那会让"保存"跟着动作跑掉）。
CHECK_BTN_TEXT = "保存并检查更新"

#: 底部右侧按钮文案：按状态在"下载 / 安装"之间切换，控件本身常驻置灰。
DOWNLOAD_BTN_TEXT = "下载更新"
INSTALL_BTN_TEXT = "安装更新"

#: 检查完成后又改了分支/渠道 → 已查到的包跟当前选择不再对应，必须重新检查。
#: 写在输出区的详情行里（不动状态正文，用户仍能读到版本与更新说明）。
STALE_SELECTION_HINT = (
    "分支或渠道已更改，当前检查结果已失效：请重新点击「保存并检查更新」。"
)

#: MirrorChyan 渠道缺 CDK（需求 §11.8/§18：CDK 为空时不得发下载请求）。
#: 必须在界面这一层拦住 —— 否则底层抛的是内部措辞
#: "CDK 为空：不得发起 MirrorChyan 下载信息请求"，用户看不懂该做什么。
MIRRORCHYAN_NEEDS_CDK = (
    "MirrorChyan 渠道需要 CDK：请填写 MirrorChyan CDK 后重试，"
    "或把更新渠道改为 Cloudflare R2 / GitHub。"
)


def _speed_label(value: int) -> str:
    return "不限速" if value == 0 else f"{value} M"


class UpdateDialog(QDialog):
    """「关于 → 更新」打开的更新页面。"""

    #: 用户在对话框里确认了配置（点「保存并检查更新」或「下载更新」）→ 主窗口负责写 settings.json
    settings_committed = Signal()
    #: 更新包已下载并校验通过，请求主程序启动安装器并退出（需求 §46）。
    #: 载荷是 ``(包路径, SHA-256)`` —— 校验值必须随信号一起递出去，因为本对话框
    #: 在 emit 之后立刻 accept() 关闭，主窗口不能再回来向它要（需求 §53：
    #: 安装器要拿这个值在替换文件前复核，缺失就只能跳过哈希校验）。
    install_requested = Signal(object)      # (Path, str)

    def __init__(self, settings: Settings, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 更新")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._settings = settings
        # draft：点「取消」时丢弃，点「保存并检查更新」（或「下载更新」）时提交
        # （需求 §13）。**下载前也提交**：否则检查之后改的代理/限速只改界面，
        # 既不下发给下载线程也不写盘。
        self._draft = UpdateSettings.from_dict(settings.update.to_dict())
        self._check_worker: UpdateCheckWorker | None = None
        self._download_worker: UpdateDownloadWorker | None = None
        self._proxy_worker: ProxyTestWorker | None = None
        self._result: CheckResult | None = None
        self._outcome: CheckOutcome | None = None
        self._package: Path | None = None
        self._package_sha256: str = ""       # 下载后本地算出的 SHA-256（传给安装器复核）
        self._state = "idle"        # idle / checking / checked / no_update / update_available / downloading / done
        #: 本轮检查用的分支/渠道：用来判断"检查完之后有没有被改过"（改了 → 结果失效）
        self._checked_branch: str | None = None
        self._checked_channel: str | None = None
        #: 关窗时"脱离"出去的线程（信号已摘，等它自己收尾）——主窗口会接管它们，
        #: 见 MainWindow._adopt_update_workers
        self._orphaned_workers: list[object] = []

        self._build_ui()
        self._load_draft()

    # -- 构建界面 ----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        # 字段列一律吃掉剩余宽度：显式声明，避免 macOS 等平台默认策略把控件
        # 留在 sizeHint 宽度上（那样右边就与其他行对不齐了）
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

        self.branch_combo = NoWheelComboBox()
        for value in UPDATE_BRANCHES:
            self.branch_combo.addItem(BRANCH_LABELS.get(value, value), value)
        form.addRow("更新分支", self.branch_combo)

        self.channel_combo = NoWheelComboBox()
        for value in UPDATE_CHANNELS:
            self.channel_combo.addItem(CHANNEL_LABELS.get(value, value), value)
        form.addRow("更新渠道", self.channel_combo)

        self.cdk_edit = QLineEdit()
        self.cdk_edit.setPlaceholderText("仅 MirrorChyan 渠道需要；留空则只能走 R2 / GitHub")
        self.cdk_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("MirrorChyan CDK", self.cdk_edit)

        # 「代理」行 = 输入框 + 测试按钮，两者贴齐字段列的两端。
        # 复合控件默认带一圈内容边距、且默认按 sizeHint 摆放（不跟着列宽伸展），
        # 结果是输入框左边比别的行右 9px、按钮右边比别的行左 9px、整体还短一截。
        # 对策：内容边距归零 + 水平方向声明 Expanding（见 _proxy_row）。
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("留空表示不使用代理；支持 http(s):// 与 socks5://")
        self.proxy_test_btn = QPushButton("测试代理")
        self.proxy_test_btn.clicked.connect(self._on_test_proxy)
        # 宽度钉死：文字在「测试代理 / 测试中…」之间来回换，不钉死输入框就会
        # 跟着一伸一缩（右边界虽然仍对齐，但看着在抖）
        self.proxy_test_btn.setFixedWidth(
            max(self.proxy_test_btn.sizeHint().width(),
                self.proxy_test_btn.minimumSizeHint().width())
        )
        form.addRow("代理", self._proxy_row())

        self.speed_combo = NoWheelComboBox()
        for value in SPEED_LIMIT_CHOICES:
            self.speed_combo.addItem(_speed_label(value), value)
        form.addRow("下载限速", self.speed_combo)

        root.addLayout(form)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("updateHint")
        root.addWidget(self.hint_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        # 状态区（对话框下半的"输出区"）放在**固定高度**的滚动容器里：
        # 文案再长也只在这里滚动，绝不撑大窗口、更不反过来压缩上面表单的行高
        # （见 _fit_status_height 与 _clear_output）。
        self.status_area = QScrollArea()
        self.status_area.setWidgetResizable(True)
        self.status_area.setFrameShape(QFrame.Shape.NoFrame)
        self.status_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.status_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        status_body = QWidget()
        status_layout = QVBoxLayout(status_body)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(6)

        self.status_label = QLabel("选择分支与渠道后点击「保存并检查更新」。")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        status_layout.addWidget(self.status_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setVisible(False)
        status_layout.addWidget(self.detail_label)
        # 注意：这里**不加** addStretch。加了下方的弹性空间会顶掉滚动条，
        # 长文案就只能被裁掉、滚不动了；内容比视口矮时由 body 的最小高度决定，
        # 不会把文字拉散。
        self.status_area.setWidget(status_body)
        root.addWidget(self.status_area, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        # 进度条**常驻占位**、空闲时禁用置灰，不做 show/hide 切换：
        # 隐藏的控件不参与布局，一露头 Qt 就得在现有窗口高度里重新分配空间——
        # 抢不到高度时 QFormLayout 会去压行高，表现为「代理」那一行被压扁
        # （实测 30px → 12px）。常驻占位后表单高度恒定，最小高度也一次算准，
        # 任何状态切换都不可能再挤上面的表单。
        self.progress.setMinimumHeight(self.progress.sizeHint().height())
        self.progress.setEnabled(False)
        root.addWidget(self.progress)

        buttons = QHBoxLayout()
        # 左：语义恒定（保存 + 检查）。右：常驻置灰，按状态在"下载 / 安装"间切换。
        self.check_btn = QPushButton(CHECK_BTN_TEXT)
        self.check_btn.setDefault(True)
        self.check_btn.clicked.connect(self._on_check_clicked)

        self.download_btn = QPushButton(DOWNLOAD_BTN_TEXT)
        self.download_btn.clicked.connect(self._on_download_clicked)
        # 宽度钉死成「下载更新 / 安装更新」里较宽的那个：文案会来回换，
        # 不钉死按钮就会跟着一伸一缩（与「测试代理」按钮同一个理由）。
        # 常驻置灰而不是 show/hide：隐藏的控件不参与布局，一露头就得重排
        # （实测按钮行只需 ~256px，远小于窗口 560px 最小宽，重排虽不至于撑大
        # 窗口，但会让按钮左右位置在状态切换时抖动）。
        widest = 0
        for label in (DOWNLOAD_BTN_TEXT, INSTALL_BTN_TEXT):
            self.download_btn.setText(label)
            widest = max(
                widest,
                self.download_btn.sizeHint().width(),
                self.download_btn.minimumSizeHint().width(),
            )
        self.download_btn.setFixedWidth(widest)
        self.download_btn.setText(DOWNLOAD_BTN_TEXT)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._on_cancel)
        # 需求 §11.7：主按钮在左、取消在右 —— 用 HBox 固定，不交给 QDialogButtonBox
        buttons.addWidget(self.check_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.download_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)
        #: 按钮行的布局引用：位置是需求 §11.7 的硬要求，测试要能直接断言顺序
        self.action_row = buttons

        self._update_hint()
        self._fit_status_height()
        self._sync_actions()

        # 「检查完之后把分支/渠道改掉」→ 已查到的包不再与选择对应，右侧按钮置灰
        # 并提示重查（需求 §13 的补强）。连接必须放在按钮构造之后：
        # 上面的 addItem 也会发 currentIndexChanged，那时按钮还不存在。
        self.branch_combo.currentIndexChanged.connect(self._on_selection_changed)
        self.channel_combo.currentIndexChanged.connect(self._on_selection_changed)

    # -- 布局辅助 ----------------------------------------------------------

    def _proxy_row(self) -> QWidget:
        """「代理」行的复合控件：输入框 + 测试按钮。

        QFormLayout 只支持「标签 / 字段」两列，塞不进第三个控件，所以按钮必须
        和输入框待在同一个字段里。要让它与其他行的边界严格对齐，两个条件缺一不可：

        1. 内容边距归零——默认边距会把输入框左边界推进去、把按钮右边界拉出来；
        2. 水平方向 ``Expanding``——默认 ``Preferred`` 时 QFormLayout 只按
           ``sizeHint`` 给宽度，整行会比别的字段短一截（右边对不齐）。
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.proxy_edit, 1)
        row.addWidget(self.proxy_test_btn)
        widget = QWidget()
        widget.setLayout(row)
        widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return widget

    def _clear_output(self) -> None:
        """清空下半的"输出区"（状态正文 + 详情行 + 滚动位置）。

        每次开始一个新动作（检查 / 下载 / 测试代理）都先清一遍：
        否则上一轮的"发现新版本…"会留在下面，用户分不清哪条是这次的结论；
        滚动位置也一并回到顶部，新文案从第一行开始读。
        """
        self.status_label.clear()
        self.detail_label.clear()
        self.detail_label.setVisible(False)
        self.status_area.verticalScrollBar().setValue(0)

    def _reset_progress(self) -> None:
        """进度条回到空闲态：禁用置灰、显示 0%（占位始终保留）。"""
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setEnabled(False)

    def _show_progress(self) -> None:
        """让进度条进入工作态（控件常驻占位，这里只是解除禁用）。"""
        self.progress.setEnabled(True)

    def _fit_status_height(self) -> None:
        """状态区高度固定 + 窗口最小高度一次算准（表单永不被压扁）。

        为什么需要它：状态文案（"发现新版本"里带更新说明、失败原因）变化时，
        Qt 只在窗口大小不变的假设下重排布局——空间不够时 QFormLayout 会自己
        压缩行高（实测「代理」行被压到 12px）。所以：

        - 状态区高度固定为 :data:`STATUS_AREA_HEIGHT`，文案长了只自己滚动；
        - 进度条常驻占位（见 :meth:`_build_ui`），最小高度里天然含它的位置，
          所以「检查中」只是把它点亮，不会中途多出一个控件来抢高度；
        - 窗口最小高度取布局自己算出来的 ``minimumSize``，再留 8px 余量吸收
          Qt 内部取整误差。
        """
        self.status_area.setFixedHeight(STATUS_AREA_HEIGHT)
        self.setMinimumHeight(self.layout().minimumSize().height() + 8)

    def _load_draft(self) -> None:
        self._select(self.branch_combo, self._draft.branch)
        self._select(self.channel_combo, self._draft.channel)
        self._select(self.speed_combo, self._draft.speed_limit)
        self.proxy_edit.setText(self._draft.proxy)
        self.cdk_edit.setText(cdk_store.load_cdk(self._draft))

    @staticmethod
    def _select(combo, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _update_hint(self) -> None:
        """提示 CDK 存哪、代理/限速对哪些渠道生效（需求 §14/§29/§30）。"""
        from mangaproof.utils.platform import is_android_strict

        parts: list[str] = []
        if is_android_strict():
            parts.append("CDK 保存在应用私有目录的 settings.json。应用的私有目录受安卓沙箱保护。")
        elif cdk_store.keyring_available():
            parts.append("系统凭据库可用, CDK 将保存在系统凭据库（keyring），不写入配置文件。")
        else:
            parts.append("系统凭据库不可用，CDK 将明文保存在 settings.json。")
        parts.append("\n代理与限速仅对 Cloudflare R2 / GitHub 生效；MirrorChyan 不使用它们。")
        self.hint_label.setText("　".join(parts))

    # -- 表单 ↔ draft ------------------------------------------------------

    def _collect_draft(self) -> UpdateSettings:
        self._draft.branch = self.branch_combo.currentData()
        self._draft.channel = self.channel_combo.currentData()
        self._draft.speed_limit = int(self.speed_combo.currentData())
        self._draft.proxy = self.proxy_edit.text().strip()
        return self._draft

    def _commit(self, *, save_cdk: bool = True) -> None:
        """把 draft 提交到真实设置并发信号（需求 §13：保存由两个动作按钮触发）。

        「取消」不调用它 —— 未执行任何动作的修改直接丢弃。
        """
        draft = self._collect_draft()
        if save_cdk:
            # 写入 keyring 成功时 draft.cdk 会被清空（需求 §14）
            cdk_store.save_cdk(draft, self.cdk_edit.text().strip())
        self._settings.update = draft
        self.settings_committed.emit()

    # -- 按钮行为 ----------------------------------------------------------

    def _on_check_clicked(self) -> None:
        """左键：语义永远只有"保存配置 + 检查更新"（需求 §13）。

        不再像旧版那样按状态改文案去兼做下载/安装 —— 那正是"检查完之后改的设置
        既不生效也不落盘"的来源（保存只挂在这一支上，而它随时会变成别的动作）。
        """
        if self._state in ("checking", "downloading"):
            return
        self._start_check()

    def _on_download_clicked(self) -> None:
        """右键：``update_available`` → 下载（先保存配置）；``done`` → 启动安装器。"""
        if self._state == "done" and self._package is not None:
            # 需求 §46：由主窗口负责"启动安装器 → 确认拉起成功 → 主程序退出"；
            # 先发信号（主窗口可能弹错误框），再关闭本对话框。
            self.install_requested.emit((self._package, self._package_sha256))
            self.accept()
            return
        if self._state == "update_available":
            self._start_download()

    def _on_selection_changed(self, *_args) -> None:
        """分支/渠道被改动 → 重新判定右侧按钮（可能失效，也可能改回原值而恢复）。"""
        self._sync_actions()

    # -- 按钮/提示的唯一推导出口 -------------------------------------------

    def _selection_matches_checked(self) -> bool:
        """当前分支/渠道是否仍与"这一轮检查用的"一致。

        包是按检查时的分支与渠道定位的（文件名、R2 前缀、Release 目标都不同），
        所以要改这两项就只能重新检查 —— 绝不能让用户以为在下 beta。
        """
        return (
            self.branch_combo.currentData() == self._checked_branch
            and self.channel_combo.currentData() == self._checked_channel
        )

    def _download_enabled(self) -> bool:
        if self._state == "done":
            return self._package is not None
        if self._state != "update_available" or self._result is None:
            return False
        return self._selection_matches_checked()

    def _download_tooltip(self) -> str:
        if self._state == "checking":
            return "正在检查更新，请稍候"
        if self._state == "downloading":
            return "正在下载更新包，请稍候"
        if self._state == "done":
            return "启动安装器安装已下载的更新包（安装时程序会退出并重启）"
        if self._state == "update_available":
            if self._selection_matches_checked():
                return "下载并校验更新包"
            return STALE_SELECTION_HINT
        return "请先点击「保存并检查更新」"

    def _sync_actions(self) -> None:
        """按当前状态刷新底部两个动作按钮 —— **唯一**改它们文案/可用性的地方。

        集中在这里的原因：旧版散落 7 处 ``setText``（检查/立即更新/立即安装并重启），
        状态一多就出现"按钮停在上一个动作的文案上"这类漂移。
        """
        busy = self._state in ("checking", "downloading")
        done = self._state == "done" and self._package is not None

        # 左键文案恒定；忙碌时两个动作按钮都不可点（取消仍可用）
        self.check_btn.setText(CHECK_BTN_TEXT)
        self.check_btn.setEnabled(not busy)
        self.check_btn.setToolTip(
            "正在忙，请等待当前动作结束" if busy else "保存当前配置并检查更新"
        )

        self.download_btn.setText(INSTALL_BTN_TEXT if done else DOWNLOAD_BTN_TEXT)
        self.download_btn.setEnabled(self._download_enabled())
        self.download_btn.setToolTip(self._download_tooltip())

        self._refresh_stale_hint()

    def _show_hint(self, text: str) -> None:
        """把一条提示写进输出区的详情行（不动状态正文，信息不丢）。"""
        self.detail_label.setText(text)
        self.detail_label.setVisible(True)

    def _refresh_stale_hint(self) -> None:
        """"结果已失效"提示只在需要时出现，改回原值就撤掉。

        判据直接看详情行当前文本，不另存一个布尔量 —— 那一行还可能被下载进度
        （"已下载 / 总量 + 速度"）或 CDK 缺失提示占用，多存一份状态就会不一致。
        """
        stale = (
            self._state == "update_available"
            and self._result is not None
            and not self._selection_matches_checked()
        )
        if stale:
            if self.detail_label.text() != STALE_SELECTION_HINT:
                self._show_hint(STALE_SELECTION_HINT)
        elif self.detail_label.text() == STALE_SELECTION_HINT:
            self.detail_label.clear()
            self.detail_label.setVisible(False)

    def _on_cancel(self) -> None:
        self._orphaned_workers = self._abort_workers()
        self.reject()

    def _abort_workers(self) -> list[object]:
        """请求取消全部后台线程并**安全脱离**，返回仍需托管收尾的线程。

        实测故障：下载途中点取消 → 窗口消失后主界面卡死一小会然后闪退。两个原因：

        1. 原来只对下载线程调 ``request_cancel()``，而取消要等"下一个数据块到达"
           才生效；网络读一旦阻塞在 socket 上，就要等 read 超时（30 秒）。
           期间窗口已经 ``accept()`` 关闭、``exec()`` 返回，模态对话框随即析构——
           作为其子对象的 QThread 仍在运行 → Qt 报
           ``QThread: Destroyed while thread is still running`` 并**直接 abort**
           （闪退）。
        2. 唯一被 ``wait()`` 等待的是代理测试线程，而且固定等 2 秒 ——
           主线程被堵住，就是"卡死一小会"。

        所以这里改成：**只发取消、短暂等一下、等不到就脱离**。脱离后的线程交给
        主窗口托管（:meth:`MainWindow._adopt_update_workers`），在后台自然收尾；
        信号已被 ``disown()`` 摘掉，不会再有回调去碰已析构的窗口。
        """
        pending: list[object] = []
        for worker in (self._check_worker, self._download_worker, self._proxy_worker):
            if worker is None or not worker.isRunning():
                continue
            worker.request_cancel()
            # 只有"一定能很快退出"的线程值得等：下载线程可能阻塞在 socket 读上
            if worker.wait(_ABORT_WAIT_MS):
                continue
            worker.disown()
            pending.append(worker)
        return pending

    # -- 检查更新 ----------------------------------------------------------

    def _start_check(self) -> None:
        self._commit()                       # 需求 §13：保存当前配置后再检查

        self._state = "checking"
        self._result = None
        # 新一轮检查 → 上一轮下载的包作废：否则「安装更新」会留着指向旧包，
        # 用户在"又查了一次、这次没更新"之后仍可能点到它（误装旧版本）。
        self._package = None
        self._package_sha256 = ""
        # 记下本轮用的分支/渠道：之后被改动即判为"结果失效"，见 _refresh_stale_hint
        self._checked_branch = self._draft.branch
        self._checked_channel = self._draft.channel
        self._set_form_enabled(False)
        self._clear_output()
        self.cancel_btn.setText("取消")      # 下载完成后这里是「稍后」，新一轮要还原
        self.status_label.setText("正在检查更新……")
        self.progress.setRange(0, 0)         # 需求 §31：不确定进度条
        self._show_progress()
        self._sync_actions()

        worker = UpdateCheckWorker(
            branch=self._draft.branch,
            source=self._draft.channel,
            proxy=self._draft.proxy,
            parent=self,
        )
        worker.progress.connect(self.status_label.setText)
        worker.succeeded.connect(self._on_check_ok)
        worker.failed.connect(self._on_check_failed)
        self._check_worker = worker
        worker.start()

    def _on_check_ok(self, outcome: CheckOutcome) -> None:
        self._reset_progress()
        self._set_form_enabled(True)
        self._outcome = outcome

        if outcome.kind == "unsupported":
            self._state = "no_update"
            self.status_label.setText(outcome.message)
            self._sync_actions()
            return

        result = outcome.result
        self._result = result
        if not result.has_update:
            # 需求 §32
            self._state = "no_update"
            self.status_label.setText(
                "当前已经是最新版本\n\n"
                f"当前版本：{result.current_display}\n"
                f"更新分支：{result.branch}"
            )
            self._sync_actions()
            return

        # 需求 §33：不自动下载，等用户点「下载更新」
        self._state = "update_available"
        size_text = human_size(outcome.filesize) if outcome.filesize else (
            outcome.size_note or "—"
        )
        note = f"\n\n更新说明：\n{result.release.release_note}" if result.release.release_note else ""
        self.status_label.setText(
            "发现新版本\n\n"
            f"当前版本：{result.current_display}\n"
            f"最新版本：{result.latest_display}\n\n"
            f"分支：{result.branch}\n\n"
            f"文件：\n{outcome.filename or '—'}\n\n"
            f"大小：\n{size_text}{note}"
        )
        self._sync_actions()

    def _on_check_failed(self, error: object) -> None:
        self._reset_progress()
        self._set_form_enabled(True)
        self._state = "checked"
        self._result = None
        self.status_label.setText(self._error_text(error))
        self._sync_actions()

    # -- 下载 --------------------------------------------------------------

    def _start_download(self) -> None:
        if self._result is None:
            return
        # 守卫 1：分支/渠道被改过 → 查到的包已不对应，要求重查（按钮此刻也是灰的，
        # 这里再挡一次是为了防"信号/时序"绕过，绝不发错包的请求）。
        if not self._selection_matches_checked():
            self._refresh_stale_hint()
            self._sync_actions()
            return
        # 守卫 2：MirrorChyan 渠道必须先有 CDK（需求 §11.8/§18）。放在最前面**且不落盘**：
        # 被拒绝的操作不该产生副作用；底层为空的措辞是内部术语
        # （"CDK 为空：不得发起 MirrorChyan 下载信息请求"），不能原样丢给用户。
        if (
            self.channel_combo.currentData() == "mirrorchyan"
            and not self.cdk_edit.text().strip()
        ):
            self._show_hint(MIRRORCHYAN_NEEDS_CDK)
            return
        # 需求 §13 的补强：下载前把"用户此刻看到的配置"提交并落盘。
        # 旧版不提交 —— 检查完之后改的代理/限速既不下发也不保存，改了等于没改。
        self._commit()

        from mangaproof.update import detector

        target = detector.current_target()
        if target is None:
            self.status_label.setText("当前系统架构暂不支持更新")
            return

        self._state = "downloading"
        self._set_form_enabled(False)
        self._clear_output()
        self.status_label.setText("正在准备下载……")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._show_progress()
        self._sync_actions()

        worker = UpdateDownloadWorker(
            branch=self._draft.branch,
            source=self._draft.channel,
            release=self._result.release,
            target=target,
            dest_dir=self._download_dir(),
            proxy=self._draft.proxy,
            speed_limit_mbps=self._draft.speed_limit,
            cdk=self.cdk_edit.text().strip(),
            parent=self,
        )
        worker.progress.connect(self._on_download_progress)
        worker.succeeded.connect(self._on_download_ok)
        worker.failed.connect(self._on_download_failed)
        self._download_worker = worker
        worker.start()

    def _download_dir(self) -> Path:
        """下载目录（需求 §36/§37）：Windows 用 TEMP，Linux/macOS 用 CACHE。"""
        from mangaproof.update.platform_dirs import update_package_dir

        return update_package_dir()

    def _on_download_progress(
        self, done: int, total: object, speed: object, phase: str
    ) -> None:
        self.status_label.setText(f"{phase}…" if phase.endswith("…") is False else phase)
        total_int = int(total) if isinstance(total, int) and total > 0 else None
        if total_int:
            percent = min(100, int(done * 100 / total_int))
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        else:
            self.progress.setRange(0, 0)     # 需求 §34：无 Content-Length 用不确定进度
        parts = [f"{human_size(done)} / {human_size(total_int) if total_int else '未知'}"]
        if isinstance(speed, (int, float)) and speed:
            parts.append(f"速度：{human_speed(float(speed))}")
        # 详情行是"已下载 / 总量 + 速度"的唯一去处，必须显式可见：
        # _clear_output() 在下载开始时会把它藏起来（避免上一轮的残留），
        # 这里只 setText 不 setVisible 的话进度数字就永远看不到了。
        self.detail_label.setVisible(True)
        self.detail_label.setText("　".join(parts))

    def _on_download_ok(self, path: object) -> None:
        from mangaproof.update import checksum

        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._state = "done"
        self._package = Path(str(path))
        # 这里算出的 SHA-256 不只是给界面看：点「安装更新」时会作为
        # --sha256 传给安装器，让它在替换文件前再校验一次（需求 §53）。
        digest = checksum.sha256_of(self._package)
        self._package_sha256 = digest
        self.status_label.setText(
            "更新包已下载并通过校验\n\n"
            f"文件：{self._package.name}\n"
            f"SHA-256：{digest}\n\n"
            "点击「安装更新」将启动安装器：程序会退出并在安装完成后重启。"
        )
        self.detail_label.setText(str(self._package))
        self.detail_label.setVisible(True)
        self._set_form_enabled(False)
        self.cancel_btn.setText("稍后")
        self._sync_actions()

    def _on_download_failed(self, error: object) -> None:
        from mangaproof.update.downloader import DownloadCancelled

        self._reset_progress()
        self._set_form_enabled(True)
        self.detail_label.setVisible(False)
        self._state = "update_available"
        if isinstance(error, DownloadCancelled):
            self.status_label.setText("已取消下载。")
        else:
            self.status_label.setText(self._error_text(error))
        self._sync_actions()

    # -- 代理测试 ----------------------------------------------------------

    def _on_test_proxy(self) -> None:
        proxy = self.proxy_edit.text().strip()
        self.proxy_test_btn.setEnabled(False)
        self.proxy_test_btn.setText("测试中…")
        # 输出区只留本次结果：把上一轮的结论/进度先清掉
        self._clear_output()
        self.status_label.setText("正在测试代理……")
        worker = ProxyTestWorker(proxy=proxy, parent=self)
        worker.finished_with.connect(self._on_proxy_result)
        self._proxy_worker = worker
        worker.start()

    def _on_proxy_result(self, ok: bool, message: str) -> None:
        self.proxy_test_btn.setEnabled(True)
        self.proxy_test_btn.setText("测试代理")
        self.status_label.setText(("✓ " if ok else "✗ ") + message)

    # -- 辅助 --------------------------------------------------------------

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (
            self.branch_combo, self.channel_combo, self.cdk_edit,
            self.proxy_edit, self.proxy_test_btn, self.speed_combo,
        ):
            widget.setEnabled(enabled)

    def output_text(self) -> str:
        """输出区当前全文（供测试断言，也方便日志排查）。"""
        parts = [self.status_label.text()]
        if self.detail_label.isVisible() and self.detail_label.text():
            parts.append(self.detail_label.text())
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _error_text(error: object) -> str:
        if isinstance(error, UpdateError):
            return f"更新失败：\n{error}"
        return f"更新失败：\n{error}"

    def closeEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        self._orphaned_workers = self._abort_workers()
        super().closeEvent(event)

    # -- 供主窗口调用 ------------------------------------------------------

    def take_package(self) -> Path | None:
        """取走已校验的更新包路径（主窗口用它启动安装器）。"""
        return self._package

    def take_package_sha256(self) -> str:
        """取走更新包的 SHA-256（主窗口作为 ``--sha256`` 传给安装器，需求 §53）。

        渠道侧的哈希可能缺失（MirrorChyan 有时不给），但**本地算出来的这个总有**：
        它是"下载到手的字节"的指纹，让安装器在替换文件前再校验一次，
        而不是打一句"未提供 --sha256，本次不做哈希校验"就跳过。
        """
        return self._package_sha256


__all__ = [
    "UpdateDialog",
    "CHANNEL_LABELS",
    "BRANCH_LABELS",
    "CHECK_BTN_TEXT",
    "DOWNLOAD_BTN_TEXT",
    "INSTALL_BTN_TEXT",
    "STALE_SELECTION_HINT",
    "MIRRORCHYAN_NEEDS_CDK",
]
