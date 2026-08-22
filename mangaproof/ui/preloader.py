"""后台 PSD 预加载线程（需求：切换大 PSD 不卡顿）。

两阶段调度（关键优化：切换文件只等 merged，不被背景图/图层提取拖慢）：

- 阶段 A（preload_merged）：按队列顺序依次只提取各文件的 merged image，
  快速铺开「切换可用」的覆盖面——文件切换的关键路径；
- 阶段 B（preload_extra）：补提取背景图（自动对比用）与目标图层像素
  /视觉边界（定位缩放用），优先级低于阶段 A；
- 前台请求（open）：merged + 目标图层（不含背景图），永远排在队列最前；
- 快速切换：新 open 请求替换未处理的旧请求，set_preloads 整体替换
  两阶段队列；正在执行的任务无法中断、结果仅写入缓存无害。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

log = logging.getLogger("mangaproof.ui.preloader")

KIND_OPEN = "open"              # 前台打开请求：merged + 目标图层
KIND_PRELOAD = "preload"        # 阶段 A 完成：merged 已就绪
KIND_EXTRA = "extra"            # 阶段 B 完成：背景图 + 图层像素已就绪
WARM_ALL = "*"                  # 阶段 B 的 layer_id 哨兵：预热全部图层


class PreloadWorker(QThread):
    """单线程后台加载器（队列 + 条件变量）。

    doc_provider(rel) -> PSDDocument 或 None（由主窗口提供，只读取
    已创建的文档对象，不在后台创建新文档）。
    """

    task_done = Signal(str, str, bool, object)   # rel, kind, ok, images{merged|bg: QImage|None}

    def __init__(self, doc_provider: Callable[[str], Optional[object]], parent=None):
        super().__init__(parent)
        self._doc_provider = doc_provider
        self._cond = threading.Condition()
        self._open_job: Optional[Tuple[str, str]] = None   # (rel, layer_id)
        self._merged_jobs: List[Tuple[str, str]] = []      # 阶段 A
        self._extra_jobs: List[Tuple[str, str]] = []       # 阶段 B
        self._stop = False

    # -- 主线程 API --------------------------------------------------------

    def submit_open(self, rel: str, layer_id: str = "") -> None:
        """请求打开某个文件（merged + 目标图层），替换未处理的旧请求。"""
        with self._cond:
            self._open_job = (rel, layer_id)
            self._cond.notify_all()

    def cancel_open(self) -> None:
        with self._cond:
            self._open_job = None

    def set_preloads(
        self,
        merged_jobs: List[Tuple[str, str]],
        extra_jobs: List[Tuple[str, str]],
    ) -> None:
        """整体替换两阶段队列（快速切换时旧邻域作废）。"""
        with self._cond:
            self._merged_jobs = list(merged_jobs)
            self._extra_jobs = list(extra_jobs)
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    # -- 线程主体 ----------------------------------------------------------

    def run(self) -> None:
        while True:
            with self._cond:
                while (
                    not self._stop
                    and self._open_job is None
                    and not self._merged_jobs
                    and not self._extra_jobs
                ):
                    self._cond.wait()
                if self._stop:
                    return
                # 优先级：open > 阶段 A（merged）> 阶段 B（bg/图层）
                if self._open_job is not None:
                    rel, layer_id = self._open_job
                    self._open_job = None
                    kind = KIND_OPEN
                elif self._merged_jobs:
                    rel, layer_id = self._merged_jobs.pop(0)
                    kind = KIND_PRELOAD
                else:
                    rel, layer_id = self._extra_jobs.pop(0)
                    kind = KIND_EXTRA
            ok, images = self._process(rel, kind, layer_id)
            self.task_done.emit(rel, kind, ok, images)

    def _process(self, rel: str, kind: str, layer_id: str) -> tuple:
        from mangaproof.ui.viewer_widget import numpy_to_qimage

        doc = self._doc_provider(rel)
        if doc is None:
            log.warning("预加载失败：文档不存在 %s", rel)
            return False, {}
        images: dict = {"merged": None, "bg": None}
        try:
            if kind in (KIND_OPEN, KIND_PRELOAD):
                doc.prepare_merged()
                # 后台预热显示用 QImage，切换后首帧免转换
                try:
                    images["merged"] = numpy_to_qimage(doc.merged_np())
                except Exception:
                    pass
            if kind == KIND_OPEN:
                self._warm_layer(doc, layer_id)
            elif kind == KIND_EXTRA:
                doc.prepare_bg()
                if layer_id == WARM_ALL:
                    # 当前文件：预热全部图层的视觉边界，任意 ←→ 图层切换免等待
                    self._warm_all_layers(doc)
                else:
                    self._warm_layer(doc, layer_id)
                bg = doc.bg_image()
                if bg is not None:
                    images["bg"] = numpy_to_qimage(bg[2])
            return True, images
        except Exception:
            log.exception("预加载失败：%s", rel)
            return False, images

    @staticmethod
    def _warm_layer(doc, layer_id: str) -> None:
        """预热目标图层像素与视觉边界（定位/缩放免等待）。"""
        if not layer_id:
            return
        info = doc.layer_by_id(layer_id)
        if info is None:
            return
        if doc.layer_image(layer_id) is not None:
            info.visual_bounds()

    @staticmethod
    def _warm_all_layers(doc) -> None:
        """预热文档全部图层的视觉边界（层像素走 LRU，不常驻）。

        无论个别图层是否提取失败，结束都标记完成，避免反复重试。
        """
        for info in doc.layers:
            if info.has_visual_bounds():
                continue
            if doc.layer_image(info.id) is not None:
                info.visual_bounds()
        doc.mark_all_layers_warmed()
