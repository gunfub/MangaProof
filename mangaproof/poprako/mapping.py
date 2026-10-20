# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""本地 PSD ↔ Poprako 复合图页的匹配（纯函数；需求 §7，设计文档 §7）。

四级规则按顺位尝试，命中即锁定；**有歧义一律不猜**——宁可让用户手动选，
也不要把问题标到错误的页上（那是会造成返修方向性错误的事故）。

不导入 Qt、不发网络：全部可用普通 ``pytest`` 覆盖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from mangaproof.poprako.models import PageArtwork

__all__ = [
    "MatchResult",
    "normalize_ident",
    "auto_match",
    "align_in_order",
]

_DIGITS = re.compile(r"(\d+)")


def normalize_ident(text: object) -> str:
    """统一成可比较的形式：去空白、``\\``→``/``、去 ``./`` 前缀与开头斜杠、小写。"""
    if not isinstance(text, str):
        return ""
    value = text.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/").lower()


def _basename(value: str) -> str:
    return PurePosixPath(value).name if value else ""


def _stem(value: str) -> str:
    return PurePosixPath(value).stem if value else ""


def _digits(value: str) -> str:
    """取去扩展名之后**最后一段**连续数字，并把前导零去掉（``001`` 与 ``1`` 同值）。"""
    runs = _DIGITS.findall(_stem(value))
    if not runs:
        return ""
    try:
        return str(int(runs[-1]))
    except ValueError:  # pragma: no cover —— 纯数字正则不可能失败
        return runs[-1]


def _file_key(file_rel: str, level: int) -> str:
    normalized = normalize_ident(file_rel)
    if level == 0:
        return normalized
    if level == 1:
        return _basename(normalized)
    if level == 2:
        return _stem(normalized)
    return _digits(normalized)


def _page_key(page: PageArtwork, level: int) -> str:
    normalized = normalize_ident(page.raw_ident)
    if level == 0:
        return normalized
    if level == 1:
        return _basename(normalized)
    if level == 2:
        return _stem(normalized)
    return _digits(normalized)


def _group(keys: Mapping[str, str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for owner, key in keys.items():
        if not key:
            continue
        grouped.setdefault(key, []).append(owner)
    return grouped


@dataclass(frozen=True, slots=True)
class MatchResult:
    """一次自动匹配的结果。

    ``ambiguous`` 里的文件同时也会出现在 ``unmatched_local``（它们需要人工处理），
    这里单独列出来是为了让界面能说明"为什么不自动选"。
    """

    mapping: dict[str, str]
    unmatched_local: tuple[str, ...]
    unmatched_remote: tuple[PageArtwork, ...]
    ambiguous: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def matched_count(self) -> int:
        return len(self.mapping)


def auto_match(files: Sequence[str], pages: Sequence[PageArtwork]) -> MatchResult:
    """按四级规则自动匹配。

    ``files`` 的顺序即任务里的自然序；``pages`` 按 ``index`` 使用（返回值里再排序）。
    """
    ordered_pages = sorted(pages, key=lambda page: (page.index, page.id))
    files = list(files)
    page_by_id = {page.id: page for page in ordered_pages}
    remaining_files = list(files)
    remaining_ids = set(page_by_id)
    mapping: dict[str, str] = {}
    ambiguous: dict[str, set[str]] = {}

    for level in range(4):
        file_keys = {name: _file_key(name, level) for name in remaining_files}
        page_keys = {pid: _page_key(page_by_id[pid], level) for pid in remaining_ids}
        files_by_key = _group(file_keys)
        pages_by_key = _group(page_keys)

        assignments: list[tuple[str, str]] = []
        for key, owners in files_by_key.items():
            if not key:
                continue
            candidates = pages_by_key.get(key, [])
            if not candidates:
                continue
            if len(owners) == 1 and len(candidates) == 1:
                assignments.append((owners[0], candidates[0]))
            else:
                for owner in owners:
                    ambiguous.setdefault(owner, set()).update(candidates)

        for owner, page_id in assignments:
            mapping[owner] = page_id
            ambiguous.pop(owner, None)
            if owner in remaining_files:
                remaining_files.remove(owner)
            remaining_ids.discard(page_id)

    unmatched_local = tuple(name for name in files if name not in mapping)
    used_ids = set(mapping.values())
    unmatched_remote = tuple(page for page in ordered_pages if page.id not in used_ids)
    ambiguous_rows = tuple(
        (owner, tuple(sorted(ambiguous.get(owner, set()))))
        for owner in unmatched_local
        if ambiguous.get(owner)
    )
    return MatchResult(
        mapping=mapping,
        unmatched_local=unmatched_local,
        unmatched_remote=unmatched_remote,
        ambiguous=ambiguous_rows,
    )


def align_in_order(
    files: Sequence[str], pages: Sequence[PageArtwork]
) -> dict[str, str]:
    """按顺序对齐：本地自然序 ↔ 远端页序，一一对应取前 ``min(n, m)`` 项。"""
    ordered_pages = sorted(pages, key=lambda page: (page.index, page.id))
    pairs = zip(list(files), ordered_pages)
    return {name: page.id for name, page in pairs}
