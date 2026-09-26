# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""自动缩放（需求 §20、§20.1）。

比例定义：图层视觉内容的最长边，占 Viewport 对应尺寸的目标比例。
例如最长边 1200、比例 80% → 最长边 ≈ 视口对应尺寸 × 80%。

2026-09-25 追加：``bg`` 与 ``bg 拷贝`` 可以各用一份独立比例（:func:`resolve_display_ratio`），
其余图层继续用全局比例；功能默认关闭。
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


def resolve_display_ratio(
    *,
    global_ratio: float,
    per_layer_enabled: bool,
    bg_ratio: float,
    bg_copy_ratio: float,
    layer_id: Optional[str],
    bg_layer_id: Optional[str],
    bg_copy_layer_id: Optional[str],
) -> float:
    """这次自动缩放该用哪个比例：bg / bg 拷贝 可各用独立值，其余用全局值。

    纯函数（不碰 Qt、不碰设置对象），调用方负责把 id 查好传进来 —— 这样这条规则
    可以被单测直接钉住。

    优先级与边界（需求方 2026-09-25 决策）：

    - ``per_layer_enabled=False`` → 恒为全局比例（功能默认关，行为与旧版一致）；
    - **bg 优先**：``bg_layer_id`` 已含"没有 `bg` 名 → 最底部有可用像素内容图层"的
      既有兜底（需求 §24），所以兜底选中的那层也按 bg 的独立比例显示；
    - ``bg 拷贝`` **没有就不套用、也不兜底**：``bg_copy_layer_id`` 为 None 时，
      任何图层都不会拿到 bg 拷贝的独立比例；
    - 两个 id 都不匹配（或为 None）→ 全局比例。
    """
    if not per_layer_enabled:
        return global_ratio
    if bg_layer_id is not None and layer_id == bg_layer_id:
        return bg_ratio
    if bg_copy_layer_id is not None and layer_id == bg_copy_layer_id:
        return bg_copy_ratio
    return global_ratio
