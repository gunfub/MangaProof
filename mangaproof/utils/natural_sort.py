"""自然排序：001.psd < 002.psd < 10.psd，而不是纯字符串排序。"""

from __future__ import annotations

import re
from functools import cmp_to_key

_SPLIT_RE = re.compile(r"(\d+)")


def natural_key(text: str):
    """返回可用于 sorted(key=...) 的自然排序键。

    将字符串拆分为「文本块 / 数字块」，数字块按整数值比较。
    """
    parts = []
    for token in _SPLIT_RE.split(str(text)):
        if token.isdigit():
            # 用 (1, int, 原字符串) 保证数字部分按数值、且前缀 0 稳定
            parts.append((1, int(token), token))
        elif token:
            parts.append((0, token.lower(), token))
    return parts


def natural_sorted(iterable):
    """按自然排序返回新列表。"""
    return sorted(iterable, key=natural_key)


def cmp_natural(a: str, b: str) -> int:
    """自然排序比较函数（旧式 comparator，需要时使用）。"""

    def _cmp(x, y):
        return (x > y) - (x < y)

    ka, kb = natural_key(a), natural_key(b)
    for x, y in zip(ka, kb):
        if x[0] != y[0]:
            # 数字块排在文本块之后（与 Windows 资源管理器行为一致）
            return _cmp(x[0], y[0])
        if x[0] == 1:
            if x[1] != y[1]:
                return _cmp(x[1], y[1])
        if x[2] != y[2]:
            return _cmp(x[2], y[2])
    return _cmp(len(ka), len(kb))


natural_sort_key = cmp_to_key(cmp_natural)
