"""Viewer Canvas（需求 §19、§23、§27、§31、§39）。

- 自维护 Camera（center/zoom），鼠标滚轮上下平移（可在设置中切换为缩放）、
  Ctrl+滚轮缩放、Alt+滚轮左右平移；触控板双指滚双轴平移、捏合缩放；拖拽平移；
- 显示源：merged（PSD 自带 Original）↔ bg（背景图层，需求 §24、§25）；
- Overlay：当前图层视觉边界虚线框、问题红框 + 问题编号、拖框橡皮筋；
- 红框为 Viewer Overlay，绝不写入 PSD（需求 §2.2、§31）。
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QEvent
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from mangaproof.camera.camera import Camera
from mangaproof.camera.centering import layer_visual_bounds, layer_visual_center
from mangaproof.camera.zoom import fit_zoom
from mangaproof.psd.document import PSDDocument
from mangaproof.review.issue import Issue
from mangaproof.ui.theme import (
    COLOR_ACCENT,
    COLOR_CHECKER_A,
    COLOR_CHECKER_B,
    COLOR_FAIL,
)

log = logging.getLogger("mangaproof.ui.viewer")

SOURCE_MERGED = "merged"
SOURCE_BG = "bg"

MIN_RECT_WORLD_SIZE = 3.0     # 拖框最小尺寸（世界像素），过滤误点
CHECKER_SIZE = 16


def smooth_scaling_for(zoom: float) -> bool:
    """缩放插值策略（类 Photoshop）。

    zoom < 1：True（双线性平滑下采样，缩小不丢细节）；
    zoom >= 1：False（最近邻，放大显示清晰像素块）。
    """
    return zoom < 1.0


def numpy_to_qimage(arr) -> Optional[QImage]:
    """RGBA numpy → QImage（深拷贝，独立于 numpy 内存）。

    可在非 GUI 线程调用（QImage 允许跨线程创建，交由主线程使用）。
    """
    if arr is None or arr.size == 0:
        return None
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    qimg = QImage(arr.data, w, h, arr.strides[0], QImage.Format.Format_RGBA8888)
    return qimg.copy()


class ViewerWidget(QWidget):
    # 方式 B：先拖出红框（世界坐标 x, y, w, h）
    rect_drawn = Signal(float, float, float, float)
    # 方式 A：先选问题类型再拖框
    issue_drawn = Signal(str, float, float, float, float)
    camera_changed = Signal()
    pending_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._doc: Optional[PSDDocument] = None
        self._source = SOURCE_MERGED
        self._camera = Camera()
        self._qimages: dict = {}          # (doc_id, source) -> QImage

        self._issues: List[Issue] = []
        self._layer_outline: Optional[Tuple[float, float, float, float]] = None
        self._redraw_mode = False
        self._pending_type: Optional[str] = None
        self._drag: Optional[dict] = None
        self._wheel_mode = "pan"   # 裸滚轮行为："pan" 上下移动 / "zoom" 缩放

    def set_wheel_mode(self, mode: str) -> None:
        """裸滚轮（不按修饰键）行为：'pan' 上下移动 / 'zoom' 缩放。

        触控板双指滚（pixelDelta）不受影响，恒为双轴平移。
        """
        self._wheel_mode = mode if mode in ("pan", "zoom") else "pan"

    # ------------------------------------------------------------------ API

    @property
    def camera(self) -> Camera:
        return self._camera

    @property
    def document(self) -> Optional[PSDDocument]:
        return self._doc

    def set_document(self, doc: Optional[PSDDocument]) -> None:
        if doc is not self._doc:
            self._qimages.clear()
        self._doc = doc
        self.update()

    def set_source(self, source: str) -> None:
        if source not in (SOURCE_MERGED, SOURCE_BG):
            source = SOURCE_MERGED
        self._source = source
        self.update()

    @property
    def source(self) -> str:
        return self._source

    def set_issues(self, issues: List[Issue]) -> None:
        self._issues = list(issues)
        self.update()

    def set_layer_outline(self, rect: Optional[Tuple[float, float, float, float]]) -> None:
        self._layer_outline = rect
        self.update()

    # -- 问题创建模式 -----------------------------------------------------

    @property
    def redraw_mode(self) -> bool:
        return self._redraw_mode

    def set_redraw_mode(self, enabled: bool) -> None:
        self._redraw_mode = bool(enabled)
        if enabled:
            self._pending_type = None
        self.pending_changed.emit()
        self.update()

    @property
    def pending_type(self) -> Optional[str]:
        return self._pending_type

    def set_pending_type(self, issue_type: Optional[str]) -> None:
        self._pending_type = issue_type
        if issue_type is not None:
            self._redraw_mode = False
        self.pending_changed.emit()
        self.update()

    def cancel_pending(self) -> None:
        self._pending_type = None
        self._redraw_mode = False
        self._drag = None
        self.pending_changed.emit()
        self.update()

    def any_issue_mode(self) -> bool:
        return self._redraw_mode or self._pending_type is not None

    # -- 相机定位（需求 §17、§18、§20） ---------------------------------

    def recenter_on_layer(self, info, ratio: float) -> None:
        """图层视觉中心 → 视口中心 + 按比例自动缩放。"""
        if self._doc is None:
            return
        vb = layer_visual_bounds(info)
        center = layer_visual_center(info)
        if center is None:
            center = info.center
        target = vb if vb is not None else info.bounds
        vw, vh = self.width(), self.height()
        new_zoom = fit_zoom(target, (vw, vh), ratio)
        if new_zoom is None:
            # 无法计算（透明图层且 Bounds 不可用）→ 保持 Camera（需求 §18.1）
            log.info("图层 %s 无法自动缩放，保持当前 Camera", info.id)
            return
        self._camera.center_on(*center)
        self._camera.zoom = new_zoom
        self._camera.clamp_zoom()
        self.camera_changed.emit()
        self.update()

    # ------------------------------------------------------------------ 绘制

    def _paint_checkerboard(self, painter: QPainter) -> None:
        w, h = self.width(), self.height()
        for y in range(0, h, CHECKER_SIZE):
            for x in range(0, w, CHECKER_SIZE):
                even = ((x // CHECKER_SIZE) + (y // CHECKER_SIZE)) % 2 == 0
                painter.fillRect(
                    x, y, CHECKER_SIZE, CHECKER_SIZE,
                    QColor(COLOR_CHECKER_A if even else COLOR_CHECKER_B),
                )

    def _qimage(self, source: str) -> Optional[QImage]:
        if self._doc is None:
            return None
        key = (id(self._doc), source)
        qimg = self._qimages.get(key)
        if qimg is not None:
            return qimg
        try:
            if source == SOURCE_MERGED:
                arr = self._doc.merged_np()
            else:
                bg = self._doc.bg_image()
                if bg is None:
                    return None
                arr = bg[2]
        except Exception:
            log.exception("提取显示图像失败（source=%s）", source)
            return None
        qimg = numpy_to_qimage(arr)
        if qimg is None:
            return None
        self._qimages[key] = qimg
        return qimg

    def inject_qimage(self, doc, source: str, qimage: QImage) -> None:
        """注入后台线程预热好的显示图像（免去首帧转换开销）。"""
        self._qimages[(id(doc), source)] = qimage

    def _source_offset(self) -> Tuple[float, float]:
        """bg 图层裁剪像素相对世界坐标的偏移（merged 为 0,0）。"""
        if self._source == SOURCE_BG and self._doc is not None:
            bg = self._doc.bg_image()
            if bg is not None:
                return float(bg[0]), float(bg[1])
        return 0.0, 0.0

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        # 缩放质量策略（类 Photoshop）：
        # - zoom < 100%：双线性平滑，保证缩小时的下采样质量；
        # - zoom >= 100%：最近邻采样，放大显示清晰的像素块，不糊。
        smooth = smooth_scaling_for(self._camera.zoom)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        self._paint_checkerboard(painter)

        if self._doc is None:
            painter.end()
            return

        vw, vh = self.width(), self.height()

        # 显示图像（merged ↔ bg，需求 §23：对比期间仅切换显示源）
        qimg = self._qimage(self._source)
        if qimg is not None:
            ox, oy = self._source_offset()
            w, h = qimg.width(), qimg.height()
            sx0, sy0 = self._camera.world_to_screen(ox, oy, vw, vh)
            sx1, sy1 = self._camera.world_to_screen(ox + w, oy + h, vw, vh)
            if not smooth:
                # 最近邻 + 设备像素取整：像素块边缘锐利、尺寸均匀
                dpr = self.devicePixelRatioF()
                sx0 = round(sx0 * dpr) / dpr
                sy0 = round(sy0 * dpr) / dpr
                sx1 = round(sx1 * dpr) / dpr
                sy1 = round(sy1 * dpr) / dpr
            painter.drawImage(
                QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0),
                qimg,
                QRectF(0, 0, w, h),
            )

        # 当前图层视觉边界虚线框（便于识别当前监制对象）
        if self._layer_outline is not None:
            x, y, w, h = self._layer_outline
            sx0, sy0 = self._camera.world_to_screen(x, y, vw, vh)
            sx1, sy1 = self._camera.world_to_screen(x + w, y + h, vw, vh)
            pen = QPen(QColor(COLOR_ACCENT), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))

        # 问题红框 Overlay（需求 §31、§32、§39：对比期间保持存在）
        for issue in self._issues:
            self._draw_issue_rect(painter, issue, vw, vh)

        # 拖框橡皮筋
        if self._drag and self._drag.get("mode") == "rect":
            self._draw_rubber_band(painter, vw, vh)

        painter.end()

    def _draw_issue_rect(self, painter: QPainter, issue: Issue, vw: int, vh: int) -> None:
        x, y, w, h = issue.rect
        if w <= 0 or h <= 0:
            return
        sx0, sy0 = self._camera.world_to_screen(x, y, vw, vh)
        sx1, sy1 = self._camera.world_to_screen(x + w, y + h, vw, vh)
        pen = QPen(QColor(COLOR_FAIL), 3.0)
        pen.setCosmetic(True)  # 屏幕宽度恒定，不随 zoom 变细
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))

        # 问题编号徽标（红底白字）
        if issue.issue_no > 0:
            label = str(issue.issue_no)
            bx, by = sx0, sy0 - 20.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLOR_FAIL))
            painter.drawRoundedRect(QRectF(bx, by, 22.0, 18.0), 4.0, 4.0)
            painter.setPen(QColor("white"))
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(bx, by, 22.0, 18.0), Qt.AlignmentFlag.AlignCenter, label)

    def _draw_rubber_band(self, painter: QPainter, vw: int, vh: int) -> None:
        drag = self._drag
        x0, y0 = drag["x0"], drag["y0"]
        x1, y1 = drag["x1"], drag["y1"]
        sx0, sy0 = self._camera.world_to_screen(x0, y0, vw, vh)
        sx1, sy1 = self._camera.world_to_screen(x1, y1, vw, vh)
        pen = QPen(QColor(COLOR_FAIL), 2.0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 0, 0, 30))
        painter.drawRect(
            QRectF(min(sx0, sx1), min(sy0, sy1), abs(sx1 - sx0), abs(sy1 - sy0))
        )

    # ------------------------------------------------------------------ 鼠标

    def _world_at(self, pos: QPointF) -> Tuple[float, float]:
        return self._camera.screen_to_world(pos.x(), pos.y(), self.width(), self.height())

    def event(self, event: QEvent) -> bool:
        # 捕捉 macOS 触控板原生手势（如双指捏合缩放）
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                if self._doc is None:
                    return True
                # Mac 的 NativeGesture Zoom 会返回相对缩放增量，比如 0.01 等
                # 我们把它转换为 factor（例如 1.01）传给相机的 zoom_around
                factor = 1.0 + event.value()
                pos = event.position()
                self._camera.zoom_around(pos.x(), pos.y(), self.width(), self.height(), factor)
                self.camera_changed.emit()
                self.update()
                return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        if self._doc is None:
            return
        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton and self.any_issue_mode():
            wx, wy = self._world_at(pos)
            self._drag = {"mode": "rect", "x0": wx, "y0": wy, "x1": wx, "y1": wy}
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag = {
                "mode": "pan",
                "last": pos,
                "cam": (self._camera.center_x, self._camera.center_y),
            }
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        pos = event.position()
        if self._drag["mode"] == "pan":
            last: QPointF = self._drag["last"]
            dx, dy = pos.x() - last.x(), pos.y() - last.y()
            self._camera.pan_by_screen(dx, dy)
            self._drag["last"] = pos
            self.camera_changed.emit()
            self.update()
        elif self._drag["mode"] == "rect":
            wx, wy = self._world_at(pos)
            self._drag["x1"], self._drag["y1"] = wx, wy
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        drag = self._drag
        if drag["mode"] == "rect" and event.button() == Qt.MouseButton.LeftButton:
            x0, y0, x1, y1 = drag["x0"], drag["y0"], drag["x1"], drag["y1"]
            x, y = min(x0, x1), min(y0, y1)
            w, h = abs(x1 - x0), abs(y1 - y0)
            if w >= MIN_RECT_WORLD_SIZE and h >= MIN_RECT_WORLD_SIZE:
                if self._pending_type is not None:
                    issue_type = self._pending_type
                    self._pending_type = None
                    self.issue_drawn.emit(issue_type, x, y, w, h)
                else:
                    self.rect_drawn.emit(x, y, w, h)
                self.pending_changed.emit()
            elif self._pending_type is not None:
                self._pending_type = None
                self.pending_changed.emit()
        self._drag = None
        self.unsetCursor()
        self.update()
        event.accept()

    def wheelEvent(self, event) -> None:
        if self._doc is None:
            return

        pixel_delta = event.pixelDelta()
        angle_delta = event.angleDelta()

        # 模式 1：按住 Ctrl(Win) / Cmd(Mac) + 滚轮 -> 缩放逻辑
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = angle_delta.y()
            if delta != 0:
                factor = 1.25 ** (delta / 120.0)
                pos = event.position()
                self._camera.zoom_around(pos.x(), pos.y(), self.width(), self.height(), factor)
                self.camera_changed.emit()
                self.update()
            event.accept()
            return

        # 模式 2：按住 Alt(Win) / Option(Mac) + 滚轮 -> 左右平移逻辑。
        # 传统鼠标无横向刻度时，纵向刻度映射为横向（Photoshop 行为）。
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            dx = 0.0
            if not pixel_delta.isNull():
                dx = pixel_delta.x()
                if dx == 0:
                    dx = pixel_delta.y()
            elif not angle_delta.isNull():
                dx = angle_delta.x() / 2.0
                if dx == 0:
                    dx = angle_delta.y() / 2.0
            if dx != 0:
                self._camera.pan_by_screen(dx, 0.0)
                self.camera_changed.emit()
                self.update()
            event.accept()
            return

        # 模式 3：不按修饰键。触控板双指滚（pixelDelta）恒为双轴平移；
        # 鼠标滚轮按设置：默认上下平移（wheel_mode="pan"），
        # 可切换为缩放（wheel_mode="zoom"）。
        if not pixel_delta.isNull():
            # Mac 触控板快车道：读取高精度平滑像素偏移
            dx = pixel_delta.x()
            dy = pixel_delta.y()
            if dx != 0 or dy != 0:
                self._camera.pan_by_screen(dx, dy)
                self.camera_changed.emit()
                self.update()
            event.accept()
            return

        if self._wheel_mode == "zoom":
            delta = angle_delta.y()
            if delta != 0:
                factor = 1.25 ** (delta / 120.0)
                pos = event.position()
                self._camera.zoom_around(pos.x(), pos.y(), self.width(), self.height(), factor)
                self.camera_changed.emit()
                self.update()
            event.accept()
            return

        # Win/传统鼠标慢车道：将滚轮的 120 刻度除以 2 转换为柔和的平移像素
        dx = 0.0
        dy = 0.0
        if not angle_delta.isNull():
            dx = angle_delta.x() / 2.0
            dy = angle_delta.y() / 2.0
        if dx != 0 or dy != 0:
            # 复用相机原有的平移接口
            self._camera.pan_by_screen(dx, dy)
            self.camera_changed.emit()
            self.update()

        event.accept()
