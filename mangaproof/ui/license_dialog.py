"""第三方许可对话框（与「关于」分离）。

左侧组件列表，右侧显示该组件的版本、SPDX 许可证标识、版权声明、
主页与许可证全文/摘要（格式参考 Chromium chrome://credits、
Flutter LicenseRegistry、VS Code Third Party Notices 等惯例）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

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
