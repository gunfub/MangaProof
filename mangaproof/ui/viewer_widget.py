# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

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
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QEvent, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from mangaproof.camera.camera import Camera
from mangaproof.camera.centering import POSITION_VISUAL, position_bounds
from mangaproof.camera.zoom import fit_zoom
from mangaproof.psd.document import PSDDocument
from mangaproof.review.issue import Issue
from mangaproof.ui.split_view import Pane, SplitLayout
from mangaproof.ui.theme import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_CHECKER_A,
    COLOR_CHECKER_B,
    COLOR_FAIL,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
)

log = logging.getLogger("mangaproof.ui.viewer")

SOURCE_MERGED = "merged"
SOURCE_BG = "bg"

MIN_RECT_WORLD_SIZE = 3.0     # 拖框最小尺寸（世界像素），过滤误点
CHECKER_SIZE = 16

#: 问题红框线宽（屏幕像素，cosmetic 画笔）。**没有设置项**，固定值。
#: 蓝色虚线框的线宽上限与之对齐（config/settings.py 的 OUTLINE_WIDTH_MAX，
#: 由 tests/test_outline_width.py 的守卫用例钉住两者相等）。
ISSUE_RECT_PEN_WIDTH = 6.0

#: 蓝色虚线框的线宽（屏幕像素，cosmetic 画笔）。Viewer 自身保持历史默认，
#: 应用级默认值在 config/settings.py（DEFAULT_OUTLINE_WIDTH），由主窗口下发。
DEFAULT_OUTLINE_PEN_WIDTH = 1.0


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


def outline_screen_rect(camera, outline, vw: int, vh: int) -> Optional[QRectF]:
    """图层视觉边界（世界坐标 LTRB）→ 屏幕矩形；退化/无效返回 None。

    单独抽出便于测试：世界坐标 (left, top, right, bottom) 必须分别映射
    两个角点后再求宽高，不能把 right/bottom 直接当宽高用。
    """
    if outline is None:
        return None
    left, top, right, bottom = (float(v) for v in outline)
    if right <= left or bottom <= top:
        return None
    x0, y0 = camera.world_to_screen(left, top, vw, vh)
    x1, y1 = camera.world_to_screen(right, bottom, vw, vh)
    return QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()


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

        # 左右并排（2026-10，第二种对比形态）：固定 50/50 + 4px 分隔。
        # 它是**显示模式**而不是对比开关：模式由设置 compare_form 决定，
        # 这里只保存当前是否分栏；没有"进入/退出"的瞬态。
        self._split_mode = False
        self._split_layout = SplitLayout()
        # 分屏角标：切到分屏 / 首个任务时显示 3 秒；无任务时常显（Q3/Q5）
        self._labels_visible = False
        self._labels_timer = QTimer(self)
        self._labels_timer.setSingleShot(True)
        self._labels_timer.timeout.connect(self._on_labels_timeout)

        self._issues: List[Issue] = []
        self._layer_outline: Optional[Tuple[float, float, float, float]] = None
        # 蓝框线宽（屏幕像素）：由主窗口按设置下发，见 set_outline_pen_width
        self._outline_width: float = DEFAULT_OUTLINE_PEN_WIDTH
        self._redraw_mode = False
        self._pending_type: Optional[str] = None
        self._drag: Optional[dict] = None
        self._wheel_mode = "pan"   # 裸滚轮行为："pan" 上下移动 / "zoom" 缩放
        self._empty_hint = "未打开任务"   # 未打开/已关闭任务时的画布提示
        # 分屏时每栏更窄：主窗口给一份"每个快捷键一行"的版式，避免在词中间折行
        self._split_empty_hint = ""

    def set_wheel_mode(self, mode: str) -> None:
        """裸滚轮（不按修饰键）行为：'pan' 上下移动 / 'zoom' 缩放。

        触控板双指滚（pixelDelta）不受影响，恒为双轴平移。
        """
        self._wheel_mode = mode if mode in ("pan", "zoom") else "pan"

    def set_empty_hint(self, text: str, split_text: Optional[str] = None) -> None:
        """无文档时画布中央的提示文案（由主窗口按当前快捷键绑定生成）。

        ``split_text``：分屏时每栏各画一份。半栏更窄，主窗口会把两个快捷键
        拆成两行，避免 Qt 在"打开漫画文件夹"这种长词中间折行。
        """
        self._empty_hint = text
        self._split_empty_hint = text if split_text is None else split_text
        if self._doc is None:
            self.update()

    @property
    def empty_hint(self) -> str:
        """当前空画布提示（仅供测试/调试）。"""
        return self._empty_hint

    @property
    def split_empty_hint(self) -> str:
        """分屏空画布提示（仅供测试/调试）。"""
        return self._split_empty_hint

    # -- 左右并排（第二种对比形态） ---------------------------------------

    def set_split_mode(self, enabled: bool) -> None:
        """切换左右并排显示模式。

        - 开启时若已有文档：角标显示 3 秒（Q3：设置页切到并排并保存后）；
          若还没有文档：角标常显（Q5），等首个文档打开后再转为 3 秒计时；
        - 关闭时：停计时、强制回 merged（分屏期间源切换不影响绘制，
          退出分屏必须回到单栏的成品图）。
        """
        enabled = bool(enabled)
        if enabled == self._split_mode:
            return
        self._split_mode = enabled
        if enabled:
            if self._doc is not None:
                self.show_labels(3000)
            else:
                self._labels_timer.stop()
                self._labels_visible = False
        else:
            self._labels_timer.stop()
            self._labels_visible = False
            self._source = SOURCE_MERGED
        self.update()

    @property
    def split_mode(self) -> bool:
        return self._split_mode

    def show_labels(self, ms: int = 3000) -> None:
        """让「原图 / 成品图」角标再显示 ``ms`` 毫秒（仅分屏生效）。"""
        self._labels_visible = True
        self._labels_timer.start(max(1, int(ms)))
        self.update()

    def _on_labels_timeout(self) -> None:
        self._labels_visible = False
        self.update()

    @property
    def labels_visible(self) -> bool:
        """角标当前是否可见：无任务时常显，有任务时看 3 秒计时。"""
        if not self._split_mode:
            return False
        return self._doc is None or self._labels_visible

    # ------------------------------------------------------------------ API

    @property
    def camera(self) -> Camera:
        return self._camera

    @property
    def document(self) -> Optional[PSDDocument]:
        return self._doc

    def set_document(self, doc: Optional[PSDDocument]) -> None:
        had_doc = self._doc is not None
        if doc is not self._doc:
            self._qimages.clear()
        self._doc = doc
        if doc is None:
            # 回到无任务：角标转为常显（Q5），计时停掉
            self._labels_timer.stop()
            self._labels_visible = False
        elif not had_doc and self._split_mode:
            # 无任务 → 首个任务：角标显示 3 秒（Q3）；同任务内换文件不重复显示
            self.show_labels(3000)
        self.update()

    def set_source(self, source: str) -> None:
        if source not in (SOURCE_MERGED, SOURCE_BG):
            source = SOURCE_MERGED
        self._source = source
        # 分屏时两栏各自固定源，_source 只留给单栏/闪切；无需重绘
        if not self._split_mode:
            self.update()

    @property
    def source(self) -> str:
        return self._source

    def set_issues(self, issues: List[Issue]) -> None:
        self._issues = list(issues)
        self.update()

    def set_layer_outline(self, rect: Optional[Tuple[float, float, float, float]]) -> None:
        """设置当前图层视觉边界虚线框。

        rect 为世界坐标 (left, top, right, bottom)——与
        camera.centering.layer_visual_bounds() 的返回、LayerInfo.bounds
        同约定（历史缺陷：本方法收到的 LTRB 曾被 paintEvent 当作
        (x, y, w, h) 解包，导致左上角正确、右下角画到 left+right/top+bottom）。
        None 表示不显示。
        """
        self._layer_outline = rect
        self.update()

    @property
    def layer_outline(self) -> Optional[Tuple[float, float, float, float]]:
        """当前虚线框（世界坐标 LTRB），仅供测试/调试。"""
        return self._layer_outline

    def set_outline_pen_width(self, width: float) -> None:
        """设置蓝色虚线框的线宽（屏幕像素，cosmetic 画笔）。

        应用级取值与默认值在 config/settings.py（1～6px，默认 3px），
        主窗口启动与设置应用时下发；这里只做下限兜底，避免 0/负数画笔。
        """
        try:
            value = float(width)
        except (TypeError, ValueError):
            value = DEFAULT_OUTLINE_PEN_WIDTH
        self._outline_width = value if value >= 1.0 else DEFAULT_OUTLINE_PEN_WIDTH
        self.update()

    @property
    def outline_pen_width(self) -> float:
        """当前蓝框线宽（屏幕像素），仅供测试/调试。"""
        return self._outline_width

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

    def recenter_on_rect(self, rect, ratio: float) -> None:
        """把世界坐标矩形 (left, top, right, bottom) 居中到视口并按比例缩放。

        **相机写入的唯一入口**：图层定位（:meth:`recenter_on_layer`）与「整页视角」
        （主窗口在「自动定位」关闭时翻页 / 打开文件）都从这里进——两份缩放公式
        各自漂移过一次就很难再对齐。

        视口尺寸随显示模式变化：单栏 = 整个 Viewer，左右并排 = **一栏**。
        缩放统一用 contain（见 :func:`mangaproof.camera.zoom.fit_zoom`），
        分屏窄栏下也能完整装下目标。

        退化矩形、或 fit_zoom 算不出缩放时**保持当前 Camera**（需求 §18.1）。
        """
        if rect is None:
            return
        left, top, right, bottom = (float(v) for v in rect)
        if right <= left or bottom <= top:
            log.info("目标矩形退化 %s，保持当前 Camera", rect)
            return
        new_zoom = fit_zoom(
            (left, top, right, bottom), self._fit_viewport_size(), ratio
        )
        if new_zoom is None:
            log.info("目标矩形 %s 无法自动缩放，保持当前 Camera", rect)
            return
        self._camera.center_on((left + right) / 2.0, (top + bottom) / 2.0)
        self._camera.zoom = new_zoom
        self._camera.clamp_zoom()
        self.camera_changed.emit()
        self.update()

    def recenter_on_layer(self, info, ratio: float, position_mode: str = POSITION_VISUAL) -> None:
        """图层定位 → 视口中心 + 按比例自动缩放。

        目标矩形由 ``position_mode`` 决定（见 camera.centering.position_bounds）：

        - ``visual``（桌面默认）：视觉内容包围盒，解像素、居中更准；
        - ``fast``（Android 默认）：PSD 图层原始框，纯元数据、不解像素。

        两种模式共用同一个相机/缩放公式，只有"矩形从哪来"不同。
        """
        if self._doc is None:
            return
        target = position_bounds(info, position_mode)
        if target is None:
            # 无可用矩形（整层透明且 Bounds 不可用）→ 保持 Camera（需求 §18.1）
            log.info(
                "图层 %s 没有可用的定位矩形（%s 模式），保持当前 Camera",
                info.id,
                position_mode,
            )
            return
        self.recenter_on_rect(target, ratio)

    # ------------------------------------------------------------------ 绘制

    def _fit_viewport_size(self) -> Tuple[int, int]:
        """自动定位 / 整页视角的视口：单栏 = 整窗，左右并排 = 一栏。"""
        if self._split_mode:
            return self._split_layout.fit_size(self.width(), self.height())
        return (self.width(), self.height())

    def _left_source(self) -> str:
        """并排左栏的源：有 bg 用 bg，否则回退 merged。

        关键：这里**不主动触发** ``bg_image()`` 的像素提取。阶段 B 预加载还没
        跑完时在 UI 线程解背景图会卡 0.3～1s；只认"已提取过 / 已注入过"的 bg，
        其余先显示 merged，等 ``_on_preload_done`` 注入后重绘。
        """
        doc = self._doc
        if doc is None:
            return SOURCE_MERGED
        if doc.has_bg():
            return SOURCE_BG if doc.bg_image() is not None else SOURCE_MERGED
        if (id(doc), SOURCE_BG) in self._qimages:
            return SOURCE_BG
        return SOURCE_MERGED

    def _panes(self) -> List[Pane]:
        """当前要绘制的栏。单栏返回整窗 Pane，绘制/命中逻辑无需分支。"""
        if not self._split_mode or self._doc is None:
            return [
                Pane(
                    QRectF(0.0, 0.0, float(self.width()), float(self.height())),
                    self._source,
                    "",
                )
            ]
        return self._split_layout.panes(
            self.width(), self.height(), self._left_source(), SOURCE_MERGED
        )

    def _paint_checkerboard(self, painter: QPainter, w: int, h: int) -> None:
        for y in range(0, h, CHECKER_SIZE):
            for x in range(0, w, CHECKER_SIZE):
                even = ((x // CHECKER_SIZE) + (y // CHECKER_SIZE)) % 2 == 0
                painter.fillRect(
                    x, y, CHECKER_SIZE, CHECKER_SIZE,
                    QColor(COLOR_CHECKER_A if even else COLOR_CHECKER_B),
                )

    def _draw_empty_hint(
        self, painter: QPainter, text: str, w: int, h: int
    ) -> None:
        """未打开 / 已关闭任务：棋盘格上给出下一步指引。

        文案由主窗口按「当前快捷键绑定」生成（set_empty_hint）。分屏时用
        "每个快捷键一行"的版式并允许按栏宽换行，避免半栏里在长词中间折断。
        """
        if not text:
            return
        painter.setPen(QColor(COLOR_TEXT_DIM))
        font = painter.font()
        font.setPointSizeF(max(font.pointSizeF(), 11.0))
        painter.setFont(font)
        painter.drawText(
            QRectF(16.0, 16.0, max(1.0, w - 32.0), max(1.0, h - 32.0)),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            text,
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
        # 分屏左栏可能正在等阶段 B 的 bg：注入后立刻重绘，从 merged 切到原图
        if doc is self._doc and self._split_mode and source == SOURCE_BG:
            self.update()

    def _source_offset(self, source: str) -> Tuple[float, float]:
        """bg 图层裁剪像素相对世界坐标的偏移（merged 为 0,0）。"""
        if source == SOURCE_BG and self._doc is not None:
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

        if self._doc is None:
            self._paint_empty(painter)
            painter.end()
            return

        # 单栏跑 1 次、并排跑 2 次：每栏自带棋盘格 + 图像 + Overlay，
        # 共享同一份 Camera（左右位置、缩放永远一致）。
        for pane in self._panes():
            painter.save()
            painter.setClipRect(pane.rect)      # 越界的图像/橡皮筋不串到另一栏
            painter.translate(pane.rect.topLeft())
            vw, vh = pane.size_px()
            self._paint_checkerboard(painter, vw, vh)
            self._paint_image(painter, pane.source, vw, vh)
            self._paint_overlays(painter, vw, vh)
            painter.restore()

        self._paint_divider(painter)
        self._paint_labels(painter)
        painter.end()

    # -- 分栏绘制 ----------------------------------------------------------

    def _paint_image(self, painter: QPainter, source: str, vw: int, vh: int) -> None:
        """画一栏的显示源（merged / bg），带世界坐标偏移与最近邻取整。"""
        qimg = self._qimage(source)
        if qimg is None:
            return
        ox, oy = self._source_offset(source)
        w, h = qimg.width(), qimg.height()
        sx0, sy0 = self._camera.world_to_screen(ox, oy, vw, vh)
        sx1, sy1 = self._camera.world_to_screen(ox + w, oy + h, vw, vh)
        if not smooth_scaling_for(self._camera.zoom):
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

    def _paint_overlays(self, painter: QPainter, vw: int, vh: int) -> None:
        """一栏的 Overlay：蓝框、问题红框、拖框橡皮筋（两栏都画）。"""
        # 当前图层视觉边界虚线框（便于识别当前监制对象，可在设置中关闭；
        # 线宽 1～6px 可调，见 config/settings.py 的 OUTLINE_WIDTH_CHOICES）
        rect = outline_screen_rect(self._camera, self._layer_outline, vw, vh)
        if rect is not None:
            pen = QPen(QColor(COLOR_ACCENT), self._outline_width, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)   # 屏幕宽度恒定，放大看细节时线不会变粗
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

        # 问题红框 Overlay（需求 §31、§32、§39：对比期间保持存在）
        for issue in self._issues:
            self._draw_issue_rect(painter, issue, vw, vh)

        # 拖框橡皮筋（两栏同一世界坐标，各画一份；越界由栏裁剪）
        if self._drag and self._drag.get("mode") == "rect":
            self._draw_rubber_band(painter, vw, vh)

    def _paint_divider(self, painter: QPainter) -> None:
        """中间 4px 分隔条（空画布也画）。"""
        if not self._split_mode:
            return
        painter.fillRect(
            self._split_layout.divider(self.width(), self.height()),
            QColor(COLOR_BORDER),
        )

    def _paint_labels(self, painter: QPainter) -> None:
        """左上角「原图 / 成品图」角标。

        无任务时常显；有任务时只在切到分屏 / 首个任务打开的 3 秒内显示。
        """
        if not self.labels_visible:
            return
        panes = self._split_layout.panes(
            self.width(), self.height(), SOURCE_MERGED, SOURCE_MERGED
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for pane in panes:
            font = painter.font()
            font.setPointSizeF(10.0)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()
            chip = QRectF(
                pane.rect.x() + 10.0,
                pane.rect.y() + 10.0,
                fm.horizontalAdvance(pane.label) + 18.0,
                24.0,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(43, 45, 48, 215))
            painter.drawRoundedRect(chip, 5.0, 5.0)
            painter.setPen(QColor(COLOR_TEXT))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, pane.label)

    def _paint_empty(self, painter: QPainter) -> None:
        """未打开 / 已关闭任务：单栏一块画布；分屏两块空画布 + 分隔 + 常显角标。"""
        w, h = self.width(), self.height()
        if not self._split_mode:
            self._paint_checkerboard(painter, w, h)
            self._draw_empty_hint(painter, self._empty_hint, w, h)
            return

        hint = self._split_empty_hint or self._empty_hint
        for pane in self._split_layout.panes(w, h, SOURCE_MERGED, SOURCE_MERGED):
            painter.save()
            painter.setClipRect(pane.rect)
            painter.translate(pane.rect.topLeft())
            pw, ph = pane.size_px()
            self._paint_checkerboard(painter, pw, ph)
            self._draw_empty_hint(painter, hint, pw, ph)
            painter.restore()
        self._paint_divider(painter)
        self._paint_labels(painter)

    def _draw_issue_rect(self, painter: QPainter, issue: Issue, vw: int, vh: int) -> None:
        x, y, w, h = issue.rect
        if w <= 0 or h <= 0:
            return
        sx0, sy0 = self._camera.world_to_screen(x, y, vw, vh)
        sx1, sy1 = self._camera.world_to_screen(x + w, y + h, vw, vh)
        pen = QPen(QColor(COLOR_FAIL), ISSUE_RECT_PEN_WIDTH)
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

    def _pane_at(self, pos: QPointF) -> Optional[Pane]:
        """点落在哪一栏；单栏恒为整窗，分屏落进 4px 分隔返回 None。"""
        if self._doc is None:
            return None
        return self._split_layout.pane_at(pos, self._panes())

    def _pane_world(self, pane: Pane, sx: float, sy: float) -> Tuple[float, float]:
        """屏幕坐标（widget 系）→ 世界坐标，按该栏的局部原点与尺寸换算。"""
        local = pane.local_pos(QPointF(sx, sy))
        vw, vh = pane.size_px()
        return self._camera.screen_to_world(local.x(), local.y(), vw, vh)

    def event(self, event: QEvent) -> bool:
        # 捕捉 macOS 触控板原生手势（如双指捏合缩放）
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                if self._doc is None:
                    return True
                # Mac 的 NativeGesture Zoom 会返回相对缩放增量，比如 0.01 等
                # 我们把它转换为 factor（例如 1.01）传给相机的 zoom_around
                factor = 1.0 + event.value()

                # 先取屏幕全局坐标，再映射为画布内部局部坐标
                global_pos = event.globalPosition()
                pos = QPointF(self.mapFromGlobal(global_pos.toPoint()))
                pane = self._pane_at(pos)
                if pane is None:
                    return True
                local = pane.local_pos(pos)
                vw, vh = pane.size_px()
                self._camera.zoom_around(local.x(), local.y(), vw, vh, factor)
                self.camera_changed.emit()
                self.update()
                return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        if self._doc is None:
            return
        pos = event.position()
        pane = self._pane_at(pos)
        if pane is None:          # 4px 分隔条上不开始拖拽
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.any_issue_mode():
            wx, wy = self._pane_world(pane, pos.x(), pos.y())
            self._drag = {
                "mode": "rect", "pane": pane,
                "x0": wx, "y0": wy, "x1": wx, "y1": wy,
            }
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
            pane: Pane = self._drag["pane"]
            # Q9：只把越过中线的 x 截在起手栏内；y 允许越界，由栏裁剪
            sx = min(max(pos.x(), pane.rect.left()), pane.rect.right())
            wx, wy = self._pane_world(pane, sx, pos.y())
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
        # 缩放锚点必须落在某一栏内；平移与栏无关，分隔条上也能继续拖
        pane = self._pane_at(event.position())

        # 模式 1：按住 Ctrl(Win) / Cmd(Mac) + 滚轮 -> 缩放逻辑
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = angle_delta.y()
            if delta != 0 and pane is not None:
                factor = 1.25 ** (delta / 120.0)
                local = pane.local_pos(event.position())
                vw, vh = pane.size_px()
                self._camera.zoom_around(local.x(), local.y(), vw, vh, factor)
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
            if delta != 0 and pane is not None:
                factor = 1.25 ** (delta / 120.0)
                local = pane.local_pos(event.position())
                vw, vh = pane.size_px()
                self._camera.zoom_around(local.x(), local.y(), vw, vh, factor)
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
