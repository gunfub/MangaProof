# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""元数据转换单测（需求 §8、§9；设计文档 §8、§13）。

不联网。重点：

- 归一化公式、越界钳制、退化/空框的区分与计数；
- 严格四键（服务端 ``additionalProperties: false``）；
- 问题顺序 = 本地编号顺序（决定服务端 ``index``）；
- 三种未映射情形的**阻塞/不阻塞**判断；
- 未映射远端页默认保留、可切回清空。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.convert import (  # noqa: E402
    RECT_EPS,
    build_payload,
    normalize_rect,
    read_psd_size,
)
from mangaproof.poprako.models import PageArtwork, Rect, RemoteIssue  # noqa: E402
from mangaproof.review.issue import Issue  # noqa: E402

DATA = ROOT / "tests" / "data" / "chapter01"
CANVAS = (1024, 1536)


def page(pid: str, index: int, raw: str | None = None) -> PageArtwork:
    return PageArtwork(
        id=pid, index=index, raw_ident=raw or f"{index + 1:03d}.psd",
        image_uploaded=True, ext="png",
    )


def issue(
    file: str,
    no: int,
    *,
    type_: str = "文字位置错误",
    layer: str = "对白图层",
    comment: str = "向左移动",
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
) -> Issue:
    return Issue(
        file=file, layer_id="0.1", layer_name=layer, type=type_,
        comment=comment, rect=rect, issue_no=no,
    )


def remote(pid: str, index: int, *, variant="文字位置", note="旧批注") -> RemoteIssue:
    return RemoteIssue(
        id=f"ri_{pid}_{index}", page_artwork_id=pid, index=index, variant=variant,
        layer_name="对白", rect=Rect(0.1, 0.1, 0.2, 0.2), note=note,
    )


def issues_of(page_payload: dict) -> list[dict]:
    return page_payload["issues"]


# ---------------------------------------------------------------- 画布尺寸


def test_read_psd_size_matches_psd_tools_for_real_file():
    assert read_psd_size(DATA / "001.psd") == (1024, 1536)


def test_read_psd_size_returns_none_for_non_psd(tmp_path: Path):
    text = tmp_path / "note.psd"
    text.write_text("not a psd", encoding="utf-8")
    assert read_psd_size(text) is None
    assert read_psd_size(tmp_path / "missing.psd") is None


# ---------------------------------------------------------------- 归一化


def test_normalize_rect_converts_pixels_to_unit_square():
    result = normalize_rect((102.4, 153.6, 204.8, 307.2), CANVAS)
    assert result.rect == {
        "x_coord": 0.1, "y_coord": 0.1, "width": 0.2, "height": 0.2,
    }
    assert result.clamped is False and result.dropped is False


def test_normalize_rect_treats_zero_box_as_empty():
    result = normalize_rect((0.0, 0.0, 0.0, 0.0), CANVAS)
    assert result.empty is True
    assert result.rect is None and result.dropped is False


def test_normalize_rect_drops_degenerate_one_dimension():
    result = normalize_rect((10.0, 10.0, 100.0, 0.0), CANVAS)
    assert result.dropped is True
    assert result.rect is None


def test_normalize_rect_clamps_out_of_bounds_and_keeps_edge_at_one():
    result = normalize_rect((1000.0, 1500.0, 200.0, 200.0), CANVAS)
    assert result.clamped is True
    rect = result.rect
    assert rect is not None
    assert rect["x_coord"] + rect["width"] <= 1.0
    assert rect["y_coord"] + rect["height"] <= 1.0


def test_normalize_rect_rounding_never_exceeds_one():
    result = normalize_rect((1023.0, 1535.0, 2.0, 2.0), CANVAS)
    rect = result.rect
    assert rect is not None
    assert rect["x_coord"] + rect["width"] <= 1.0 + 1e-12
    assert rect["y_coord"] + rect["height"] <= 1.0 + 1e-12


def test_normalize_rect_rejects_non_finite():
    result = normalize_rect((float("nan"), 0.0, 10.0, 10.0), CANVAS)
    assert result.dropped is True


# ---------------------------------------------------------------- payload


def test_payload_uses_strict_four_keys_and_sorted_issues():
    rows = [
        issue("001.psd", 3, type_=" 漏字 ", layer="  ", comment="多行\n批注",
              rect=(102.4, 153.6, 204.8, 307.2)),
        issue("001.psd", 1, type_="   ", layer="气泡", comment=""),
    ]
    payload, report = build_payload(
        files=["001.psd"], issues=rows, base_dir=DATA,
        mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
    )

    assert len(payload["pages"]) == 1
    items = issues_of(payload["pages"][0])
    assert [item["variant"] for item in items] == ["问题", "漏字"], "按编号升序发送"
    assert items[0]["layer_name"] == "气泡"
    assert "layer_name" not in items[1], "空白图层名必须整键省略"
    assert items[1]["note"] == "多行\n批注"
    for item in items:
        assert set(item) <= {"variant", "note", "layer_name", "rect"}
        assert "variant" in item and "note" in item
    assert report.issues_written == 2 and report.is_blocked is False


def test_payload_includes_mapped_page_with_zero_issues():
    payload, report = build_payload(
        files=["001.psd"], issues=[], base_dir=DATA,
        mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
    )
    assert payload["pages"] == [{"page_artwork_id": "pa_1", "issues": []}]
    assert report.pages_written == 1 and report.issues_written == 0


def test_payload_orders_pages_by_remote_index():
    payload, _report = build_payload(
        files=["a.psd", "b.psd"], issues=[], base_dir=DATA,
        mapping={"a.psd": "pa_late", "b.psd": "pa_early"},
        pages=[page("pa_late", 5, "a.psd"), page("pa_early", 0, "b.psd")],
        sizes={"a.psd": CANVAS, "b.psd": CANVAS},
    )
    assert [row["page_artwork_id"] for row in payload["pages"]] == ["pa_early", "pa_late"]


def test_payload_uses_given_sizes_and_reads_header_otherwise():
    payload, report = build_payload(
        files=["001.psd", "002.psd"], issues=[
            issue("001.psd", 1, rect=(512.0, 768.0, 512.0, 768.0)),
            issue("002.psd", 2, rect=(0.0, 0.0, 1024.0, 1536.0)),
        ],
        base_dir=DATA,
        mapping={"001.psd": "pa_1", "002.psd": "pa_2"},
        pages=[page("pa_1", 0), page("pa_2", 1)],
        sizes={"001.psd": CANVAS},          # 002 走真实文件头
    )
    assert report.missing_sizes == []
    first = issues_of(payload["pages"][0])[0]["rect"]
    second = issues_of(payload["pages"][1])[0]["rect"]
    assert first == {"x_coord": 0.5, "y_coord": 0.5, "width": 0.5, "height": 0.5}
    assert second == {"x_coord": 0.0, "y_coord": 0.0, "width": 1.0, "height": 1.0}


# ---------------------------------------------------------------- 未映射本地页


def test_unmapped_local_page_with_issues_blocks_upload():
    payload, report = build_payload(
        files=["001.psd", "002.psd"],
        issues=[issue("001.psd", 1), issue("002.psd", 2), issue("002.psd", 3)],
        base_dir=DATA, mapping={"001.psd": "pa_1"},
        pages=[page("pa_1", 0), page("pa_2", 1)],
    )
    assert report.is_blocked is True
    assert report.unmapped_with_issues == [("002.psd", 2)]
    assert any("002.psd" in line for line in report.blocking)
    assert payload["pages"][0]["page_artwork_id"] == "pa_1"


def test_unmapped_local_page_without_issues_does_not_block():
    _payload, report = build_payload(
        files=["001.psd", "002.psd"], issues=[issue("001.psd", 1)],
        base_dir=DATA, mapping={"001.psd": "pa_1"},
        pages=[page("pa_1", 0), page("pa_2", 1)],
    )
    assert report.is_blocked is False
    assert report.unmapped_without_issues == ["002.psd"]


def test_empty_mapping_blocks():
    _payload, report = build_payload(
        files=["001.psd"], issues=[], base_dir=DATA, mapping={}, pages=[page("pa_1", 0)],
    )
    assert report.is_blocked is True
    assert any("映射" in line for line in report.blocking)


def test_duplicate_mapping_blocks():
    _payload, report = build_payload(
        files=["001.psd", "002.psd"], issues=[], base_dir=DATA,
        mapping={"001.psd": "pa_1", "002.psd": "pa_1"},
        pages=[page("pa_1", 0)],
        sizes={"001.psd": CANVAS, "002.psd": CANVAS},
    )
    assert report.is_blocked is True
    assert any("映射重复" in line for line in report.blocking)


def test_stale_mapping_entry_blocks():
    _payload, report = build_payload(
        files=["001.psd"], issues=[], base_dir=DATA,
        mapping={"gone.psd": "pa_1", "001.psd": "pa_1"},
        pages=[page("pa_1", 0)], sizes={"001.psd": CANVAS},
    )
    assert report.is_blocked is True
    assert any("不存在的文件" in line for line in report.blocking)


# ---------------------------------------------------------------- 画布尺寸缺失


def test_missing_canvas_size_blocks_when_box_present(tmp_path: Path):
    _payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1, rect=(10.0, 10.0, 20.0, 20.0))],
        base_dir=tmp_path,
        mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
    )
    assert report.is_blocked is True
    assert report.missing_sizes == ["001.psd"]
    assert report.dropped_rects == 1


def test_missing_canvas_size_ok_when_no_box(tmp_path: Path):
    payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1)],
        base_dir=tmp_path,
        mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
    )
    assert report.is_blocked is False
    assert "rect" not in issues_of(payload["pages"][0])[0]


# ---------------------------------------------------------------- 未映射远端页


def test_unmapped_remote_page_keeps_existing_issues_by_default():
    payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1)],
        base_dir=DATA, mapping={"001.psd": "pa_1"},
        pages=[page("pa_1", 0), page("pa_2", 1)],
        remote_issues=[remote("pa_2", 0), remote("pa_2", 1)],
    )
    ids = [row["page_artwork_id"] for row in payload["pages"]]
    assert ids == ["pa_1", "pa_2"]
    assert report.pages_preserved == 1 and report.issues_preserved == 2
    assert report.pages_cleared == []
    kept = issues_of(payload["pages"][1])
    assert [item["variant"] for item in kept] == ["文字位置", "文字位置"]
    assert kept[0]["rect"] == {"x_coord": 0.1, "y_coord": 0.1, "width": 0.2, "height": 0.2}


def test_unmapped_remote_page_can_be_cleared_explicitly():
    payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1)],
        base_dir=DATA, mapping={"001.psd": "pa_1"},
        pages=[page("pa_1", 0), page("pa_2", 1)],
        remote_issues=[remote("pa_2", 0)],
        preserve_unmapped=False,
    )
    assert [row["page_artwork_id"] for row in payload["pages"]] == ["pa_1"]
    assert report.pages_preserved == 0
    assert report.pages_cleared == [(page("pa_2", 1).display_name, 1)]


def test_remote_issue_rect_is_revalidated_on_preserve():
    bad = RemoteIssue(
        id="ri_bad", page_artwork_id="pa_2", index=0, variant="x", layer_name="",
        rect=Rect(0.9, 0.9, 0.5, 0.5), note="越界",
    )
    payload, _report = build_payload(
        files=["p.psd"], issues=[], base_dir=DATA,
        mapping={"p.psd": "pa_1"}, pages=[page("pa_1", 0), page("pa_2", 1)],
        remote_issues=[bad], sizes={"p.psd": CANVAS},
    )
    rect = issues_of(payload["pages"][1])[0]["rect"]
    assert rect["x_coord"] + rect["width"] <= 1.0
    assert rect["y_coord"] + rect["height"] <= 1.0


# ---------------------------------------------------------------- 体积


def test_payload_size_warning_uses_configurable_threshold():
    rows = [issue("001.psd", i, comment="x" * 200) for i in range(1, 31)]
    _payload, report = build_payload(
        files=["001.psd"], issues=rows, base_dir=DATA,
        mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
        max_payload_bytes=100,
    )
    assert report.estimated_bytes > 100
    assert report.warnings and "预警线" in report.warnings[0]


def test_payload_is_json_serializable_and_utf8_sized():
    payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1, comment="中文批注")],
        base_dir=DATA, mapping={"001.psd": "pa_1"}, pages=[page("pa_1", 0)],
    )
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert report.estimated_bytes == len(raw)
    assert "中文批注" in raw.decode("utf-8")


def test_rect_epsilon_constant_is_small_enough():
    assert RECT_EPS <= 1e-6


@pytest.mark.parametrize("bad_rect", [(), (1.0, 2.0)])
def test_malformed_rect_is_dropped_not_crashing(bad_rect):
    result = normalize_rect(bad_rect, CANVAS)
    assert result.dropped is True


def test_page_without_uploaded_image_only_warns():
    empty_page = PageArtwork(
        id="pa_1", index=0, raw_ident="001.psd", image_uploaded=False, ext=None
    )
    _payload, report = build_payload(
        files=["001.psd"], issues=[issue("001.psd", 1)],
        base_dir=DATA, mapping={"001.psd": "pa_1"}, pages=[empty_page],
    )
    assert report.is_blocked is False
    assert any("还没有上传复合图图片" in line for line in report.warnings)
