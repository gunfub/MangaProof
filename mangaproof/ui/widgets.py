# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""通用小部件。

- :class:`NoWheelComboBox`：不响应滚轮的下拉框；
- :class:`ToggleSwitch`：胶囊形状的开关（点击切换，滑块只是视觉）。
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QComboBox, QSizePolicy

from mangaproof.ui.theme import (
    COLOR_ACCENT,
    COLOR_BG_HOVER,
    COLOR_BORDER,
    COLOR_TEXT_DIM,
)


class NoWheelComboBox(QComboBox):
    """任何情况下都不响应滚轮的下拉框（聚焦与否都一样）。

    Fusion 风格默认允许滚轮直接改下拉框的值
    （QStyle::SH_ComboBox_AllowWheelScrolling），代价是"想滚页面/顺手划一下
    就改掉了设置，还不知道原来选的是什么"——下拉框里都是语义选项，
    误改后很难察觉，也没有撤销。这里一律忽略滚轮事件：

    - 在滚动区内：事件冒泡给滚动区，滚轮只管滚页面；
    - 不在滚动区内：事件冒泡给父级，没有滚动条时即被丢弃，什么都不会发生。

    改值一律走显式交互：点击展开选择，或聚焦后用键盘
    （↑/↓ 切换、首字母跳转、空格展开）。
    """

    def wheelEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        event.ignore()


# ---------------------------------------------------------------------------
# 胶囊开关
# ---------------------------------------------------------------------------

#: 轨道尺寸与外观（逻辑像素）：轨道 40×20、圆角 6px（不是半圆）；滑块 16×16，
#: 用与轨道**相同**的圆角 6px（圆角方块，不是圆形），风格才统一。
#: 控件尺寸**就是**轨道尺寸——设置行按控件右边缘对齐，多留一圈 padding 会让
#: 开关的可见右边缘比下拉框/按钮缩进几个像素，对不齐。
_SWITCH_TRACK_W = 40
_SWITCH_TRACK_H = 20
_SWITCH_RADIUS = 6
_SWITCH_KNOB = 16
_SWITCH_KNOB_RADIUS = 6

#: 滑块滑动时长（毫秒）：只是视觉过渡，`isChecked()` 立即生效
_SWITCH_ANIM_MS = 120

#: 配色（跟随暗色主题的层级）
_SWITCH_ON = COLOR_ACCENT            # 开：强调色轨道 + 纯白滑块
_SWITCH_ON_HOVER = "#5aa0e9"
_SWITCH_OFF = COLOR_BORDER           # 关：边框色轨道 + 浅灰滑块
_SWITCH_OFF_HOVER = COLOR_BG_HOVER
_SWITCH_DISABLED = "#33363a"         # 禁用：更暗的轨道 + 更暗的滑块
_SWITCH_KNOB_ON = "#ffffff"
_SWITCH_KNOB_OFF = "#c8ccd2"
_SWITCH_KNOB_DISABLED = COLOR_TEXT_DIM


class ToggleSwitch(QAbstractButton):
    """胶囊形状的开关：**点一下就切换**，滑块不跟随鼠标拖动。

    为什么不用 QCheckBox：设置页里「开/关」是一整行的状态，方形勾选框
    和右侧的下拉框放在一起层级不统一；胶囊开关只表达"当前是开还是关"，
    比勾选框更像状态指示。形状按要求做成**圆角矩形轨道**（圆角 6px，
    不是两端半圆的跑道形），滑块是**同圆角的方块**（不是圆形），
    开时滑到右、关时滑到左。

    交互（刻意保持简单）：

    - 点击控件任意位置切换（`QAbstractButton` 的 click 语义），聚焦后按 Space
      同样切换；滑块**不跟随拖动**，按住拖到控件外松手不会切换；
    - 状态与动画解耦：`isChecked()` 在点击当拍就是新值，滑块只是随后用 120ms
      滑到位——不存在"拖到一半/动画没播完"的中间状态；
    - 回车刻意**不**绑定切换：对话框里 Enter 属于「确定」，不能因为焦点在开关上
      就变成切换开关。

    禁用时轨道与滑块一起变暗；配色见上面的模块常量。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._progress = 0.0          # 0 = 滑块在左（关），1 = 在右（开）
        self._hover = False
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(_SWITCH_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_to_state)

    # -- 尺寸 --------------------------------------------------------------

    def sizeHint(self) -> QSize:   # noqa: N802（Qt 命名）
        return QSize(_SWITCH_TRACK_W, _SWITCH_TRACK_H)

    def minimumSizeHint(self) -> QSize:   # noqa: N802（Qt 命名）
        return self.sizeHint()

    # -- 滑块位置（动画属性）----------------------------------------------

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def _animate_to_state(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        # 还没显示（构造 / 复位时 setChecked）不播动画，直接就位
        if not self.isVisible():
            self._anim.stop()
            self._set_progress(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    # -- 悬停 / 绘制 -------------------------------------------------------

    def enterEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:   # noqa: N802（Qt 命名）
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        track = QRectF(0, 0, _SWITCH_TRACK_W, _SWITCH_TRACK_H)

        if not self.isEnabled():
            track_color = QColor(_SWITCH_DISABLED)
            knob_color = QColor(_SWITCH_KNOB_DISABLED)
        elif self.isChecked():
            track_color = QColor(_SWITCH_ON_HOVER if self._hover else _SWITCH_ON)
            knob_color = QColor(_SWITCH_KNOB_ON)
        else:
            track_color = QColor(_SWITCH_OFF_HOVER if self._hover else _SWITCH_OFF)
            knob_color = QColor(_SWITCH_KNOB_OFF)

        painter.setBrush(track_color)
        painter.drawRoundedRect(track, _SWITCH_RADIUS, _SWITCH_RADIUS)

        inset = (_SWITCH_TRACK_H - _SWITCH_KNOB) / 2.0
        x_left = track.x() + inset
        x_right = track.x() + _SWITCH_TRACK_W - inset - _SWITCH_KNOB
        knob_x = x_left + (x_right - x_left) * self._progress
        painter.setBrush(knob_color)
        # 圆角方块：圆角与轨道一致，形状与外壳是一套语言
        painter.drawRoundedRect(
            QRectF(knob_x, track.y() + inset, _SWITCH_KNOB, _SWITCH_KNOB),
            _SWITCH_KNOB_RADIUS,
            _SWITCH_KNOB_RADIUS,
        )

        # 键盘焦点：沿轨道边缘画 1px 描边（开态用白色、关态用强调色，两种底色
        # 上都看得清）。描边画在控件内部，因此不会把可见右边缘顶出去。
        if self.hasFocus():
            pen = QPen(QColor(_SWITCH_KNOB_ON if self.isChecked() else COLOR_ACCENT))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0),
                _SWITCH_RADIUS,
                _SWITCH_RADIUS,
            )
