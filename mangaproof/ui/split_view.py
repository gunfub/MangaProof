# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""左右并排对比的纯几何（2026-10，第二种对比形态）。

这里只回答"两栏怎么切、每栏是哪张图、点落在哪一栏"，不持有 QWidget 状态，
方便不开窗口直接单测；绘制与事件仍留在 :class:`mangaproof.ui.viewer_widget.ViewerWidget`。

约定（需求方 2026-10 拍板）：

- **固定 50/50**，不做可拖动分隔条：两栏视口同宽，同一世界坐标在左右栏的
  局部坐标完全一致，图层坐标/滚轮锚点/拖框都直接复用同一套换算；
- 中间 **4px** 分隔条，颜色走主题 ``COLOR_BORDER``；
- 左栏 = **原图**（背景图层，``SOURCE_BG``）；右栏 = **成品图**
  （PSD 自带 merged / 需求文档里的 Original，``SOURCE_MERGED``）。

术语对照（**不要混**）：

- 界面「原图」= ``bg`` 背景图层（未嵌字）；
- 界面「成品图」= merged / Original（已嵌字的成品）；
- 需求文档里的 "Original" = 成品图，不是界面上的「原图」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF

#: 分隔条宽度（屏幕像素）
GAP_PX = 4

#: 两栏角标文案（Q10 统一用词）
LEFT_LABEL = "原图"
RIGHT_LABEL = "成品图"


@dataclass(frozen=True)
class Pane:
    """一栏：屏幕矩形 + 该栏显示的源 + 角标文案。"""

    rect: QRectF
    source: str
    label: str

    def contains(self, pos: QPointF) -> bool:
        # 半开区间 [left, right)：QRectF.contains 会把右/下边缘也算进去，
        # 那样 4px 分隔条的第一列会被误判成左栏；绘制同样是半开覆盖。
        rect = self.rect
        return (
            rect.left() <= pos.x() < rect.right()
            and rect.top() <= pos.y() < rect.bottom()
        )

    def local_pos(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x() - self.rect.x(), pos.y() - self.rect.y())

    def size_px(self) -> Tuple[int, int]:
        """栏宽高（整数像素），用于世界坐标换算。"""
        return int(round(self.rect.width())), int(round(self.rect.height()))


class SplitLayout:
    """固定 50/50 的两栏几何。

    只依赖宽高，不依赖 QWidget / Camera；``width`` 为奇数时右栏多 1px
    （左栏是较小的一侧，适配尺寸统一取左栏，保证两栏都装得下）。
    """

    def __init__(self, gap: int = GAP_PX) -> None:
        self._gap = max(0, int(gap))

    @property
    def gap(self) -> int:
        return self._gap

    # -- 几何 --------------------------------------------------------------

    def _left_width(self, width: int) -> int:
        width = max(1, int(width))
        return max(1, (width - self._gap) // 2)

    def panes(
        self,
        width: int,
        height: int,
        left_source: str,
        right_source: str,
    ) -> List[Pane]:
        """两栏矩形。

        左栏 ``[0, lw)``、分隔条 ``[lw, lw+gap)``、右栏 ``[lw+gap, width)``；
        总宽不足时（极窄窗口）仍保证每栏至少 1px，不做上下堆叠。
        """
        width = max(1, int(width))
        height = max(1, int(height))
        lw = self._left_width(width)
        rw = max(1, width - self._gap - lw)
        return [
            Pane(QRectF(0.0, 0.0, float(lw), float(height)), left_source, LEFT_LABEL),
            Pane(
                QRectF(float(lw + self._gap), 0.0, float(rw), float(height)),
                right_source,
                RIGHT_LABEL,
            ),
        ]

    def divider(self, width: int, height: int) -> QRectF:
        """中间 4px 分隔条矩形（空画布也要画）。"""
        width = max(1, int(width))
        height = max(1, int(height))
        lw = self._left_width(width)
        return QRectF(float(lw), 0.0, float(self._gap), float(height))

    def fit_size(self, width: int, height: int) -> Tuple[int, int]:
        """自动定位/整页视角使用的视口尺寸：一栏的宽高。"""
        height = max(1, int(height))
        return self._left_width(width), height

    # -- 命中 --------------------------------------------------------------

    @staticmethod
    def pane_at(pos: QPointF, panes: Iterable[Pane]) -> Optional[Pane]:
        """点落在哪一栏；落在 4px 分隔条或窗口外返回 None。"""
        for pane in panes:
            if pane.contains(pos):
                return pane
        return None
