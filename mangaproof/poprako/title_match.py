# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""作品名与任务名的匹配 / 排序 / 高亮区间（纯函数，不导入 Qt、不发网络）。

用途：Poprako 的"作品"下拉里，把与当前任务（漫画/章节）名字最像的作品排到前面，
并把**匹配上的那几个字**标绿（需求：2026-10-20 讨论定稿）。

算法（可解释、无第三方依赖）：

1. 两边都先做**归一化**，并生成 4 组变体（去不去括号噪声 × 去不去章节号）；
2. 对每一对变体算
   ``s = 0.65 × SequenceMatcher.ratio + 0.35 × 字符二元组 Dice``；
   一方包含另一方时 **+0.15**（封顶 1.0）；
3. 作品得分 = 所有变体对里的最高分；
4. 排序：分数降序 → 标题自然序 → 原名次序（**全部重排**，不设阈值）；
5. 高亮：取最高分那一对变体的**最长公共子串**（至少 2 字），映射回作品名的原始字符区间。

为什么用二元组 Dice 补充：中文标题常有插入语（``某漫画`` vs ``某漫画 第二部``），
纯字符级 ratio 对插入惩罚偏重，二元组重叠能把这类"包含关系"抬上来。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Iterable, Optional, Sequence

from mangaproof.poprako.models import Comic
from mangaproof.utils.natural_sort import natural_key

__all__ = [
    "RankedComic",
    "TitleMatch",
    "MIN_SPAN",
    "normalize",
    "variants",
    "dice_bigrams",
    "analyse",
    "rank_comics",
]

#: 高亮至少要有几个字（单字重合没有解释力）
MIN_SPAN = 2

#: 四组归一化变体：(去括号噪声, 去章节号)
_FLAGS: tuple[tuple[bool, bool], ...] = (
    (False, False),
    (True, False),
    (False, True),
    (True, True),
)

#: 括号噪声：汉化组 / 作者 / 分辨率这类通常写在括号里
_NOISE_RE = re.compile(r"(?:【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\))")

#: 章节 / 卷标记（只删"带标记词"的，避免误伤标题里的数字）
_CHAPTER_RE = re.compile(
    r"(?:第\s*\d+\s*[话話卷回集册季部]|(?:ch|chapter|vol|volume)\.?\s*\d+)",
    re.IGNORECASE,
)

#: 分隔符：空白与各种标点，去掉它们能显著提高相似度判定
_SEP_RE = re.compile(r"[\s_\-—–·•,，.。:：;；!！?？~～/\\|]+")


@dataclass(frozen=True, slots=True)
class TitleMatch:
    """一次名称匹配的结果：分数 + 作品名上的高亮区间（左闭右开，原始字符下标）。"""

    score: float
    span: Optional[tuple[int, int]]

    @property
    def has_span(self) -> bool:
        return self.span is not None and self.span[1] > self.span[0]


@dataclass(frozen=True, slots=True)
class RankedComic:
    """排好序的作品：原对象 + 分数 + 高亮区间。"""

    comic: Comic
    score: float
    span: Optional[tuple[int, int]]


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

def _normalize_with_map(
    text: str, *, drop_noise: bool, drop_chapter: bool
) -> tuple[str, list[int]]:
    """归一化并保留"每个归一化字符来自原文哪个下标"的映射。

    映射是高亮的关键：归一化会删括号、删章节号、删标点，删完之后要能回到原文位置。
    """
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text or ""):
        folded = unicodedata.normalize("NFKC", char).lower()
        for out_char in folded:
            chars.append(out_char)
            origin.append(index)

    def _drop(pattern: re.Pattern[str]) -> None:
        nonlocal chars, origin
        joined = "".join(chars)
        spans = [match.span() for match in pattern.finditer(joined)]
        if not spans:
            return
        drop = [False] * len(chars)
        for start, end in spans:
            for position in range(start, end):
                drop[position] = True
        kept_chars: list[str] = []
        kept_origin: list[int] = []
        for position, char in enumerate(chars):
            if not drop[position]:
                kept_chars.append(char)
                kept_origin.append(origin[position])
        chars, origin = kept_chars, kept_origin

    if drop_noise:
        _drop(_NOISE_RE)
    if drop_chapter:
        _drop(_CHAPTER_RE)
    _drop(_SEP_RE)
    return "".join(chars), origin


def normalize(
    text: str, *, drop_noise: bool = True, drop_chapter: bool = True
) -> str:
    """归一化后的字符串（默认把两类噪声都去掉）。"""
    return _normalize_with_map(text, drop_noise=drop_noise, drop_chapter=drop_chapter)[0]


def variants(text: str) -> tuple[str, ...]:
    """四组变体（去重、去空），供打分时取最高。"""
    seen: list[str] = []
    for drop_noise, drop_chapter in _FLAGS:
        value = normalize(text, drop_noise=drop_noise, drop_chapter=drop_chapter)
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def dice_bigrams(left: str, right: str) -> float:
    """字符二元组的 Dice 系数（0～1）。"""
    if not left or not right:
        return 0.0
    if len(left) < 2 or len(right) < 2:
        return 1.0 if left == right else 0.0
    left_pairs = {left[i : i + 2] for i in range(len(left) - 1)}
    right_pairs = {right[i : i + 2] for i in range(len(right) - 1)}
    overlap = len(left_pairs & right_pairs)
    return 2.0 * overlap / (len(left_pairs) + len(right_pairs))


# ---------------------------------------------------------------------------
# 打分 / 高亮
# ---------------------------------------------------------------------------

def _pair_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right).ratio()
    score = 0.65 * ratio + 0.35 * dice_bigrams(left, right)
    if left in right or right in left:
        score = min(1.0, score + 0.15)
    return score


def _longest_common_substring(left: str, right: str) -> tuple[int, int, int]:
    """返回 ``(left_start, right_start, length)``；没有公共字符时长度为 0。"""
    if not left or not right:
        return 0, 0, 0
    previous = [0] * (len(right) + 1)
    best_left = best_right = best_len = 0
    for i, left_char in enumerate(left, 1):
        current = [0] * (len(right) + 1)
        for j, right_char in enumerate(right, 1):
            if left_char == right_char:
                current[j] = previous[j - 1] + 1
                if current[j] > best_len:
                    best_len = current[j]
                    best_left = i - best_len
                    best_right = j - best_len
        previous = current
    return best_left, best_right, best_len


def analyse(task_name: str, title: str) -> TitleMatch:
    """算一个作品与任务名的匹配分数与高亮区间。"""
    best_score = 0.0
    best_left = ""
    best_flags: Optional[tuple[bool, bool]] = None

    for left_flags in _FLAGS:
        left = normalize(task_name, drop_noise=left_flags[0], drop_chapter=left_flags[1])
        if not left:
            continue
        for right_flags in _FLAGS:
            right = normalize(title, drop_noise=right_flags[0], drop_chapter=right_flags[1])
            if not right:
                continue
            score = _pair_score(left, right)
            if score > best_score:
                best_score = score
                best_left = left
                best_flags = right_flags

    if best_flags is None:
        return TitleMatch(0.0, None)

    right_text, origin = _normalize_with_map(
        title, drop_noise=best_flags[0], drop_chapter=best_flags[1]
    )
    _left_start, right_start, length = _longest_common_substring(best_left, right_text)
    if length < MIN_SPAN or right_start >= len(origin):
        return TitleMatch(best_score, None)

    start = origin[right_start]
    end = origin[right_start + length - 1] + 1
    return TitleMatch(best_score, (start, end) if end > start else None)


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------

def rank_comics(
    task_name: str,
    comics: Sequence[Comic],
    *,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[RankedComic]:
    """给作品打分并**全部重排**（分数降序 → 标题自然序 → 原名次序）。

    ``progress(done, total)`` 每 25 条回调一次（最后一条必回调），供进度窗显示。
    """
    items = list(comics)
    total = len(items)
    rows: list[tuple[float, tuple, int, Comic, Optional[tuple[int, int]]]] = []
    for index, comic in enumerate(items):
        match = analyse(task_name, comic.title)
        rows.append((match.score, natural_key(comic.title), index, comic, match.span))
        if progress is not None and ((index + 1) % 25 == 0 or index + 1 == total):
            progress(index + 1, total)

    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [RankedComic(comic=row[3], score=row[0], span=row[4]) for row in rows]


def iter_titles(entries: Iterable[RankedComic]) -> list[str]:
    """小工具：取出标题列表（测试与日志用）。"""
    return [entry.comic.title for entry in entries]
