# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""视觉中心定位（需求 §17、§18）。

选择图层后，将图层「视觉中心」（非 Bounds 几何中心）移动到视口中心。

定位依据有两种（``position_mode``，设置项「图层定位依据」）：

- :data:`POSITION_VISUAL`（默认，桌面）：解析图层像素，按 alpha > threshold
  （默认 0）算 Visual Bounding Box —— 居中更准，代价是要解像素；
- :data:`POSITION_FAST`（默认，Android）：直接取 PSD 图层自带的
  ``x / y / w / h``（:attr:`LayerInfo.bounds`），**零像素解析** —— 快、
  省内存，图层有透明留白 / 蒙版 / 特效时居中会不如前者精准（刻意的取舍）。

两者都走 :func:`position_bounds` 这一个入口，调用方（相机定位、蓝色虚线框、
自动框选）只认它返回的矩形。

**不变量（改动前必读）**：快速模式是纯元数据路径，**禁止**在这里引入任何像素
提取 / 缓存。后续针对「某些图层居中更准」的改进（蒙版、特效、透明留白……）
一律只做在 visual 分支 —— 快速分支只要开始解像素，就不再是"零解析"模式，
速度与内存会一起回退（见 docs/1.3.0位置解析快速路径_技术设计.md）。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

from mangaproof.psd.layer_model import LayerInfo, compute_visual_bounds

log = logging.getLogger("mangaproof.camera.centering")

# 自动框选：在视觉边界（画布上的蓝色虚线框）基础上，上下左右各外扩的像素数。
AUTO_BOX_MARGIN = 5.0

# 定位依据（设置项「图层定位依据」的两个取值）
POSITION_VISUAL = "visual"    # 视觉内容包围盒（解像素，精准）
POSITION_FAST = "fast"        # PSD 图层原始框（零像素解析，快）
POSITION_MODES: Tuple[str, ...] = (POSITION_VISUAL, POSITION_FAST)


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


def _layer_bounds_or_none(info: LayerInfo) -> Optional[Tuple[int, int, int, int]]:
    """PSD 图层原始框（世界坐标）；退化矩形（空图层）返回 None。"""
    left, top, right, bottom = (int(v) for v in info.bounds)
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def position_bounds(
    info: LayerInfo,
    position_mode: str = POSITION_VISUAL,
    alpha_threshold: float = 0.0,
) -> Optional[Tuple[int, int, int, int]]:
    """定位 / 自动缩放 / 蓝色虚线框的目标矩形（世界坐标 LTRB）。

    - ``POSITION_FAST``：直接取 PSD 图层原始框，**不碰像素**；退化时 None；
    - ``POSITION_VISUAL``：视觉内容包围盒；图层完全透明时按需求 §18.1
      回退到 Layer Bounds；两者都不可用返回 None（调用方保持当前 Camera）。

    返回 None 一律表示"没有可用的定位矩形"，调用方不得把 None 当 (0,0,0,0) 用。
    """
    if position_mode == POSITION_FAST:
        return _layer_bounds_or_none(info)
    visual = layer_visual_bounds(info, alpha_threshold)
    if visual is not None:
        return visual
    return _layer_bounds_or_none(info)


def position_center(
    info: LayerInfo,
    position_mode: str = POSITION_VISUAL,
    alpha_threshold: float = 0.0,
) -> Optional[Tuple[float, float]]:
    """定位目标中心（世界坐标），与 :func:`position_bounds` 的矩形一致。"""
    rect = position_bounds(info, position_mode, alpha_threshold)
    if rect is None:
        return None
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def compute_center_target(info: LayerInfo, alpha_threshold: float = 0.0) -> Tuple[float, float]:
    """定位目标点（世界坐标），带 Bounds fallback（需求 §18.1）。

    历史接口：仅按视觉边界 + Bounds 兜底计算，不含快速模式（新代码请用
    :func:`position_center`）。两者在"无有效像素"时结果一致。
    """
    center = layer_visual_center(info, alpha_threshold)
    if center is not None:
        return center
    if info.width > 0 and info.height > 0:
        log.info("图层 %s 无有效像素，使用 Layer Bounds 中心", info.id)
        return info.center
    # Bounds 也不可用 → 保持当前 Camera（调用方不移动相机即可）
    return info.center


def auto_box_rect(
    info: LayerInfo,
    margin: float = AUTO_BOX_MARGIN,
    alpha_threshold: float = 0.0,
    position_mode: str = POSITION_VISUAL,
) -> Optional[Tuple[float, float, float, float]]:
    """自动框选矩形（世界坐标 x, y, w, h）。

    以定位目标矩形为准，上下左右**各外扩 margin 像素**——对称外扩，所以中心
    与该矩形中心完全一致。

    - ``POSITION_VISUAL``：蓝色虚线框 = 视觉边界；视觉边界为空（整层透明）时
      回退 Layer Bounds；
    - ``POSITION_FAST``：直接用 PSD 图层原始框（与快速模式的定位、蓝框同源，
      不解像素）。

    两者都不可用返回 None（调用方不生成红框）。
    """
    bounds = position_bounds(info, position_mode, alpha_threshold)
    if bounds is None:
        return None
    left, top, right, bottom = (float(v) for v in bounds)
    return (
        left - margin,
        top - margin,
        (right - left) + 2 * margin,
        (bottom - top) + 2 * margin,
    )
