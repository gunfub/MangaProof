# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""胶囊开关 ToggleSwitch：点击切换（不拖动）、禁用、状态即时、开/关配色。

这个控件是设置页里所有「开 / 关」项的载体，交互上刻意保持最简单：
点任意位置切换一次，滑块只是视觉（120ms 滑动），不跟随鼠标拖动，
所以不会出现"拖到一半"的中间状态。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_toggle_switch.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

from mangaproof.ui.widgets import ToggleSwitch


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _standalone(qapp) -> ToggleSwitch:
    switch = ToggleSwitch()
    switch.resize(switch.sizeHint())
    switch.show()
    qapp.processEvents()
    return switch


def test_size_is_compact_pill(qapp):
    switch = _standalone(qapp)
    try:
        assert switch.sizeHint() == switch.size()
        assert 30 <= switch.width() <= 60
        assert 18 <= switch.height() <= 30
        assert switch.isCheckable() and not switch.isChecked()
    finally:
        switch.close()
        switch.deleteLater()


def test_click_anywhere_toggles(qapp):
    switch = _standalone(qapp)
    try:
        # 点左半边（关着时滑块在这一侧）也能切换，不需要瞄准滑块
        QTest.mouseClick(
            switch, Qt.MouseButton.LeftButton, pos=QPoint(6, switch.height() // 2)
        )
        assert switch.isChecked() is True
        # 点右半边同样切换
        QTest.mouseClick(
            switch,
            Qt.MouseButton.LeftButton,
            pos=QPoint(switch.width() - 6, switch.height() // 2),
        )
        assert switch.isChecked() is False
    finally:
        switch.close()
        switch.deleteLater()


def test_space_key_toggles(qapp):
    switch = _standalone(qapp)
    try:
        switch.setFocus()
        qapp.processEvents()
        QTest.keyClick(switch, Qt.Key.Key_Space)
        assert switch.isChecked() is True
        QTest.keyClick(switch, Qt.Key.Key_Space)
        assert switch.isChecked() is False
    finally:
        switch.close()
        switch.deleteLater()


def test_dragging_does_not_toggle(qapp):
    """按住滑块拖到外面松手：不切换（没有"拖动滑块"这种交互）。"""
    switch = _standalone(qapp)
    try:
        QTest.mousePress(
            switch, Qt.MouseButton.LeftButton, pos=QPoint(8, switch.height() // 2)
        )
        QTest.mouseMove(switch, QPoint(switch.width() + 60, switch.height() + 40))
        QTest.mouseRelease(
            switch,
            Qt.MouseButton.LeftButton,
            pos=QPoint(switch.width() + 60, switch.height() + 40),
        )
        assert switch.isChecked() is False
    finally:
        switch.close()
        switch.deleteLater()


def test_disabled_does_not_toggle(qapp):
    switch = _standalone(qapp)
    try:
        switch.setEnabled(False)
        QTest.mouseClick(
            switch, Qt.MouseButton.LeftButton, pos=switch.rect().center()
        )
        assert switch.isChecked() is False
    finally:
        switch.close()
        switch.deleteLater()


def test_state_is_immediate(qapp):
    """滑块滑动只是视觉：`isChecked()` 在点击当拍就是新状态。"""
    switch = _standalone(qapp)
    try:
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=switch.rect().center())
        assert switch.isChecked() is True     # 不等动画
        switch.setChecked(False)
        assert switch.isChecked() is False    # 程序化设置同样即时
    finally:
        switch.close()
        switch.deleteLater()


def test_on_off_paint_differs(qapp):
    """开/关两态的配色必须不同（轨道强调色 vs 边框色；滑块左右位置不同）。"""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setSpacing(8)
    off = ToggleSwitch()
    on = ToggleSwitch()
    on.setChecked(True)
    lay.addWidget(off)
    lay.addWidget(on)
    host.resize(140, 40)
    host.show()
    qapp.processEvents()
    try:
        off_img = off.grab().toImage()
        on_img = on.grab().toImage()
        y = off_img.height() // 2
        off_left = off_img.pixelColor(10, y)      # 关：这一侧是滑块（浅灰）
        on_left = on_img.pixelColor(10, y)        # 开：这一侧是轨道（强调色）
        assert off_left != on_left
        assert on_left.blue() > on_left.red(), on_left.name()   # 开：蓝色轨道
        # 另一侧相反：关是深色轨道，开是白色滑块
        off_right = off_img.pixelColor(off_img.width() - 10, y)
        on_right = on_img.pixelColor(on_img.width() - 10, y)
        assert off_right != on_right
        assert on_right.lightness() > off_right.lightness(), (on_right.name(), off_right.name())
    finally:
        host.close()
        host.deleteLater()


def test_knob_radius_is_track_radius_minus_gap():
    """内圆角 = 外圆角 − 间隙：两段圆弧同心，拐角处的间隙才与直边一致。

    用相同半径时（6px）：直边间隙 2px、45° 拐角间隙 2.83px，而且圆弧→直边的
    转折点错开 2px，拐角会"张开"——所以这里把它钉成公式，改尺寸时不会忘。
    """
    from mangaproof.ui import widgets

    gap = (widgets._SWITCH_TRACK_H - widgets._SWITCH_KNOB) / 2
    assert widgets._SWITCH_KNOB_GAP == gap
    assert widgets._SWITCH_KNOB_RADIUS == widgets._SWITCH_RADIUS - gap
    assert gap > 0
    assert widgets._SWITCH_KNOB_RADIUS > 0
