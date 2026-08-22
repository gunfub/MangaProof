"""PSDDocument：单个 PSD 的只读文档包装（需求 §2.2、§2.3、§59、§60）。

持有：
- merged image（PSD 自带，长期缓存，绝不重合成）；
- 背景图层（bg 名精确匹配，否则最底部有像素图层，需求 §24）；
- 可监制图层列表（LayerInfo，像素延迟加载 + LRU 缓存）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from mangaproof.psd import loader
from mangaproof.psd.image_cache import LayerImageCache
from mangaproof.psd.layer_model import LayerInfo

log = logging.getLogger("mangaproof.psd.document")

# 具备直接像素内容的图层类型（调整图层、容器组除外）
_REVIEWABLE_KINDS = {
    "pixel",
    "type",
    "shape",
    "smartobject",
    "solidcolorfill",
    "gradientfill",
    "patternfill",
}


class PSDDocument:
    def __init__(
        self,
        path: Path,
        psd=None,
        layer_cache: Optional[LayerImageCache] = None,
    ):
        self.path = Path(path)
        self._psd = psd if psd is not None else loader.open_psd_tools(self.path)
        self._layer_cache = layer_cache if layer_cache is not None else LayerImageCache()

        # 像素提取可能在后台预加载线程与 UI 线程间并发，
        # 用可重入锁串行化对 psd-tools 惰性解析的访问。
        self._io_lock = threading.RLock()

        # 长期缓存：merged image（需求 §60）
        self._merged_np: Optional[np.ndarray] = None
        self._merged_error: Optional[Exception] = None
        # 长期缓存：background image（世界坐标偏移 + 像素）
        self._bg: Optional[Tuple[int, int, np.ndarray]] = None

        self._layers: Optional[List[LayerInfo]] = None
        self._layer_by_id: Optional[Dict[str, LayerInfo]] = None
        self._bg_layer_id: Optional[str] = None

    # -- 基本信息 ----------------------------------------------------------

    @property
    def psd(self):
        return self._psd

    @property
    def size(self) -> Tuple[int, int]:
        return (int(self._psd.width), int(self._psd.height))

    @property
    def name(self) -> str:
        return self.path.name

    # -- merged image ------------------------------------------------------

    def has_merged(self) -> bool:
        """merged image 是否已提取（或已确认不可用）。"""
        return self._merged_np is not None or self._merged_error is not None

    def merged_np(self) -> np.ndarray:
        """PSD 自带的 merged image（RGBA numpy），仅读取一次。

        无 merged image 时抛 NoCompositeError；所有异常都会缓存，
        避免每次访问都重新解析。
        """
        with self._io_lock:
            if self._merged_np is None and self._merged_error is None:
                try:
                    pil = loader.get_merged_pil(self._psd)
                    rgba = pil.convert("RGBA")
                    self._merged_np = np.asarray(rgba).copy()
                except Exception as exc:
                    self._merged_error = exc
                    raise
            if self._merged_error is not None:
                if isinstance(self._merged_error, loader.NoCompositeError):
                    raise loader.NoCompositeError(str(self._merged_error))
                raise self._merged_error
            return self._merged_np  # type: ignore[return-value]

    def prepare_images(self) -> None:
        """后台预加载：提取 merged 与背景图像，失败仅缓存错误不抛出。"""
        self.prepare_merged()
        self.prepare_bg()

    def prepare_merged(self) -> None:
        """阶段 A：仅提取 merged（文件切换的关键路径）。"""
        try:
            self.merged_np()
        except Exception:
            pass

    def prepare_bg(self) -> None:
        """阶段 B：提取背景图像（自动对比用）。"""
        try:
            self.bg_image()
        except Exception:
            pass

    def release_images(self) -> None:
        """释放 merged/bg 大图（保留图层树与 LRU 层像素缓存）。

        非阻塞：若后台线程正在提取则跳过本轮，避免 UI 卡在锁上。
        """
        if not self._io_lock.acquire(blocking=False):
            return
        try:
            self._merged_np = None
            self._merged_error = None
            self._bg = None
        finally:
            self._io_lock.release()

    # -- 图层 --------------------------------------------------------------

    def build_layers(self) -> List[LayerInfo]:
        """构建可监制图层列表（文档顺序，稳定编号）。"""
        infos: List[LayerInfo] = []
        stack: List[Tuple] = [(self._psd, None, "0")]

        def visit(node, parent_id, path_id):
            # 先按文档顺序收集本层，再递归子层
            if node.kind in _REVIEWABLE_KINDS and getattr(node, "visible", True):
                bbox = tuple(int(v) for v in node.bbox) if node.bbox else (0, 0, 0, 0)
                info = LayerInfo(
                    id=path_id,
                    name=str(getattr(node, "name", "") or ""),
                    bounds=bbox,
                    visible=bool(node.visible),
                    layer_type=str(node.kind),
                    parent_id=parent_id,
                    image_loader=self._make_image_loader(node),
                )
                infos.append(info)
            # 递归子层（group 等容器）
            try:
                children = list(node)  # __iter__ 按文档顺序
            except Exception:
                children = []
            for i, child in enumerate(children):
                visit(child, path_id, f"{path_id}.{i}")

        visit(self._psd, None, "0")
        self._layers = infos
        self._layer_by_id = {info.id: info for info in infos}
        return infos

    def _make_image_loader(self, node):
        """返回惰性加载器：layer composite（含蒙版/剪贴）→ 失败退化为 topil。

        提取过程持 io 锁，避免与后台预加载线程并发访问 psd-tools。
        """

        def load():
            with self._io_lock:
                try:
                    # composite() 得到图层自身内容（含蒙版、剪贴图层），
                    # 背景透明。这是图层“实际显示内容”的最佳近似。
                    img = node.composite(force=True)
                    if img is None:
                        img = node.topil()
                except Exception:
                    try:
                        img = node.topil()
                    except Exception:
                        return None
                if img is None:
                    return None
                return np.asarray(img.convert("RGBA")).copy()

        return load

    @property
    def layers(self) -> List[LayerInfo]:
        if self._layers is None:
            self.build_layers()
        return self._layers  # type: ignore[return-value]

    def layer_by_id(self, layer_id: str) -> Optional[LayerInfo]:
        if self._layer_by_id is None:
            self.build_layers()
        return self._layer_by_id.get(layer_id)  # type: ignore[union-attr]

    def layer_image(self, layer_id: str) -> Optional[np.ndarray]:
        """图层像素（LRU 缓存），返回图层自身坐标系下的 RGBA。"""
        key_path = str(self.path)
        cached = self._layer_cache.get(key_path, layer_id)
        if cached is not None:
            return cached
        info = self.layer_by_id(layer_id)
        if info is None:
            return None
        img = info.load_image()
        if img is not None:
            self._layer_cache.put(key_path, layer_id, img)
        return img

    # -- 背景图层（需求 §24、§25） ----------------------------------------

    def bg_layer_id(self) -> Optional[str]:
        if self._bg_layer_id is None:
            self._bg_layer_id = self._select_bg_layer()
        return self._bg_layer_id

    def _select_bg_layer(self) -> Optional[str]:
        layers = self.layers
        # 1) 名称严格等于 "bg"（大小写敏感）
        for info in layers:
            if info.name == "bg":
                if self._has_content(info):
                    return info.id
        # 2) 最底部具有可用像素内容的图层
        bottom = None
        for info in layers:  # 文档顺序 = 从上到下，最后的即最底部
            if self._has_content(info):
                bottom = info.id
        return bottom

    @staticmethod
    def _has_content(info: LayerInfo) -> bool:
        if info.bounds[2] <= info.bounds[0] or info.bounds[3] <= info.bounds[1]:
            return False
        try:
            img = info.load_image()
        except Exception:
            return False
        return img is not None and img.size > 0

    def bg_image(self) -> Optional[Tuple[int, int, np.ndarray]]:
        """背景图层像素，返回 (offset_x, offset_y, RGBA numpy)。

        像素坐标系为图层自身（已裁到 bbox），绘制时需偏移到世界坐标。
        """
        if self._bg is None:
            bg_id = self.bg_layer_id()
            if bg_id is None:
                return None
            info = self.layer_by_id(bg_id)
            img = self.layer_image(bg_id)
            if info is None or img is None:
                return None
            self._bg = (info.bounds[0], info.bounds[1], img)
        return self._bg

    # -- 资源 --------------------------------------------------------------

    def release(self) -> None:
        """释放缓存（PSD 句柄由 psd-tools 惰性管理，无需显式关闭）。"""
        self._merged_np = None
        self._merged_error = None
        self._bg = None
        if self._layer_cache is not None:
            self._layer_cache.clear()


def pil_to_rgba_np(pil: Image.Image) -> np.ndarray:
    return np.asarray(pil.convert("RGBA")).copy()
