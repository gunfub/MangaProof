"""图层数据模型（需求 §63 LayerInfo）。

只读：MangaProof 绝不修改 PSD 图层（需求 §2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import numpy as np

# 矩形统一使用 PSD World Coordinates：(left, top, right, bottom) 或
# (x, y, width, height)。两者都表示整像素坐标。


@dataclass
class LayerInfo:
    """可监制图层的只读模型。

    image 延迟加载（通过 image_loader 回调），避免一次性解出全部图层像素
    （需求 §59、§60）。
    """

    id: str                      # 稳定编号（文档内索引路径，如 "3" 或 "3.1"）
    name: str
    bounds: Tuple[int, int, int, int]   # (left, top, right, bottom)
    visible: bool
    layer_type: str              # psd-tools kind
    image_mode: str = "composite"  # 像素提取路径：composite | topil | topil_only
    parent_id: Optional[str] = None
    children: list[str] = field(default_factory=list)
    image_loader: Optional[Callable[[], Optional[np.ndarray]]] = None
    # 视觉边界专用加载器：alpha 直取快路径（平坦图层），None 时回退完整像素提取
    bounds_loader: Optional[Callable[[], Optional[np.ndarray]]] = None
    # 视觉边界（alpha > 0 像素的包围盒），惰性缓存
    _visual_bounds: Optional[Tuple[int, int, int, int]] = field(
        default=None, repr=False, init=False
    )
    _visual_bounds_computed: bool = field(default=False, repr=False, init=False)

    # -- 几何 --------------------------------------------------------------

    @property
    def width(self) -> int:
        return max(0, self.bounds[2] - self.bounds[0])

    @property
    def height(self) -> int:
        return max(0, self.bounds[3] - self.bounds[1])

    @property
    def center(self) -> Tuple[float, float]:
        return (
            (self.bounds[0] + self.bounds[2]) / 2.0,
            (self.bounds[1] + self.bounds[3]) / 2.0,
        )

    # -- 像素 --------------------------------------------------------------

    def load_image(self) -> Optional[np.ndarray]:
        """加载该图层自身像素（RGBA numpy 数组），失败返回 None。"""
        if self.image_loader is None:
            return None
        try:
            return self.image_loader()
        except Exception:
            return None

    def load_bounds_image(self) -> Optional[np.ndarray]:
        """视觉边界专用最小图像：alpha 快路径优先，回退完整像素提取。

        平坦图层的 bounds_loader 直接解码 alpha 通道（2D 数组），
        免去 composite 合成管线 / ICC / RGBA 转换（实测 38×）。
        """
        if self.bounds_loader is not None:
            try:
                img = self.bounds_loader()
            except Exception:
                img = None
            if img is not None:
                return img
        return self.load_image()

    def visual_bounds(
        self, alpha_threshold: float = 0.0, image: Optional[np.ndarray] = None
    ) -> Optional[Tuple[int, int, int, int]]:
        """视觉内容包围盒（alpha > threshold），无内容时返回 None。

        结果相对 PSD World Coordinates（与 bounds 同坐标系）。
        结果缓存：computed 标记区分「未计算」与「已计算但无内容」，
        避免透明图层每次访问都重新提取像素。

        image 参数：调用方已提取的像素可直接传入复用（避免二次提取）。
        """
        if not self._visual_bounds_computed:
            if image is None:
                image = self.load_bounds_image()
            self._visual_bounds = compute_visual_bounds(
                image, alpha_threshold=alpha_threshold
            )
            self._visual_bounds_computed = True
        return self._visual_bounds

    def has_visual_bounds(self) -> bool:
        """视觉边界是否已计算（供预加载快路径判断，避免 UI 线程提取像素）。"""
        return self._visual_bounds_computed


def compute_visual_bounds(
    image: Optional[np.ndarray], alpha_threshold: float = 0.0
) -> Optional[Tuple[int, int, int, int]]:
    """从图像数组计算视觉边界（需求 §18）。

    数组通常是图层自身像素（已裁到 bbox 的裁剪图，RGBA），
    或平坦图层的 alpha 直取快路径结果（2D alpha 掩码）。
    返回 (left, top, right, bottom)。
    """
    if image is None or image.size == 0 or image.ndim < 2:
        return None
    height, width = image.shape[:2]
    if image.ndim == 2:
        alpha = image            # 快路径：直接就是 alpha 掩码
    elif image.shape[2] == 4:
        alpha = image[:, :, 3]
    elif image.shape[2] == 2:  # LA
        alpha = image[:, :, 1]
    else:
        return (0, 0, width, height)  # 无 Alpha 通道 → 整幅图均为内容

    mask = alpha > alpha_threshold
    if not mask.any():
        return None
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    top = int(np.argmax(rows))
    bottom = int(height - np.argmax(rows[::-1]))
    left = int(np.argmax(cols))
    right = int(width - np.argmax(cols[::-1]))
    if bottom <= top or right <= left:
        return None
    return (left, top, right, bottom)
