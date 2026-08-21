"""视觉中心定位（需求 §17、§18）。

选择图层后，将图层「视觉中心」（非 Bounds 几何中心）移动到视口中心。

- 有 Alpha 的图层：alpha > threshold（默认 0）的像素计算 Visual Bounding Box；
- 完全透明：使用 Layer Bounds fallback；
- Bounds 不可用：保持当前 Camera（由调用方处理）。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

from mangaproof.psd.layer_model import LayerInfo, compute_visual_bounds

log = logging.getLogger("mangaproof.camera.centering")


def layer_visual_bounds(info: LayerInfo, alpha_threshold: float = 0.0) -> Optional[Tuple[int, int, int, int]]:
    """返回图层视觉边界（世界坐标）。

    图层的裁剪像素相对其 bbox 原点，需把结果平移到世界坐标。
    """
    vb = info.visual_bounds(alpha_threshold=alpha_threshold)
    if vb is None:
        return None
    left, top, right, bottom = vb
    ox, oy = info.bounds[0], info.bounds[1]
    return (left + ox, top + oy, right + ox, bottom + oy)


def layer_visual_center(info: LayerInfo, alpha_threshold: float = 0.0) -> Optional[Tuple[float, float]]:
    """视觉中心（世界坐标）。"""
    vb = layer_visual_bounds(info, alpha_threshold)
    if vb is None:
        return None
    return ((vb[0] + vb[2]) / 2.0, (vb[1] + vb[3]) / 2.0)


def compute_center_target(info: LayerInfo, alpha_threshold: float = 0.0) -> Tuple[float, float]:
    """定位目标点（世界坐标），带 Bounds fallback（需求 §18.1）。"""
    center = layer_visual_center(info, alpha_threshold)
    if center is not None:
        return center
    if info.width > 0 and info.height > 0:
        log.info("图层 %s 无有效像素，使用 Layer Bounds 中心", info.id)
        return info.center
    # Bounds 也不可用 → 保持当前 Camera（调用方不移动相机即可）
    return info.center
