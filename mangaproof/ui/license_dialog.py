# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""许可相关对话框（与「关于」分离）。

- `LicenseDialog`：**第三方组件**许可。左侧组件列表，右侧显示该组件的版本、
  SPDX 许可证标识、版权声明、主页与许可证全文/摘要（格式参考 Chromium
  chrome://credits、Flutter LicenseRegistry、VS Code Third Party Notices 等惯例）。
- `AppLicenseDialog`：**本项目自身**的许可（GPL-3.0-only）全文，内容来自随包
  分发的 `LICENSE`——GPLv3 §6 要求分发目标码时随附一份本许可副本。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mangaproof import APP_NAME, __copyright__, __license__, __version__
from mangaproof.app_license import LICENSE_FILE, license_text
from mangaproof.third_party import ThirdPartyItem, build_third_party_items


class LicenseDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("第三方许可")
        self.resize(860, 560)

        self._items = build_third_party_items()

        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.component_list = QListWidget()
        for item in self._items:
            list_item = QListWidgetItem(f"{item.name}（{item.spdx}）")
            list_item.setData(Qt.ItemDataRole.UserRole, item.name)
            self.component_list.addItem(list_item)
        self.detail_view = QTextBrowser()
        self.detail_view.setOpenExternalLinks(True)
        splitter.addWidget(self.component_list)
        splitter.addWidget(self.detail_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([280, 560])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # 与「关于」页一致：没装 Qt 翻译时标准按钮默认英文 "Close"，显式改中文
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.component_list.currentRowChanged.connect(self._show_item)
        if self._items:
            self.component_list.setCurrentRow(0)

    def _show_item(self, row: int) -> None:
        if not (0 <= row < len(self._items)):
            return
        item: ThirdPartyItem = self._items[row]
        text = (
            f"<h2>{item.name}</h2>"
            f"<p><b>版本：</b>{item.version}<br/>"
            f"<b>许可证：</b>{item.spdx}<br/>"
            f"<b>版权：</b>{item.copyright}<br/>"
            f"<b>主页：</b><a href='{item.homepage}'>{item.homepage}</a></p>"
            f"<hr/>"
            f"<pre style='white-space: pre-wrap;'>{item.license_text}</pre>"
        )
        self.detail_view.setHtml(text)


class AppLicenseDialog(QDialog):
    """本项目自身许可（GPL-3.0-only）全文。

    文本是内置常量（`app_license.license_text()`，与仓库根 `LICENSE` 同源），
    与「第三方许可」页一样不读磁盘：任何产物形态都能离线查看，也就满足了
    GPLv3 §6"随目标码给接收者一份本许可副本"的要求。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("许可证")
        self.resize(760, 560)

        layout = QVBoxLayout(self)

        header = QLabel(self)
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setText(
            f"<b>{APP_NAME} {__version__}</b> — {__license__}<br/>"
            f"{__copyright__}<br/>"
            f"<span style='color:#9aa0a6;'>GNU General Public License v3.0 全文。</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self.text_view = QTextBrowser(self)
        self.text_view.setOpenExternalLinks(True)
        self.text_view.setPlainText(license_text())
        layout.addWidget(self.text_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # 与「关于」页一致：没装 Qt 翻译时标准按钮默认英文 "Close"，显式改中文
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
