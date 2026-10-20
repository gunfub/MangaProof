# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""作品名匹配 / 排序 / 高亮区间单测（``poprako/title_match.py``）。

全部纯函数：不联网、不导入 Qt。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.models import Comic  # noqa: E402
from mangaproof.poprako.title_match import (  # noqa: E402
    analyse,
    dice_bigrams,
    normalize,
    rank_comics,
    variants,
)

TASK = "[木叶翻译组] 某漫画 第12话"


def comic(cid: str, title: str) -> Comic:
    return Comic(id=cid, title=title, author="", chapter_count=1, is_archived=False)


# ---------------------------------------------------------------- 归一化


def test_normalize_strips_noise_chapter_and_separators():
    assert normalize(TASK) == "某漫画"
    assert normalize("Re:Zero 第二季 第01话") == "rezero第二季"
    assert normalize("１００％") == "100%"          # NFKC：全角 → 半角
    assert normalize("[汉化组] 作品 (作者)") == "作品"


def test_variants_keep_both_noisy_and_clean_forms():
    values = variants(TASK)
    assert "某漫画" in values
    assert any("木叶" in value for value in values), "括号里的组名不该被无条件丢弃"
    assert len(values) == len(set(values))


def test_dice_bigrams_basics():
    assert dice_bigrams("", "x") == 0.0
    assert dice_bigrams("a", "a") == 1.0
    assert dice_bigrams("abcd", "abcd") == 1.0
    assert dice_bigrams("abcd", "wxyz") == 0.0
    assert 0.0 < dice_bigrams("某漫画", "某漫画第二部") < 1.0


# ---------------------------------------------------------------- 打分 / 高亮


def test_exact_title_scores_one_with_span():
    match = analyse(TASK, "某漫画")
    assert match.score == 1.0
    assert match.span == (0, 3)


def test_containment_scores_high_and_highlights_shared_part():
    match = analyse(TASK, "某漫画 第二部")
    assert match.score >= 0.7
    assert match.span == (0, 3)
    assert "某漫画 第二部"[match.span[0]:match.span[1]] == "某漫画"


def test_unrelated_title_scores_zero_without_span():
    match = analyse(TASK, "彻底无关的作品")
    assert match.score == 0.0
    assert match.span is None


def test_span_skips_bracket_noise_prefix():
    match = analyse("某漫画", "[汉化组]某漫画")
    assert match.span is not None
    start, end = match.span
    assert "[汉化组]某漫画"[start:end] == "某漫画"


def test_span_requires_at_least_two_characters():
    match = analyse("A", "A")
    assert match.score == 1.0
    assert match.span is None, "单字重合没有解释力，不标绿"


def test_separators_inside_title_are_still_highlighted():
    match = analyse("某漫画", "某 漫 画")
    assert match.span is not None
    assert match.span[1] - match.span[0] >= 3


# ---------------------------------------------------------------- 排序


def test_rank_orders_by_score_then_natural_then_original():
    comics = [
        comic("c1", "无关A"),
        comic("c2", "某漫画 第二部"),
        comic("c3", "某漫画"),
        comic("c4", "无关B"),
    ]
    ranked = rank_comics(TASK, comics)
    assert [entry.comic.id for entry in ranked] == ["c3", "c2", "c1", "c4"]
    assert ranked[0].score == 1.0
    assert ranked[0].span == (0, 3)


def test_rank_is_stable_for_equal_scores_even_without_signal():
    comics = [comic("c3", "乙作品"), comic("c1", "甲作品"), comic("c2", "丙作品")]
    ranked = rank_comics("Chapter01", comics)      # 与三者都毫无关系 → 全部 0 分
    assert all(entry.score == 0.0 for entry in ranked)
    # 全部 0 分时按标题自然序兜底（而不是原顺序），同序再按原下标
    assert [entry.comic.id for entry in ranked] == ["c2", "c3", "c1"]


def test_rank_reports_progress():
    comics = [comic(f"c{i}", f"某漫画 第{i}话") for i in range(60)]
    seen: list[tuple[int, int]] = []
    rank_comics(TASK, comics, progress=lambda done, total: seen.append((done, total)))
    assert seen, "应当有进度回调"
    assert seen[-1] == (60, 60)
    assert all(total == 60 for _done, total in seen)


def test_rank_empty_list():
    assert rank_comics(TASK, []) == []
