# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""生成返修单选项窗口的排版：胶囊开关、右缘基线、标签列、行距。

2026-10-19 反馈：窗口里的「总览表隐藏无问题的 PSD」还是方形勾选框，而且
QFormLayout 默认横向 / 纵向间距都是 6——「文字→控件」和「控件→控件」看起来
一样宽，和设置页不是一套排版。这个模块钉住改后的三条几何约定：

1. 开关用设置页同款 `ToggleSwitch`（胶囊），不再有 `QCheckBox`；
2. 胶囊右缘 = 名称输入框 / 两个下拉框的右缘（窗口缩放后仍成立）；
   开关文案左缘 = 其余行标签左缘（QFormLayout 的标签列）；
3. 竖向行距 16（设置页 ROW_SPACING）、横向仍是 6；开关行抬到与输入行同高，
   四行文本中心等距（行高 + 16），否则 20px 的开关行会把末档节距压到 41。

另外顺手钉住窗口底部的两个易回退点：按钮中文（生成 / 取消）、状态行语义色
（警告橙 / 完成绿）——状态行属于窗口自己的一块区域，不并入设置项。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_report_dialog_ui.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
)

from mangaproof.ui.dialogs import _ROW_FIELD_GAP, _ROW_SPACING, ReportDialog
from mangaproof.ui.theme import COLOR_PASS, COLOR_WARN, apply_dark_theme
from mangaproof.ui.widgets import ToggleSwitch


@pytest.fixture(scope="module")
def qapp():
    """按生产环境上主题：控件高度（30px）与行距一起决定文本节距。"""
    app = QApplication.instance() or QApplication([])
    apply_dark_theme(app)
    yield app


def _dialog(qapp, *, incomplete: bool = False, completed: bool = False) -> ReportDialog:
    dialog = ReportDialog(
        "chapter01_返修单",
        "chapter01",
        incomplete,
        None,
        completed=completed,
    )
    dialog.show()
    qapp.processEvents()
    return dialog


def _close(dialog: ReportDialog) -> None:
    dialog.close()
    dialog.deleteLater()


def _form(dialog: ReportDialog) -> QFormLayout:
    """窗口主体第一个布局项就是 QFormLayout（名称 / 图像 / 质量 / 开关）。"""
    form = dialog.layout().itemAt(0).layout()
    assert isinstance(form, QFormLayout)
    return form


def _x(widget, root) -> int:
    return widget.mapTo(root, widget.rect().topLeft()).x()


def _right_edge(widget, root) -> int:
    """控件右边界（最后一个像素的右缘 + 1），与设置页用例同一写法。"""
    return widget.mapTo(root, QPoint(widget.width(), 0)).x()


def _center_y(widget, root) -> int:
    return widget.mapTo(root, widget.rect().center()).y()


# --------------------------------------------------------------------------- #
# ① 开关本体：设置页同款胶囊，读写语义不变
# --------------------------------------------------------------------------- #


def test_hide_clean_is_pill_switch(qapp):
    dialog = _dialog(qapp)
    try:
        assert isinstance(dialog.hide_clean_check, ToggleSwitch)
        assert dialog.hide_clean_check.text() == ""
        assert dialog.findChildren(QCheckBox) == [], "方形勾选框应已移除"
        assert dialog.hide_clean_check.accessibleName() == (
            "总览表隐藏无问题的 PSD（全部图层通过）"
        )
        # 行文本挂 tooltip（鼠标停在文字上也能看到说明），文案不再说「勾选」
        assert dialog.hide_clean_row.label.toolTip() == dialog.hide_clean_check.toolTip()
        assert "开启后" in dialog.hide_clean_check.toolTip()
        # 语义不变：默认开，setChecked / isChecked 仍是唯一读写方式
        assert dialog.hide_clean_files() is True
        dialog.hide_clean_check.setChecked(False)
        assert dialog.hide_clean_files() is False
    finally:
        _close(dialog)


# --------------------------------------------------------------------------- #
# ② 右缘基线：胶囊与输入控件压同一条线（缩窗口后仍成立）
# --------------------------------------------------------------------------- #


def test_switch_capsule_right_edge_matches_fields(qapp):
    dialog = _dialog(qapp)
    try:
        fields = (
            dialog.name_edit,
            dialog.image_format_combo,
            dialog.quality_combo,
        )
        baselines = {_right_edge(widget, dialog) for widget in fields}
        assert len(baselines) == 1, baselines
        assert _right_edge(dialog.hide_clean_check, dialog) == baselines.pop()

        for width in (560, 520):
            dialog.resize(width, dialog.height())
            qapp.processEvents()
            assert _right_edge(dialog.hide_clean_check, dialog) == _right_edge(
                dialog.name_edit, dialog
            ), width
    finally:
        _close(dialog)


# --------------------------------------------------------------------------- #
# ③ 文案左缘：与其余行标签同列
# --------------------------------------------------------------------------- #


def test_switch_label_left_aligns_with_form_labels(qapp):
    dialog = _dialog(qapp)
    try:
        form = _form(dialog)
        labels = [
            dialog.hide_clean_row.label,
            form.labelForField(dialog.name_edit),
            form.labelForField(dialog.image_format_combo),
            form.labelForField(dialog.quality_combo),
        ]
        assert all(label is not None for label in labels)
        xs = {_x(label, dialog) for label in labels}
        assert len(xs) == 1, xs
    finally:
        _close(dialog)


# --------------------------------------------------------------------------- #
# ④ 行距与文本节距：竖向 16、横向 6，四行等距
# --------------------------------------------------------------------------- #


def test_row_spacing_and_uniform_text_pitch(qapp):
    dialog = _dialog(qapp)
    try:
        form = _form(dialog)
        assert form.verticalSpacing() == _ROW_SPACING == 16
        assert form.horizontalSpacing() == _ROW_FIELD_GAP == 6

        # 横向仍是 6：文字贴着控件，与 16px 的竖向行距明确区分。
        # 标签列宽取本列最宽的标签——其余标签按各自宽度左对齐，不能拿窄标签量。
        labels = [
            form.labelForField(dialog.name_edit),
            form.labelForField(dialog.image_format_combo),
            form.labelForField(dialog.quality_combo),
        ]
        widest = max(labels, key=lambda label: label.width())
        assert _x(dialog.name_edit, dialog) - (
            _x(widest, dialog) + widest.width()
        ) == _ROW_FIELD_GAP

        # 开关行与输入行同高：否则末行文本中心会比前一行近 10px
        field_height = dialog.name_edit.height()
        assert dialog.image_format_combo.height() == field_height
        assert dialog.quality_combo.height() == field_height
        assert dialog.hide_clean_row.height() == field_height

        centers = [
            _center_y(form.labelForField(dialog.name_edit), dialog),
            _center_y(form.labelForField(dialog.image_format_combo), dialog),
            _center_y(form.labelForField(dialog.quality_combo), dialog),
            _center_y(dialog.hide_clean_row.label, dialog),
        ]
        pitches = [b - a for a, b in zip(centers, centers[1:])]
        expected = field_height + form.verticalSpacing()
        assert pitches == [expected, expected, expected], (centers, pitches)
    finally:
        _close(dialog)


# --------------------------------------------------------------------------- #
# ⑤ 底部：中文按钮 + 状态行语义色 / 独立区域
# --------------------------------------------------------------------------- #


def test_buttons_are_chinese(qapp):
    dialog = _dialog(qapp)
    try:
        ok = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)
        cancel = dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        assert ok.text() == "生成"
        assert cancel.text() == "取消"
    finally:
        _close(dialog)


def test_status_note_keeps_semantic_color_and_stays_separate(qapp):
    warn = _dialog(qapp, incomplete=True)
    try:
        assert COLOR_WARN in warn.note_label.styleSheet()
        # 状态行不在表单里：它自己占窗口下部，不与设置项排在一起
        # （QFormLayout 对不在表里的控件返回 row = -1；role 会回落到 LabelRole）
        row, _role = _form(warn).getWidgetPosition(warn.note_label)
        assert row == -1
    finally:
        _close(warn)

    done = _dialog(qapp, completed=True)
    try:
        assert COLOR_PASS in done.note_label.styleSheet()
    finally:
        _close(done)


# --------------------------------------------------------------------------- #
# ⑥ JPEG 质量档位：新增 40/50，非档位值回落默认 80
# --------------------------------------------------------------------------- #


def test_jpeg_quality_combo_offers_low_tiers(qapp):
    dialog = _dialog(qapp)
    try:
        combo = dialog.quality_combo
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "40%", "50%", "60%", "70%", "80%（默认）", "90%", "95%",
        ]
        assert dialog.jpeg_quality() == 80
        combo.setCurrentIndex(combo.findData(40))
        assert dialog.jpeg_quality() == 40
    finally:
        _close(dialog)


def test_jpeg_quality_prefill_and_fallback(qapp):
    dialog = ReportDialog("n", "task", False, None, jpeg_quality=50)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.jpeg_quality() == 50, "新档位应能预选"
    finally:
        _close(dialog)

    # 非档位值（调用方直接传 55）静默回落默认 80，不落到第一项 40
    dialog = ReportDialog("n", "task", False, None, jpeg_quality=55)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.jpeg_quality() == 80
    finally:
        _close(dialog)
