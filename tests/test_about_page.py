# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""「关于」页：项目开源地址 / 官网 / 意见与反馈（GitHub Issue）。

2026-10-07 起「关于 → 关于 MangaProof」由 `QMessageBox.about()` 改为
`AboutDialog` 页面。本文件守卫四件事：

1. 四条链接与**更新器**用的 `GITHUB_OWNER`/`GITHUB_REPO` 同源——地址写了两处，
   将来改名不会只改一处；
2. 每条链接都是"点开系统浏览器 + 可选中复制"的外链，href 与展示文本一致；
3. 每行的「复制」按钮确实把该网址写进剪贴板；
4. 页面底部**只有「关闭」**（检查更新 / 许可证 / 第三方许可仍在「关于」菜单里，
   菜单结构由 tests/test_android_ui_scale.py 守卫）。

运行：QT_QPA_PLATFORM=offscreen uv run pytest tests/test_about_page.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel

from mangaproof import APP_NAME, __copyright__, __license__, __version__
from mangaproof.app_info import FEEDBACK_URL, ISSUES_URL, SOURCE_URL, WEBSITE_URL
from mangaproof.ui.about_dialog import INFO_ROWS, AboutDialog

app = QApplication.instance() or QApplication([])

#: 行名 → 期望链接（与 INFO_ROWS 逐条对齐）
EXPECTED_LINKS = {
    "项目开源地址": SOURCE_URL,
    "官网": WEBSITE_URL,
    "意见与反馈": FEEDBACK_URL,
    "已有反馈": ISSUES_URL,
}


def test_about_links_share_source_with_updater() -> None:
    """关于页的仓库地址必须与更新器用的 owner/repo 同源（防改名漏改）。"""
    from mangaproof.update.sources import GITHUB_OWNER, GITHUB_REPO

    assert SOURCE_URL == f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
    assert WEBSITE_URL.startswith("https://")
    assert FEEDBACK_URL == f"{SOURCE_URL}/issues/new", "意见与反馈：直接开写新 Issue"
    assert ISSUES_URL == f"{SOURCE_URL}/issues", "已有反馈：Issue 列表"
    print("PASS test_about_links_share_source_with_updater")


def test_about_dialog_shows_project_info() -> None:
    """页面必须给出四条基本信息 + 版本 + 许可标识与入口指引。"""
    dialog = AboutDialog()
    try:
        assert dialog.windowTitle() == f"关于 {APP_NAME}"

        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        for name, url in EXPECTED_LINKS.items():
            assert name in text, f"缺少「{name}」一行"
            assert url in dialog.link_labels[name].text()
        assert __version__ in text, "关于页要显示版本号"
        assert __license__ in text, "关于页要给出许可证标识"
        assert __copyright__ in text, "关于页要给出版权行"
        assert "关于 → 许可证…" in text, "要指明许可全文入口"
        assert "关于 → 第三方许可" in text, "要指明第三方许可入口"
        assert "MiSans" in text, "字体致谢不能丢"
    finally:
        dialog.close()

    # INFO_ROWS 是本文件断言的唯一来源：顺序 / 条数变化必须同时改这里
    assert [name for name, _url, _hint in INFO_ROWS] == list(EXPECTED_LINKS)
    print("PASS test_about_dialog_shows_project_info")


def test_about_links_are_clickable_and_selectable() -> None:
    """四条链接都要：交给系统浏览器打开 + 文本可选中（安卓走 Intent）。"""
    dialog = AboutDialog()
    try:
        for name, url in EXPECTED_LINKS.items():
            label = dialog.link_labels[name]
            assert label.openExternalLinks(), f"{name}：必须由系统浏览器打开"
            flags = label.textInteractionFlags()
            assert flags & Qt.TextInteractionFlag.LinksAccessibleByMouse, f"{name}：链接不可点"
            assert flags & Qt.TextInteractionFlag.TextSelectableByMouse, f"{name}：网址要能选中"
            assert label.toolTip(), f"{name}：鼠标悬停要有提示"
    finally:
        dialog.close()
    print("PASS test_about_links_are_clickable_and_selectable")


def test_about_copy_buttons_fill_clipboard() -> None:
    """「复制」按钮把该行网址写进剪贴板，并给出"已复制"反馈。"""
    dialog = AboutDialog()
    try:
        for name, url in EXPECTED_LINKS.items():
            QApplication.clipboard().setText("")
            dialog.copy_buttons[name].click()
            assert QApplication.clipboard().text() == url, f"{name}：复制内容不对"
            assert dialog.copy_buttons[name].text() == "已复制"
    finally:
        dialog.close()
    print("PASS test_about_copy_buttons_fill_clipboard")


def test_about_dialog_only_has_close_button() -> None:
    """底部只留「关闭」：更新 / 许可入口仍在「关于」菜单（不重复占位）。"""
    dialog = AboutDialog()
    try:
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None, "关于页需要 QDialogButtonBox"
        assert box.standardButtons() == QDialogButtonBox.StandardButton.Close
    finally:
        dialog.close()
    print("PASS test_about_dialog_only_has_close_button")


if __name__ == "__main__":
    test_about_links_share_source_with_updater()
    test_about_dialog_shows_project_info()
    test_about_links_are_clickable_and_selectable()
    test_about_copy_buttons_fill_clipboard()
    test_about_dialog_only_has_close_button()
