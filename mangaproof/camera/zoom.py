"""自动缩放（需求 §20、§20.1）。

比例定义：图层视觉内容的最长边，占 Viewport 对应尺寸的目标比例。
例如最长边 1200、比例 80% → 最长边 ≈ 视口对应尺寸 × 80%。
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


def fit_zoom(
    visual_bounds: Optional[Tuple[float, float, float, float]],
    viewport_size: Tuple[float, float],
    ratio: float,
) -> Optional[float]:
    """根据视觉边界与目标比例计算 zoom。

    visual_bounds: (left, top, right, bottom) 世界坐标；
    viewport_size: (width, height)；
    ratio: 最长边占视口对应尺寸的目标比例（如 0.6）。

    返回 None 表示无法计算（保持当前 Camera）。
    """
    if visual_bounds is None:
        return None
    left, top, right, bottom = visual_bounds
    w = right - left
    h = bottom - top
    if w <= 0 or h <= 0:
        return None

    vw, vh = viewport_size
    if vw <= 0 or vh <= 0:
        return None

    # 最长边对应视口方向
    if w >= h:
        zoom = (vw * ratio) / w
    else:
        zoom = (vh * ratio) / h
    if not math.isfinite(zoom) or zoom <= 0:
        return None
    return zoom
