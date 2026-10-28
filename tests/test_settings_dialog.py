# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""设置对话框：左侧分类侧边栏 + 跨分类「确定 / 取消」语义。

2026-10-19 起设置页从「一个长滚动页 + 五个 QGroupBox」改成「左侧分类 + 右侧分类页」。
这个模块钉住三类最容易回退的东西：

1. **结构**：五项分类 = 五页，切分类只切显示（右侧只显示该分类的设置）；
   默认窗口横向（宽 > 高，不再是 560×560 的方块），同页设置项行距加大；
2. **滚轮**：所有下拉框都不响应滚轮（只能点开选，这是设计要求），键盘仍可改值；
3. **跨分类保存**：在多个分类里各改几项后点「确定」→ 全部写回；点「取消」（含 X）
   → 全部丢弃，Settings 与打开前完全一致。

第 3 条是这次改造的核心风险：若切页时重建控件、或 apply_to 只读当前页，
先前分类的改动就会静默丢失——用户以为保存了，实际只存了最后一页。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_settings_dialog.py -v
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QLineEdit

from mangaproof.config.settings import DEFAULT_WHEEL_MODE, Settings, SettingsManager
from mangaproof.ui.settings_dialog import (
    DEFAULT_DIALOG_SIZE,
    GUTTER,
    MINIMUM_DIALOG_SIZE,
    NAV_WIDTH,
    PAGES,
    ROW_LABEL_GAP,
    ROW_SPACING,
    SCROLL_GAP,
    KeybindingsDialog,
    SettingsDialog,
    SettingsRow,
)
from mangaproof.ui.widgets import NoWheelComboBox, ToggleSwitch

#: 每个分类「有代表性」的一个控件：用来验证切页后右侧只有该页的设置
_PAGE_WIDGETS = {
    "display": "ratio_combo",
    "compare": "compare_mode_combo",
    "task": "memory_policy_combo",
    "report": "report_name_edit",
    "shortcut": "wheel_mode_combo",
}

#: 分类键的顺序 = 侧边栏顺序
_KEYS = list(PAGES)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dialog(settings: Settings | None = None, intro: str = "") -> SettingsDialog:
    return SettingsDialog(settings if settings is not None else Settings(), None, intro=intro)


def _wheel(dy: int = -120) -> QWheelEvent:
    p = QPointF(10.0, 10.0)
    return QWheelEvent(
        p, p, QPoint(0, 0), QPoint(0, dy),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def _go(dlg: SettingsDialog, key: str) -> None:
    """点侧边栏切到某个分类。"""
    dlg.nav_list.setCurrentRow(_KEYS.index(key))
    QApplication.processEvents()


# --------------------------------------------------------------------------- #
# ① 结构：分类 = 侧边栏 = 页
# --------------------------------------------------------------------------- #


def test_sidebar_titles_are_product_copy():
    """侧边栏分类名是产品文案：改名前先确认需求方是否要一起改测试。"""
    assert [title for title, _ in PAGES.values()] == [
        "缩放与标注显示",
        "对比呈现",
        "扫描与运行策略",
        "返修单参数",
        "快捷键与滚轮",
    ]


def test_sidebar_categories_match_pages(qapp):
    dlg = _dialog()
    try:
        assert len(PAGES) == 5
        assert dlg.nav_list.count() == len(PAGES)
        assert dlg.stack.count() == len(PAGES)
        assert [dlg.nav_list.item(i).text() for i in range(len(PAGES))] == [
            title for title, _ in PAGES.values()
        ]
        # 侧边栏项带着分类键，顺序与 PAGES 一致
        assert [
            dlg.nav_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(len(PAGES))
        ] == _KEYS
        # 默认停在第一页
        assert dlg.nav_list.currentRow() == 0
        assert dlg.stack.currentIndex() == 0
        # 每个分类键都有页面 / 行容器 / 滚动区
        for key in _KEYS:
            assert key in dlg.page_widgets
            assert key in dlg.page_rows
            assert key in dlg.page_scrolls
            assert dlg.page_widgets[key].isAncestorOf(dlg.page_scrolls[key])
    finally:
        dlg.deleteLater()


def test_switching_category_shows_only_that_page(qapp):
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        for row, key in enumerate(_KEYS):
            _go(dlg, key)
            assert dlg.stack.currentIndex() == row
            assert dlg.stack.currentWidget() is dlg.page_widgets[key]
            # 本页的控件可见、其它页的控件不可见
            for other, attr in _PAGE_WIDGETS.items():
                widget = getattr(dlg, attr)
                assert dlg.page_widgets[other].isAncestorOf(widget), (key, other)
                assert widget.isVisibleTo(dlg) is (other == key), (key, other)
    finally:
        dlg.close()
        dlg.deleteLater()


def test_sidebar_wheel_does_not_switch_page(qapp):
    """侧边栏滚轮只可能滚动列表，不能顺手切页（更不能改任何设置值）。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        before_page = dlg.stack.currentIndex()
        before_row = dlg.nav_list.currentRow()
        for dy in (-120, 120):
            QApplication.sendEvent(dlg.nav_list, _wheel(dy))
            assert dlg.nav_list.currentRow() == before_row
            assert dlg.stack.currentIndex() == before_page
    finally:
        dlg.close()
        dlg.deleteLater()


def test_window_is_landscape_and_fits_768p(qapp):
    """默认窗口不再是方的：宽 > 高；最小尺寸仍能塞进 768p 笔记本。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        assert (dlg.width(), dlg.height()) == DEFAULT_DIALOG_SIZE
        assert dlg.width() > dlg.height() * 1.3, "设置窗口应明显横向"
        assert dlg.minimumWidth() == MINIMUM_DIALOG_SIZE[0]
        assert dlg.minimumHeight() == MINIMUM_DIALOG_SIZE[1]
        # 768p（可用高度约 728）以内：最小高度必须留得住底部按钮
        assert MINIMUM_DIALOG_SIZE[1] <= 480
        # 侧边栏固定宽，内容区拿走剩下的宽度
        assert dlg.nav_list.minimumWidth() == dlg.nav_list.maximumWidth() == NAV_WIDTH
        assert dlg.nav_list.width() == NAV_WIDTH
        assert dlg.stack.width() > dlg.nav_list.width() * 3
        assert dlg.button_box.isVisibleTo(dlg)
        assert not dlg.nav_list.isAncestorOf(dlg.button_box)
    finally:
        dlg.close()
        dlg.deleteLater()


def test_default_size_keeps_the_43_28_aspect_ratio(qapp):
    """窗口默认尺寸的宽高比是**硬约束**（43:28 = 860:560）：改尺寸必须横竖等比。

    2026-10 给显示页加「自动定位与缩放」一行时，560 高的视口比内容少 4px 会冒出
    滚动条，于是等比放大一档到 903×588 —— 不是把高度单独拉长。
    """
    width, height = DEFAULT_DIALOG_SIZE
    assert Fraction(width, height) == Fraction(43, 28), (
        f"默认窗口 {width}×{height} 破坏了 43:28 的宽高比"
    )
    # 等比放大后仍明显横向、且比旧尺寸大（否则改尺寸没意义）
    assert width > height * 1.3
    assert width >= 860 and height >= 560


def test_rows_are_more_spacious_than_before(qapp):
    """同一分类内的设置项行距加大（原来 QFormLayout 默认约 6px）。"""
    dlg = _dialog()
    try:
        for key, rows in dlg.page_rows.items():
            assert rows.spacing() == ROW_SPACING, key
            assert rows.spacing() >= 12, key
    finally:
        dlg.deleteLater()


def _page_row_widgets(dlg: SettingsDialog, key: str) -> list[SettingsRow]:
    """某分类页里的所有设置行（末尾的 stretch 不算）。"""
    rows = dlg.page_rows[key]
    widgets = [rows.itemAt(i).widget() for i in range(rows.count())]
    return [w for w in widgets if w is not None]


def test_row_layout_label_left_control_right(qapp):
    """每行文本贴左；下拉框紧跟文本、占满到右缘；开关贴右缘。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        combo_widths = set()
        for key in _KEYS:
            _go(dlg, key)      # 隐藏页不参与布局，切过去才有几何
            qapp.processEvents()
            rows = _page_row_widgets(dlg, key)
            assert rows, key
            assert len({row.width() for row in rows}) == 1, key
            for row in rows:
                assert isinstance(row, SettingsRow), key
                assert row.label.geometry().x() == 0, (key, row.label.text())
                assert row.control.geometry().right() == row.rect().right(), (
                    key, row.label.text(),
                )
                if isinstance(row.control, (NoWheelComboBox, QLineEdit)):
                    # 填充式：紧跟文本之后 ROW_LABEL_GAP 处开始
                    assert row.control.geometry().x() == (
                        row.label.geometry().right() + 1 + ROW_LABEL_GAP
                    ), (key, row.label.text())
                    if isinstance(row.control, NoWheelComboBox):
                        combo_widths.add(row.control.width())
                else:
                    # 右对齐式（开关 / 按钮）：保持自身宽度
                    assert row.control.width() == row.control.sizeHint().width()
        # 每行文本长度不同 → 下拉框左缘不同 → 宽度不止一种
        assert len(combo_widths) >= 4, combo_widths
    finally:
        dlg.close()
        dlg.deleteLater()


def test_gutter_baseline_and_button_alignment(qapp):
    """统一基线 GUTTER：四边留白、侧边栏/恢复默认、内容文本、底部按钮都用它。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        margins = dlg.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            GUTTER, GUTTER, GUTTER, GUTTER,
        )
        # 左侧：侧边栏与「恢复默认设置」都从基线处开始
        assert dlg.nav_list.geometry().x() == GUTTER
        assert dlg.reset_btn.mapTo(dlg, QPoint(0, 0)).x() == GUTTER
        # 侧边栏右缘 → 内容文本 = 基线
        title = dlg.page_titles["display"]
        assert title.mapTo(dlg, QPoint(0, 0)).x() == (
            dlg.nav_list.geometry().right() + 1 + GUTTER
        )
        # 右侧：下拉框与底部按钮共用同一条右缘（窗口宽 - 基线）
        _go(dlg, "display")
        baseline = dlg.width() - GUTTER
        assert dlg.ratio_combo.mapTo(
            dlg, QPoint(dlg.ratio_combo.width(), 0)
        ).x() == baseline
        assert dlg.button_box.mapTo(
            dlg, QPoint(dlg.button_box.width(), 0)
        ).x() == baseline
    finally:
        dlg.close()
        dlg.deleteLater()


def test_header_matches_first_nav_item(qapp):
    """侧边栏文字与内容页标题同格式（15px 粗体），且文字顶端对齐。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        nav = dlg.nav_list
        title = dlg.page_titles["display"]
        for font in (nav.font(), title.font()):
            assert font.pixelSize() == 15, font.pixelSize()
            assert font.bold() is True
        item_rect = nav.visualItemRect(nav.item(0))
        assert title.height() == item_rect.height()
        nav_text_top = nav.viewport().mapTo(
            dlg, item_rect.topLeft()
        ).y() + (item_rect.height() - nav.fontMetrics().height()) // 2
        title_text_top = title.mapTo(dlg, QPoint(0, 0)).y() + (
            title.height() - title.fontMetrics().height()
        ) // 2
        assert abs(nav_text_top - title_text_top) <= 1, (
            nav_text_top, title_text_top,
        )
    finally:
        dlg.close()
        dlg.deleteLater()


def test_boolean_settings_are_pill_switches(qapp):
    """8 个布尔设置全部是胶囊开关；文本在行标签上，开关本身不显示文字。"""
    dlg = _dialog()
    try:
        attrs = (
            "auto_locate_check",
            "bg_ratio_check",
            "layer_outline_check",
            "recursive_check",
            "console_check",
            "pdf_check",
            "report_options_check",
            "report_hide_clean_check",
        )
        for attr in attrs:
            widget = getattr(dlg, attr)
            assert isinstance(widget, ToggleSwitch), attr
            assert widget.text() == "", attr
            row = widget.parentWidget()
            assert isinstance(row, SettingsRow), attr
            assert row.label.text(), attr
            assert widget.accessibleName() == row.label.text(), attr
    finally:
        dlg.deleteLater()


def test_clicking_switch_row_updates_setting(qapp):
    """点行最右边的开关 → 状态改变，确定后写回该设置。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        _go(dlg, "display")
        before = dlg.layer_outline_check.isChecked()
        QTest.mouseClick(
            dlg.layer_outline_check,
            Qt.MouseButton.LeftButton,
            pos=dlg.layer_outline_check.rect().center(),
        )
        assert dlg.layer_outline_check.isChecked() is (not before)
        out = Settings()
        dlg.apply_to(out)
        assert out.show_layer_outline is (not before)
    finally:
        dlg.close()
        dlg.deleteLater()


def test_disabled_control_greys_its_row_label(qapp):
    """控件置灰时，该行文本跟着置灰（不能只有下拉框是灰的）。"""
    dlg = _dialog()
    try:
        def row_of(control) -> SettingsRow:
            row = control.parentWidget()
            assert isinstance(row, SettingsRow)
            return row

        # JPEG 质量：默认 PNG → 整行置灰；切到 JPEG 恢复
        assert not dlg.report_quality_combo.isEnabled()
        assert not row_of(dlg.report_quality_combo).label.isEnabled()
        dlg.report_image_combo.setCurrentIndex(
            dlg.report_image_combo.findData("jpeg")
        )
        assert dlg.report_quality_combo.isEnabled()
        assert row_of(dlg.report_quality_combo).label.isEnabled()

        # bg 独立比例：默认关 → 两个比例行整行置灰；打开开关即恢复
        assert not dlg.bg_ratio_combo.isEnabled()
        assert not row_of(dlg.bg_ratio_combo).label.isEnabled()
        assert not row_of(dlg.bg_copy_ratio_combo).label.isEnabled()
        dlg.bg_ratio_check.setChecked(True)
        assert row_of(dlg.bg_ratio_combo).label.isEnabled()
        assert row_of(dlg.bg_copy_ratio_combo).label.isEnabled()

        # 手动对比 → 切换速度整行置灰
        dlg.compare_mode_combo.setCurrentIndex(
            dlg.compare_mode_combo.findData("manual")
        )
        assert not dlg.compare_speed_combo.isEnabled()
        assert not row_of(dlg.compare_speed_combo).label.isEnabled()
    finally:
        dlg.deleteLater()


def test_report_quality_combo_offers_low_tiers(qapp):
    """JPEG 质量档位扩到 40～95：新档只有数值，80 保留默认标记。"""
    dlg = _dialog()
    try:
        combo = dlg.report_quality_combo
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "40%", "50%", "60%", "70%", "80%（默认）", "90%", "95%",
        ]
        combo.setCurrentIndex(combo.findData(40))
        out = Settings()
        dlg.apply_to(out)
        assert out.report_jpeg_quality == 40
    finally:
        dlg.deleteLater()


def test_report_quality_combo_falls_back_to_default(qapp):
    """直接构造的非档位值（55）在下拉框里回落默认 80，不显示成 40。"""
    settings = Settings()
    settings.report_jpeg_quality = 55
    dlg = SettingsDialog(settings)
    try:
        assert dlg.report_quality_combo.currentData() == 80
    finally:
        dlg.deleteLater()


def test_field_column_aligns_to_shortest_combo(qapp):
    """同一页的下拉/输入框左缘统一，且 = 本页最长行文本 + 间距。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        for key in _KEYS:
            _go(dlg, key)
            qapp.processEvents()
            rows = [
                row for row in _page_row_widgets(dlg, key)
                if isinstance(row.control, (NoWheelComboBox, QLineEdit))
            ]
            if len(rows) < 2:      # 只有一行时谈不上"对齐"
                continue
            xs = {row.control.geometry().x() for row in rows}
            assert len(xs) == 1, (key, xs)
            widest = max(row.label.sizeHint().width() for row in rows)
            assert xs.pop() == widest + ROW_LABEL_GAP, key
    finally:
        dlg.close()
        dlg.deleteLater()


def _right(widget, ref) -> int:
    """控件右缘在 ref 坐标系里的 x（右缘是开区间，所以正好等于基线值）。"""
    return widget.mapTo(ref, QPoint(widget.width(), 0)).x()


def test_divider_between_hint_and_rows(qapp):
    """说明与设置项之间有一条 1px 分割线，上下留空都等于设置项行距。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        for key in _KEYS:
            _go(dlg, key)
            qapp.processEvents()
            hint = dlg.page_hints[key]
            divider = dlg.page_dividers[key]
            scroll = dlg.page_scrolls[key]
            assert divider.height() == 1, key
            above = divider.mapTo(dlg, QPoint(0, 0)).y() - (
                hint.mapTo(dlg, QPoint(0, 0)).y() + hint.height()
            )
            below = scroll.mapTo(dlg, QPoint(0, 0)).y() - (
                divider.mapTo(dlg, QPoint(0, 0)).y() + divider.height()
            )
            assert above == ROW_SPACING, (key, above)
            assert below == ROW_SPACING, (key, below)
    finally:
        dlg.close()
        dlg.deleteLater()


def test_header_stays_fixed_while_scrolling(qapp):
    """标题 / 说明 / 分割线在滚动时不动（它们在滚动区之外）。"""
    dlg = _dialog()
    dlg.setMinimumSize(0, 0)
    dlg.show()
    dlg.resize(720, 300)
    qapp.processEvents()
    qapp.processEvents()
    try:
        _go(dlg, "display")
        qapp.processEvents()
        fixeds = [
            dlg.page_titles["display"],
            dlg.page_hints["display"],
            dlg.page_dividers["display"],
        ]
        before = [w.mapTo(dlg, QPoint(0, 0)).y() for w in fixeds]
        bar = dlg.page_scrolls["display"].verticalScrollBar()
        assert bar.maximum() > 0, "压矮后应可滚动"
        bar.setValue(bar.maximum())
        qapp.processEvents()
        after = [w.mapTo(dlg, QPoint(0, 0)).y() for w in fixeds]
        assert after == before
    finally:
        dlg.close()
        dlg.deleteLater()


def test_scrollbar_sits_on_baseline_and_controls_keep_gap(qapp):
    """滚动条右缘压基线；控件右缘让到滚动条左侧 SCROLL_GAP；无滚动条时回基线。"""
    dlg = _dialog()
    dlg.setMinimumSize(0, 0)
    dlg.show()
    qapp.processEvents()
    try:
        _go(dlg, "display")
        qapp.processEvents()
        scroll = dlg.page_scrolls["display"]
        bar = scroll.verticalScrollBar()

        # 1) 不滚动：滚动条隐藏，控件压基线
        assert not bar.isVisible()
        assert _right(dlg.ratio_combo, dlg) == dlg.width() - GUTTER

        # 2) 压矮：滚动条出现在最右（右缘 = 基线），控件让出 6px
        dlg.resize(720, 300)
        qapp.processEvents()
        qapp.processEvents()
        assert bar.isVisible()
        baseline = dlg.width() - GUTTER
        assert _right(bar, dlg) == baseline
        assert _right(dlg.ratio_combo, dlg) == _right(bar, dlg) - SCROLL_GAP - bar.width()
        assert bar.mapTo(dlg, QPoint(0, 0)).x() - _right(dlg.ratio_combo, dlg) == SCROLL_GAP

        # 3) 恢复高度：滚动条隐藏，控件回到基线
        dlg.resize(*DEFAULT_DIALOG_SIZE)
        qapp.processEvents()
        qapp.processEvents()
        assert not bar.isVisible()
        assert _right(dlg.ratio_combo, dlg) == dlg.width() - GUTTER
    finally:
        dlg.close()
        dlg.deleteLater()


def test_ok_cancel_buttons_are_chinese(qapp):
    """程序没装 Qt 翻译，标准按钮默认是英文；这里必须显式中文。"""
    dlg = _dialog()
    kb = KeybindingsDialog(Settings())
    try:
        assert dlg.button_box.button(QDialogButtonBox.StandardButton.Ok).text() == "确定"
        assert dlg.button_box.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消"
        assert kb.button_box.button(QDialogButtonBox.StandardButton.Ok).text() == "确定"
        assert kb.button_box.button(QDialogButtonBox.StandardButton.Cancel).text() == "取消"
    finally:
        dlg.deleteLater()
        kb.deleteLater()


# --------------------------------------------------------------------------- #
# ② 滚轮：下拉只能点开选（键盘仍可用）
# --------------------------------------------------------------------------- #


def test_every_combo_ignores_wheel(qapp):
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        combos = dlg.findChildren(NoWheelComboBox)
        assert len(combos) >= 9, [c.objectName() for c in combos]
        for combo in combos:
            before = combo.currentIndex()
            for dy in (-120, 120):
                ev = _wheel(dy)
                QApplication.sendEvent(combo, ev)
                if combo.isEnabled():
                    # 忽略滚轮 → 事件继续冒泡给滚动区（滚页面，而不是改值）
                    assert not ev.isAccepted(), f"{combo.currentText()} 不该吞滚轮"
                assert combo.currentIndex() == before, (combo.currentText(), dy)
    finally:
        dlg.close()
        dlg.deleteLater()

    # 对照：原生 QComboBox 会被滚轮改值（说明禁用确有作用）
    plain = QComboBox()
    plain.addItems(["a", "b", "c"])
    ev = _wheel()
    QApplication.sendEvent(plain, ev)
    assert ev.isAccepted() and plain.currentIndex() == 1


def test_keyboard_still_changes_combo(qapp):
    """禁用滚轮不等于禁用控件：点开后用键盘仍可正常改值。"""
    dlg = _dialog()
    dlg.show()
    qapp.processEvents()
    try:
        combo = dlg.issue_scope_combo
        combo.setFocus()
        qapp.processEvents()
        before = combo.currentIndex()
        QApplication.sendEvent(
            combo,
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier
            ),
        )
        qapp.processEvents()
        assert combo.currentIndex() != before
    finally:
        dlg.close()
        dlg.deleteLater()


# --------------------------------------------------------------------------- #
# ③ 跨分类「确定 / 取消」
# --------------------------------------------------------------------------- #


def test_cross_category_edits_all_applied_on_ok(qapp):
    """在三个分类里各改几项 → 确定：全部写回（不是只保存当前显示的那一页）。"""
    out = Settings()
    dlg = _dialog(Settings())
    try:
        # 显示：自动显示比例 60% → 40%
        dlg.ratio_combo.setCurrentIndex(dlg.ratio_combo.findData(0.4))
        # 返修单：填名称、关掉完成后自动生成
        _go(dlg, "report")
        dlg.report_name_edit.setText("第 5 话 返修")
        dlg.pdf_check.setChecked(False)
        # 快捷键与滚轮：裸滚轮改为缩放
        _go(dlg, "shortcut")
        dlg.wheel_mode_combo.setCurrentIndex(dlg.wheel_mode_combo.findData("zoom"))
        # 再切回显示页：先前改的值还在（切页不重建控件）
        _go(dlg, "display")
        assert dlg.ratio_combo.currentData() == 0.4

        dlg.apply_to(out)
        assert out.layer_display_ratio == 0.4
        assert out.report_name == "第 5 话 返修"
        assert out.generate_pdf_on_complete is False
        assert out.wheel_mode == "zoom"
    finally:
        dlg.deleteLater()


def test_cancel_discards_edits_from_every_category(qapp):
    """取消（含点 X）＝ 什么都没发生：Settings 与打开设置前逐字段一致。"""
    settings = Settings()
    before = copy.deepcopy(settings)
    dlg = _dialog(settings)
    try:
        dlg.ratio_combo.setCurrentIndex(dlg.ratio_combo.findData(0.4))
        _go(dlg, "report")
        dlg.report_name_edit.setText("不该保存")
        dlg.pdf_check.setChecked(False)
        _go(dlg, "shortcut")
        dlg.wheel_mode_combo.setCurrentIndex(dlg.wheel_mode_combo.findData("zoom"))
        assert settings == before, "改控件期间不允许碰 Settings"

        dlg.reject()
        assert dlg.result() == SettingsDialog.DialogCode.Rejected
        assert settings == before

        # 重新打开：显示的是原设置（用户视角就是"没改过"）
        again = _dialog(settings)
        try:
            assert again.report_name_edit.text() == ""
            assert again.pdf_check.isChecked() is True
            assert again.wheel_mode_combo.currentData() == DEFAULT_WHEEL_MODE
            assert again.ratio_combo.currentData() == before.layer_display_ratio
        finally:
            again.deleteLater()
    finally:
        dlg.deleteLater()


def test_keybindings_subdialog_discarded_by_cancel(qapp):
    """子对话框里改的快捷键，主对话框取消时同样不生效。"""
    settings = Settings()
    dlg = _dialog(settings)
    try:
        kb = KeybindingsDialog(settings)
        kb._core_edits["prev_psd"].setKeySequence(QKeySequence("F1"))
        dlg._kb_dialog = kb
        dlg.reject()
        assert settings.keybindings["prev_psd"] == "Up"
    finally:
        dlg.deleteLater()


def test_main_window_ok_saves_every_category(qapp):
    """端到端：经 MainWindow 打开设置 → 跨分类改 → 确定：运行时生效且落盘。"""
    from mangaproof.ui.main_window import MainWindow

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        window = MainWindow(SettingsManager(path))
        window.show()
        qapp.processEvents()
        try:
            def fake_exec(dialog):          # 模拟用户操作后点「确定」
                dialog.ratio_combo.setCurrentIndex(
                    dialog.ratio_combo.findData(0.4)
                )
                dialog.nav_list.setCurrentRow(_KEYS.index("report"))
                QApplication.processEvents()
                dialog.report_name_edit.setText("跨分类")
                dialog.pdf_check.setChecked(False)
                return SettingsDialog.DialogCode.Accepted

            with patch.object(SettingsDialog, "exec", fake_exec):
                window.open_settings_dialog()

            assert window.settings.layer_display_ratio == 0.4
            assert window.settings.report_name == "跨分类"
            assert window.settings.generate_pdf_on_complete is False
            # 界面热应用：工具栏比例下拉跟随
            assert window.ratio_combo.currentData() == 0.4
            # 落盘：两个分类的改动都在
            saved = json.loads(path.read_text(encoding="utf-8"))
            assert saved["layer_display_ratio"] == 0.4
            assert saved["report_name"] == "跨分类"
            assert saved["generate_pdf_on_complete"] is False
        finally:
            window.close()
            qapp.processEvents()


# --------------------------------------------------------------------------- #
# ④ 首次使用引导条（可选）不影响分类页
# --------------------------------------------------------------------------- #


def test_intro_sits_above_categories(qapp):
    dlg = _dialog(intro="首次使用：左侧是分类。")
    dlg.show()
    qapp.processEvents()
    try:
        assert dlg.intro_label is not None
        assert dlg.intro_label.isVisibleTo(dlg)
        assert not dlg.intro_label.isAncestorOf(dlg.nav_list)
        assert dlg.nav_list.isVisibleTo(dlg)
        assert dlg.stack.count() == len(PAGES)
    finally:
        dlg.close()
        dlg.deleteLater()
