"""导航逻辑（需求 §11、§15、§44）。

- 粗粒度：↑↓ 切换 PSD；
- 细粒度：←→ 切换图层；
- nextUnreviewedLayer()：优先当前图层之后，找不到则从头继续搜索。
"""

from __future__ import annotations

from typing import List, Optional

from mangaproof.review.state import TaskState, UNREVIEWED


def next_unreviewed_in_list(
    layer_ids: List[str],
    reviews,
    file_rel: str,
    current_index: Optional[int],
) -> Optional[int]:
    """在当前图层列表中寻找下一个 UNREVIEWED 图层（需求 §44）。

    优先当前图层之后；找不到则从头搜索；全部完成返回 None。
    """
    n = len(layer_ids)
    if n == 0:
        return None

    start = 0 if current_index is None else current_index + 1
    for i in range(start, n):
        if reviews.get(f"{file_rel}|{layer_ids[i]}", UNREVIEWED) == UNREVIEWED:
            return i
    for i in range(0, min(start, n)):
        if reviews.get(f"{file_rel}|{layer_ids[i]}", UNREVIEWED) == UNREVIEWED:
            return i
    return None


def first_unreviewed_index(layer_ids: List[str], reviews, file_rel: str) -> Optional[int]:
    return next_unreviewed_in_list(layer_ids, reviews, file_rel, None)


def file_index_by_rel(task: TaskState, rel_path: str) -> Optional[int]:
    for idx, record in enumerate(task.files):
        if record.relative_path == rel_path:
            return idx
    return None


def prev_file_index(task: TaskState, current_rel: str) -> Optional[int]:
    idx = file_index_by_rel(task, current_rel)
    if idx is None or idx <= 0:
        return None
    return idx - 1


def next_file_index(task: TaskState, current_rel: str) -> Optional[int]:
    idx = file_index_by_rel(task, current_rel)
    if idx is None or idx >= len(task.files) - 1:
        return None
    return idx + 1


def count_reviewed_in_file(task: TaskState, file_rel: str) -> int:
    """该 PSD 已监制图层数（用于跨文件判断，配合图层总数使用）。"""
    prefix = file_rel + "|"
    return sum(1 for k in task.reviews if k.startswith(prefix))
