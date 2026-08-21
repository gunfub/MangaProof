"""Camera 系统（需求 §19）。

Viewer 自行维护 camera_x / camera_y / zoom，不依赖 Photoshop 坐标系。

约定：
- 世界坐标：PSD World Coordinates（整数像素）；
- 屏幕坐标：Viewport 像素；
- zoom：每世界像素对应的屏幕像素数；
- camera center：位于视口中心的世界坐标点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class Camera:
    center_x: float = 0.0
    center_y: float = 0.0
    zoom: float = 1.0

    MIN_ZOOM = 0.005
    MAX_ZOOM = 64.0

    # -- 变换 --------------------------------------------------------------

    def world_to_screen(self, wx: float, wy: float, vw: float, vh: float) -> Tuple[float, float]:
        return (
            (wx - self.center_x) * self.zoom + vw / 2.0,
            (wy - self.center_y) * self.zoom + vh / 2.0,
        )

    def screen_to_world(self, sx: float, sy: float, vw: float, vh: float) -> Tuple[float, float]:
        return (
            (sx - vw / 2.0) / self.zoom + self.center_x,
            (sy - vh / 2.0) / self.zoom + self.center_y,
        )

    # -- 操作 --------------------------------------------------------------

    def clamp_zoom(self) -> None:
        self.zoom = min(max(self.zoom, self.MIN_ZOOM), self.MAX_ZOOM)

    def zoom_around(self, sx: float, sy: float, vw: float, vh: float, factor: float) -> None:
        """以屏幕点 (sx, sy) 为锚点缩放（需求 §27 滚轮缩放）。"""
        wx, wy = self.screen_to_world(sx, sy, vw, vh)
        self.zoom *= factor
        self.clamp_zoom()
        self.center_x = wx - (sx - vw / 2.0) / self.zoom
        self.center_y = wy - (sy - vh / 2.0) / self.zoom

    def pan_by_screen(self, dx: float, dy: float) -> None:
        """按屏幕像素平移（需求 §27 鼠标拖拽平移）。"""
        self.center_x -= dx / self.zoom
        self.center_y -= dy / self.zoom

    def center_on(self, wx: float, wy: float) -> None:
        self.center_x = float(wx)
        self.center_y = float(wy)
