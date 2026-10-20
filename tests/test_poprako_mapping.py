# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""本地 PSD ↔ 复合图页 匹配单测（需求 §7；设计文档 §13）。

全部纯函数，不联网。重点守两件事：

- 四级规则的**顺位**（路径 → 文件名 → stem → 数字段）；
- **歧义绝不自动选**：宁可留给用户手动选，也不能把问题标到错的页上。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.mapping import align_in_order, auto_match, normalize_ident  # noqa: E402
from mangaproof.poprako.models import PageArtwork  # noqa: E402


def page(pid: str, index: int, raw: str | None) -> PageArtwork:
    return PageArtwork(
        id=pid, index=index, raw_ident=raw, image_uploaded=True, ext="png"
    )


# ---------------------------------------------------------------- 规范化


def test_normalize_ident_unifies_separators_and_case():
    assert normalize_ident(" Ch1\\Sub\\001.PSD ") == "ch1/sub/001.psd"
    assert normalize_ident("./a/b.psd") == "a/b.psd"
    assert normalize_ident("/a/b.psd") == "a/b.psd"
    assert normalize_ident(None) == ""
    assert normalize_ident(123) == ""


# ---------------------------------------------------------------- 四级规则


def test_exact_relative_path_matches_first():
    result = auto_match(["ch1/001.psd"], [page("pa_1", 0, "ch1/001.psd")])
    assert result.mapping == {"ch1/001.psd": "pa_1"}
    assert result.unmatched_local == ()
    assert result.unmatched_remote == ()


def test_basename_matches_when_local_has_subdirectory():
    result = auto_match(["ch1/001.psd"], [page("pa_1", 0, "001.psd")])
    assert result.mapping == {"ch1/001.psd": "pa_1"}


def test_stem_matches_when_extension_differs():
    result = auto_match(["001.psd"], [page("pa_1", 0, "001")])
    assert result.mapping == {"001.psd": "pa_1"}


def test_digit_run_matches_and_ignores_leading_zeros():
    result = auto_match(["page_007.psd"], [page("pa_1", 0, "007.png")])
    assert result.mapping == {"page_007.psd": "pa_1"}


def test_no_match_when_names_are_unrelated():
    result = auto_match(["cover.psd"], [page("pa_1", 0, "001.psd")])
    assert result.mapping == {}
    assert result.unmatched_local == ("cover.psd",)
    assert [p.id for p in result.unmatched_remote] == ["pa_1"]


# ---------------------------------------------------------------- 歧义


def test_two_pages_with_same_basename_are_ambiguous():
    pages = [page("pa_1", 0, "a/001.psd"), page("pa_2", 1, "b/001.psd")]
    result = auto_match(["001.psd"], pages)

    assert result.mapping == {}
    assert result.unmatched_local == ("001.psd",)
    assert result.ambiguous == (("001.psd", ("pa_1", "pa_2")),)


def test_two_local_files_with_same_basename_are_ambiguous():
    pages = [page("pa_1", 0, "001.psd")]
    result = auto_match(["a/001.psd", "b/001.psd"], pages)

    assert result.mapping == {}
    assert set(result.unmatched_local) == {"a/001.psd", "b/001.psd"}
    assert {name for name, _ in result.ambiguous} == {"a/001.psd", "b/001.psd"}


def test_exact_path_wins_over_ambiguous_basename():
    pages = [page("pa_1", 0, "b/001.psd"), page("pa_2", 1, "a/001.psd")]
    result = auto_match(["a/001.psd", "b/001.psd"], pages)

    # 两个文件都在第一级按完整路径唯一命中，各归各的页（不会因为 basename 相同而歧义）
    assert result.mapping["a/001.psd"] == "pa_2"
    assert result.mapping["b/001.psd"] == "pa_1"


# ---------------------------------------------------------------- 其他


def test_page_without_raw_ident_stays_unmatched():
    result = auto_match(["001.psd"], [page("pa_1", 0, None)])
    assert result.mapping == {}
    assert [p.id for p in result.unmatched_remote] == ["pa_1"]


def test_unmatched_remote_is_ordered_by_index():
    pages = [page("pa_3", 2, "zzz.psd"), page("pa_1", 0, "aaa.psd"), page("pa_2", 1, "bbb.psd")]
    result = auto_match([], pages)
    assert [p.id for p in result.unmatched_remote] == ["pa_1", "pa_2", "pa_3"]


def test_empty_inputs_produce_empty_result():
    result = auto_match([], [])
    assert result.mapping == {}
    assert result.unmatched_local == ()
    assert result.unmatched_remote == ()
    assert result.matched_count == 0


def test_align_in_order_pairs_by_position():
    pages = [page("pa_1", 0, "001.psd"), page("pa_2", 1, "002.psd")]
    assert align_in_order(["b.psd", "a.psd"], pages) == {"b.psd": "pa_1", "a.psd": "pa_2"}


def test_align_in_order_stops_at_shorter_side():
    pages = [page("pa_1", 0, "001.psd")]
    assert align_in_order(["a.psd", "b.psd"], pages) == {"a.psd": "pa_1"}
    assert align_in_order([], [page("pa_1", 0, "001.psd")]) == {}
