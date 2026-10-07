# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""「关于 MangaProof」页面（菜单「关于 → 关于 MangaProof」）。

2026-10-07 之前这里是 `QMessageBox.about()`：只能塞一段富文本，网址和
"许可证 / 字体"说明混在一起，长网址还会顶住消息框的宽度（安卓窄屏尤其明显），
也没地方放「复制」。改成独立页面后按行给出四条基本信息：

    项目开源地址 / 官网 / 意见与反馈 / 已有反馈

每行 = 说明 + 可点击链接 + 「复制」；底部**只有「关闭」**——检查更新与两个
许可入口仍留在「关于」菜单里（需求 §10，菜单结构一个动作都不动）。

链接交给 `QLabel.setOpenExternalLinks(True)`：Qt 直接调系统默认浏览器
（安卓走 Intent），与「第三方许可」页的 `QTextBrowser` 做法一致；同时把
交互标志设成 `TextBrowserInteraction`，网址可选中、可右键复制。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mangaproof import APP_NAME, __copyright__, __license__, __version__
from mangaproof.app_info import FEEDBACK_URL, ISSUES_URL, SOURCE_URL, WEBSITE_URL
from mangaproof.ui.theme import COLOR_ACCENT, COLOR_TEXT_DIM

#: 基本信息行：(名称, 链接, 一句说明)
INFO_ROWS: Tuple[Tuple[str, str, str], ...] = (
    ("项目开源地址", SOURCE_URL, "源码与版本发布（Release）都在这里"),
    ("官网", WEBSITE_URL, "下载入口与使用说明"),
    ("意见与反馈", FEEDBACK_URL, "直接开写 GitHub Issue；附上日志或截图更好定位"),
    ("已有反馈", ISSUES_URL, "先看看有没有人提过同样的问题，避免重复提交"),
)

#: 复制成功后按钮文案停留时长（毫秒）
_COPY_FEEDBACK_MS = 1500


class AboutDialog(QDialog):
    """「关于」页：项目标识 + 基本信息（开源地址 / 官网 / 意见与反馈）+ 许可摘要。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        # 小屏（安卓）可按内容自由缩小：宽度取"最长网址 + 复制按钮"的自然宽度，
        # 高度 450 略高于当前内容（~442），多出的余量给不同字体的行高差异
        self.resize(640, 450)

        #: 行名 → 链接标签（测试与后续扩展用；行名见 `INFO_ROWS`）
        self.link_labels: Dict[str, QLabel] = {}
        #: 行名 → 「复制」按钮
        self.copy_buttons: Dict[str, QToolButton] = {}

        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.timeout.connect(self._reset_copy_buttons)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_info_section())
        layout.addWidget(self._build_license_section())
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # 程序没装 Qt 翻译，标准按钮默认是英文 "Close"；这里是全中文界面，显式改中文
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ---- 顶部：图标 + 名称 + 版本 + 一句话 ----

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        icon = self._app_icon()
        self.icon_label: Optional[QLabel] = None
        if not icon.isNull():
            icon_label = QLabel(header)
            icon_label.setPixmap(icon.pixmap(56, 56))
            self.icon_label = icon_label
            row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text = QLabel(header)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setText(
            f"<span style='font-size:18px;'><b>{APP_NAME}</b></span>"
            f" <span style='color:{COLOR_TEXT_DIM};'>v{__version__}</span><br/>"
            "漫画翻译质量检查与返修标注工具<br/>"
            f"<span style='color:{COLOR_TEXT_DIM};'>独立于 Photoshop：不调用 Photoshop API、"
            "不修改 PSD，Original 直接使用 PSD 自带的 merged image。</span>"
        )
        self.header_label = text
        row.addWidget(text, 1)
        return header

    def _app_icon(self) -> QIcon:
        """窗口图标；对话框自身没设图标时退回应用图标（打包后即 exe / APK 图标）。"""
        icon = self.windowIcon()
        if icon.isNull():
            icon = QApplication.windowIcon()
        return icon

    # ---- 基本信息：说明 + 可点击链接 + 复制 ----

    def _build_info_section(self) -> QWidget:
        section = QWidget(self)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_title("基本信息", section))

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        for row, (name, url, hint) in enumerate(INFO_ROWS):
            title = QLabel(f"{name}：", section)
            title.setStyleSheet(f"color: {COLOR_TEXT_DIM};")
            grid.addWidget(title, row, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

            link = QLabel(section)
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            link.setOpenExternalLinks(True)
            link.setWordWrap(True)
            link.setToolTip(f"在系统浏览器中打开 {url}")
            link.setText(
                f"<a style='color:{COLOR_ACCENT};' href='{url}'>{url}</a><br/>"
                f"<span style='color:{COLOR_TEXT_DIM};'>{hint}</span>"
            )
            grid.addWidget(link, row, 1)
            self.link_labels[name] = link

            copy = QToolButton(section)
            copy.setText("复制")
            copy.setToolTip(f"复制「{name}」到剪贴板")
            # 宽度按最长文案（"已复制"）先量好再固定：否则点击瞬间按钮变宽，
            # 链接列会被挤得横向抖一下。量之前必须 ensurePolished()——QSS 的
            # font-size 是 polish 阶段才生效的，之前量到的是另一个字体。
            copy.setText("已复制")
            copy.ensurePolished()
            copy.setFixedWidth(copy.sizeHint().width())
            copy.setText("复制")
            copy.clicked.connect(lambda _checked=False, n=name, u=url: self._copy_link(n, u))
            grid.addWidget(copy, row, 2, Qt.AlignmentFlag.AlignTop)
            self.copy_buttons[name] = copy

        layout.addLayout(grid)
        return section

    def _copy_link(self, name: str, url: str) -> None:
        QApplication.clipboard().setText(url)
        button = self.copy_buttons.get(name)
        if button is not None:
            button.setText("已复制")
        self._copy_reset.start(_COPY_FEEDBACK_MS)

    def _reset_copy_buttons(self) -> None:
        for button in self.copy_buttons.values():
            button.setText("复制")

    # ---- 许可摘要：全文与第三方组件仍走「关于」菜单 ----

    def _build_license_section(self) -> QWidget:
        section = QWidget(self)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_title("许可与致谢", section))

        body = QLabel(section)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setText(
            f"本软件以 <b>{__license__}</b>（GNU GPL v3.0，仅此版本）发布，"
            f"{__copyright__}。<br/>"
            "许可全文见菜单「关于 → 许可证…」（随程序一同分发）；<br/>"
            "本软件使用小米 MiSans 字体，第三方组件与许可证信息见「关于 → 第三方许可」。"
        )
        layout.addWidget(body)
        return section


def _section_title(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(f"<b>{text}</b>")
    return label
