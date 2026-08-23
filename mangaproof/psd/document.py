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

# 图层像素提取路径（按图层类型分流，见 _make_image_loader）
_LOADER_COMPOSITE = "composite"      # 其余图层：composite（蒙版/剪贴/混合/特效）
_LOADER_TOPIL = "topil"              # bg/最底层：topil 优先，失败退化 composite
_LOADER_TOPIL_ONLY = "topil_only"    # type 图层：仅 topil，失败直接放弃


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
        self._bg_checked: bool = False

        self._layers: Optional[List[LayerInfo]] = None
        self._layer_by_id: Optional[Dict[str, LayerInfo]] = None
        self._bg_layer_id: Optional[str] = None
        # 全图层视觉边界预热是否已完成（个别提取失败的图层不再反复重试）
        self._all_layers_warmed = False

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

        提取顺序：Pillow 原生 C 解码（快，约 7×）→ 失败/尺寸不符
        回退 psd-tools 纯 Python 路径 → 仍失败抛 NoCompositeError。
        所有异常都会缓存，避免每次访问都重新解析。
        """
        with self._io_lock:
            if self._merged_np is None and self._merged_error is None:
                try:
                    fast = loader.get_merged_pil_fast(self.path, expect_size=self.size)
                    if fast is not None:
                        self._merged_np = np.asarray(fast).copy()
                    else:
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
            self._bg_checked = False
        finally:
            self._io_lock.release()

    # -- 图层 --------------------------------------------------------------

    def build_layers(self) -> List[LayerInfo]:
        """构建可监制图层列表（文档顺序，稳定编号）。

        注意：psd-tools 1.18 的迭代顺序为自下而上（第一个即最底部图层，
        已用合成结果实证），因此「最底部 pixel 层」取迭代序中第一个
        pixel 层。
        """
        infos: List[LayerInfo] = []
        entries: List[Tuple] = []

        def visit(node, parent_id, path_id):
            # 先按文档顺序收集本层，再递归子层
            if node.kind in _REVIEWABLE_KINDS and getattr(node, "visible", True):
                entries.append((node, parent_id, path_id))
            # 递归子层（group 等容器）
            try:
                children = list(node)  # __iter__ 按文档顺序（自下而上）
            except Exception:
                children = []
            for i, child in enumerate(children):
                visit(child, path_id, f"{path_id}.{i}")

        visit(self._psd, None, "0")

        # 最底部可见 pixel 图层 = 迭代序中第一个 pixel 层
        # （漫画翻译监制场景中即原版未翻译底图，无蒙版/特效）
        bottom_pixel_id = None
        for node, _parent, path_id in entries:
            if node.kind == "pixel":
                bottom_pixel_id = path_id
                break

        for node, parent_id, path_id in entries:
            name = str(getattr(node, "name", "") or "")
            mode = _LOADER_COMPOSITE
            if node.kind == "type":
                mode = _LOADER_TOPIL_ONLY
            elif name == "bg" or path_id == bottom_pixel_id:
                mode = _LOADER_TOPIL
            bbox = tuple(int(v) for v in node.bbox) if node.bbox else (0, 0, 0, 0)
            info = LayerInfo(
                id=path_id,
                name=name,
                bounds=bbox,
                visible=bool(node.visible),
                layer_type=str(node.kind),
                image_mode=mode,
                parent_id=parent_id,
                image_loader=self._make_image_loader(node, mode),
            )
            infos.append(info)

        self._layers = infos
        self._layer_by_id = {info.id: info for info in infos}
        return infos

    def _make_image_loader(self, node, mode: str = _LOADER_COMPOSITE):
        """返回惰性加载器（按图层类型选择提取路径）。

        - topil_only（type 图层）：新版 PS 的文字图层无论是否栅格化都自带
          预生成的文字栅格图像，topil() 直接读出字形像素；psd-tools 的
          composite 不渲染字形（无栅格时只会产出全透明/整块填充），对
          视觉边界（几何中心）无用。topil 失败直接放弃，定位回退
          bbox 中心（与现状 composite 的几何结果一致）。
        - topil（bg/最底层图层）：原版未翻译底图，无蒙版/特效，
          topil 与 composite 像素级一致，省去蒙版/剪贴/混合/特效整条
          合成管线；topil 失败退化 composite 保持鲁棒。
        - composite（其余图层）：保留现状——composite() 得到图层自身
          内容（含蒙版、剪贴图层），背景透明，是图层「实际显示内容」
          的最佳近似。

        提取过程持 io 锁，避免与后台预加载线程并发访问 psd-tools。
        """

        def load():
            with self._io_lock:
                if mode == _LOADER_TOPIL_ONLY:
                    img = self._layer_topil(node)
                    if img is None:
                        return None
                    return np.asarray(img.convert("RGBA")).copy()
                if mode == _LOADER_TOPIL:
                    img = self._layer_topil(node)
                    if img is not None:
                        return np.asarray(img.convert("RGBA")).copy()
                    # topil 拿不到内容（异常场景）→ 退化 composite 保持鲁棒
                # composite 路径（现状）
                try:
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

    @staticmethod
    def _layer_topil(node):
        """topil() 的安全封装：异常或无内容一律返回 None。"""
        try:
            img = node.topil()
        except Exception:
            return None
        return img

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
        # 2) 最底部具有可用像素内容的图层（需求 §24）。
        #    psd-tools 1.18 迭代顺序为自下而上（已用合成结果实证）：
        #    第一个有内容的图层即 PS 图层面板最底部的图层——
        #    漫画翻译监制场景中即原版未翻译底图（对比基准）。
        for info in layers:
            if self._has_content(info):
                return info.id
        return None

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
        if self._bg is None and not self._bg_checked:
            self._bg_checked = True
            bg_id = self.bg_layer_id()
            if bg_id is None:
                return None
            info = self.layer_by_id(bg_id)
            img = self.layer_image(bg_id)
            if info is None or img is None:
                return None
            self._bg = (info.bounds[0], info.bounds[1], img)
        return self._bg

    def has_bg(self) -> bool:
        """背景图是否已尝试提取（区分「未提取」与「已提取但无背景层」）。"""
        return self._bg_checked

    def mark_all_layers_warmed(self) -> None:
        """标记全图层视觉边界预热已完成（失败图层不再重试）。"""
        self._all_layers_warmed = True

    @property
    def all_layers_warmed(self) -> bool:
        return self._all_layers_warmed

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
