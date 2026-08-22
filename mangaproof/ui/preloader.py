"""后台 PSD 预加载线程（需求：切换大 PSD 不卡顿）。

- 前台请求（open）：提取 merged image + 背景图 + 目标图层像素/视觉边界，
  用于打开文件时的快速切换（配合主窗口的进度框）；
- 后台预加载（preload）：按当前文件邻域提前提取 merged + 背景图，
  顺序 review 时后续文件秒开；
- 快速切换处理：新 open 请求直接替换未处理的旧请求；preload 队列可整体
  替换（set_preloads），正在执行的旧任务无法中断、结果仅写入缓存无害。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

log = logging.getLogger("mangaproof.ui.preloader")

KIND_OPEN = "open"
KIND_PRELOAD = "preload"


class PreloadWorker(QThread):
    """单线程后台加载器（队列 + 条件变量）。

    doc_provider(rel) -> PSDDocument 或 None（由主窗口提供，只读取
    已创建的文档对象，不在后台创建新文档）。
    """

    task_done = Signal(str, str, bool)   # rel, kind, ok

    def __init__(self, doc_provider: Callable[[str], Optional[object]], parent=None):
        super().__init__(parent)
        self._doc_provider = doc_provider
        self._cond = threading.Condition()
        self._open_job: Optional[Tuple[str, str]] = None  # (rel, layer_id)
        self._preload_jobs: List[str] = []
        self._stop = False

    # -- 主线程 API --------------------------------------------------------

    def submit_open(self, rel: str, layer_id: str = "") -> None:
        """请求打开某个文件（提取其图像），替换未处理的旧请求。"""
        with self._cond:
            self._open_job = (rel, layer_id)
            self._cond.notify_all()

    def cancel_open(self) -> None:
        with self._cond:
            self._open_job = None

    def set_preloads(self, rels: List[str]) -> None:
        """整体替换预加载队列（快速切换时旧邻域作废）。"""
        with self._cond:
            self._preload_jobs = list(rels)
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    # -- 线程主体 ----------------------------------------------------------

    def run(self) -> None:
        while True:
            with self._cond:
                while not self._stop and self._open_job is None and not self._preload_jobs:
                    self._cond.wait()
                if self._stop:
                    return
                if self._open_job is not None:
                    rel, layer_id = self._open_job
                    self._open_job = None
                    kind = KIND_OPEN
                else:
                    rel = self._preload_jobs.pop(0)
                    layer_id = ""
                    kind = KIND_PRELOAD
            ok = self._process(rel, kind, layer_id)
            self.task_done.emit(rel, kind, ok)

    def _process(self, rel: str, kind: str, layer_id: str) -> bool:
        doc = self._doc_provider(rel)
        if doc is None:
            log.warning("预加载失败：文档不存在 %s", rel)
            return False
        try:
            doc.prepare_images()
            if kind == KIND_OPEN and layer_id:
                info = doc.layer_by_id(layer_id)
                if info is not None:
                    if doc.layer_image(layer_id) is not None:
                        info.visual_bounds()   # 预热视觉边界，定位免等待
            return True
        except Exception:
            log.exception("预加载失败：%s", rel)
            return False
