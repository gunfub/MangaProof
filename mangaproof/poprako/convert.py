# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof 问题 → Poprako 导入 JSON（纯函数；需求 §8，设计文档 §8）。

这里是整条链路里**唯一**决定"发什么字节给平台"的地方，因此三条规则写死在本模块：

1. 每条问题只允许出现 ``variant / layer_name / rect / note`` 四个键
   （服务端 ``additionalProperties: false``，多一个字段整批 422）；
2. 红框用**该 PSD 画布像素宽高**归一化到 ``[0,1]``，越界钳制、退化的直接省略；
3. 未映射的远端页默认**保留**其现有问题（原样重发），而不是让它们被整体替换清空。

不导入 Qt、不发网络。
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from mangaproof.poprako.models import PageArtwork, RemoteIssue
from mangaproof.review.issue import Issue

__all__ = [
    "MAX_PAYLOAD_BYTES",
    "RECT_EPS",
    "PreflightReport",
    "RectResult",
    "read_psd_size",
    "normalize_rect",
    "build_payload",
]

#: 请求体预警线。服务端 HTTP body 上限未公开，超过只警告、不截断、不拆分。
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024

#: 矩形视为"退化"的阈值（归一化坐标）
RECT_EPS = 1e-6

#: 归一化坐标保留的小数位
_DECIMALS = 6

#: PSD/PSB 文件头固定 26 字节（4+2+6+2+4+4+2+2）
_PSD_HEADER_LEN = 26
_PSD_SIGNATURE = b"8BPS"


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RectResult:
    """一次矩形归一化的结局（四选一，计数用）。"""

    rect: Optional[dict[str, float]]
    clamped: bool = False     # 越界后被钳制（仍然发）
    dropped: bool = False     # 真正退化 / 脏数据（省略 rect）
    empty: bool = False       # 本来就没画框（静默省略，不计数）


@dataclass
class PreflightReport:
    """上传前的本地预检结论。``blocking`` 非空即禁止上传。"""

    pages_written: int = 0
    issues_written: int = 0
    #: 将被清空的远端页：(页显示名, 旧问题数)
    pages_cleared: list[tuple[str, int]] = field(default_factory=list)
    pages_preserved: int = 0
    issues_preserved: int = 0
    #: 本地有未映射的页且该页含问题：(文件名, 问题数)
    unmapped_with_issues: list[tuple[str, int]] = field(default_factory=list)
    unmapped_without_issues: list[str] = field(default_factory=list)
    missing_sizes: list[str] = field(default_factory=list)
    clamped_rects: int = 0
    dropped_rects: int = 0
    estimated_bytes: int = 0
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocking)

    @property
    def pages_total(self) -> int:
        return self.pages_written + self.pages_preserved


# ---------------------------------------------------------------------------
# 画布尺寸
# ---------------------------------------------------------------------------

def read_psd_size(path: Path) -> Optional[tuple[int, int]]:
    """只读 PSD/PSB 文件头拿画布尺寸，**不解析整份 PSD**。

    头布局（大端）：``8BPS`` + version(2) + reserved(6) + channels(2)
    + height(4) + width(4) + depth(2) + mode(2)，共 26 字节；PSB 的 version 为 2，
    但这两处的偏移与字节宽度相同。
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(_PSD_HEADER_LEN)
    except OSError:
        return None
    if len(header) < _PSD_HEADER_LEN or header[:4] != _PSD_SIGNATURE:
        return None
    try:
        height, width = struct.unpack(">II", header[14:22])
    except struct.error:  # pragma: no cover —— 长度已校验
        return None
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


# ---------------------------------------------------------------------------
# 矩形归一化
# ---------------------------------------------------------------------------

def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def normalize_rect(rect: Iterable[float], size: tuple[int, int]) -> RectResult:
    """像素矩形 ``(x, y, w, h)`` → 归一化 ``{x_coord,y_coord,width,height}``。

    - 宽高都为 0（没画框）→ :attr:`RectResult.empty`，静默省略；
    - 只有一个维度为 0、非有限值、钳制后为零 → :attr:`RectResult.dropped`；
    - 越界但仍有面积 → 钳制后照发，并标 :attr:`RectResult.clamped`；
    - 取整后重新把右/下边压回 1，避免 ``x + width`` 因为四舍五入变成 1.000001。
    """
    try:
        x, y, width_px, height_px = (float(value) for value in rect)
    except (TypeError, ValueError):
        return RectResult(None, dropped=True)

    if not _finite(x, y, width_px, height_px):
        return RectResult(None, dropped=True)
    if width_px <= RECT_EPS and height_px <= RECT_EPS:
        return RectResult(None, empty=True)
    if width_px <= RECT_EPS or height_px <= RECT_EPS:
        return RectResult(None, dropped=True)

    canvas_w, canvas_h = int(size[0]), int(size[1])
    if canvas_w <= 0 or canvas_h <= 0:
        return RectResult(None, dropped=True)

    nx, ny = x / canvas_w, y / canvas_h
    nw, nh = width_px / canvas_w, height_px / canvas_h
    clamped = nx < 0.0 or ny < 0.0 or nx + nw > 1.0 or ny + nh > 1.0

    rx = round(_clamp01(nx), _DECIMALS)
    ry = round(_clamp01(ny), _DECIMALS)
    # 取整后再用 1 - rx 兜一次底，保证 x + width <= 1
    rw = min(round(max(nw, 0.0), _DECIMALS), round(1.0 - rx, _DECIMALS))
    rh = min(round(max(nh, 0.0), _DECIMALS), round(1.0 - ry, _DECIMALS))
    if rw <= 0.0 or rh <= 0.0:
        return RectResult(None, clamped=clamped, dropped=True)

    return RectResult(
        {"x_coord": rx, "y_coord": ry, "width": rw, "height": rh}, clamped=clamped
    )


def _has_box(rect: Iterable[float]) -> bool:
    """该问题是否画了框（只判宽高，不看位置）。"""
    try:
        _x, _y, width_px, height_px = (float(value) for value in rect)
    except (TypeError, ValueError):
        return False
    return _finite(width_px, height_px) and (
        width_px > RECT_EPS or height_px > RECT_EPS
    )


# ---------------------------------------------------------------------------
# 组装 payload
# ---------------------------------------------------------------------------

def _variant_of(issue: Issue) -> str:
    return (issue.type or "").strip() or "问题"


def _local_issue(issue: Issue, size: Optional[tuple[int, int]], report: PreflightReport) -> dict:
    """把一条本地问题转成 ``IssueInstr``（严格四键）。"""
    item: dict = {"variant": _variant_of(issue), "note": issue.comment or ""}

    layer = (issue.layer_name or "").strip()
    if layer:
        item["layer_name"] = layer

    if size is None:
        # 读不到画布尺寸：退化框照常省略；非退化框已在调用方记入 blocking
        if _has_box(issue.rect):
            report.dropped_rects += 1
        return item

    result = normalize_rect(issue.rect, size)
    if result.clamped:
        report.clamped_rects += 1
    if result.dropped:
        report.dropped_rects += 1
    if result.rect is not None:
        item["rect"] = result.rect
    return item


def _remote_issue(issue: RemoteIssue) -> dict:
    """把远端现有问题原样重建（保留未映射页时用）。"""
    item: dict = {"variant": (issue.variant or "").strip() or "问题", "note": issue.note or ""}
    layer = (issue.layer_name or "").strip()
    if layer:
        item["layer_name"] = layer
    if issue.rect is not None:
        # 远端矩形本身已是归一化坐标：以 (1, 1) 为"画布"再校验一遍
        result = normalize_rect(
            (issue.rect.x, issue.rect.y, issue.rect.width, issue.rect.height), (1, 1)
        )
        if result.rect is not None:
            item["rect"] = result.rect
    return item


def build_payload(
    *,
    files: Sequence[str],
    issues: Iterable[Issue],
    base_dir: Path,
    mapping: Mapping[str, str],
    pages: Sequence[PageArtwork],
    remote_issues: Iterable[RemoteIssue] = (),
    preserve_unmapped: bool = True,
    sizes: Optional[Mapping[str, tuple[int, int]]] = None,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> tuple[dict, PreflightReport]:
    """生成 ``{"pages": [...]}`` 与预检报告。

    ``sizes`` 可传入已缓存的画布尺寸（相对路径 → (w, h)）；缺失的会就地读文件头。
    报告里的 ``blocking`` 非空时**不得上传**（调用方负责拦）。
    """
    report = PreflightReport()
    file_list = list(files)
    file_set = set(file_list)
    ordered_pages = sorted(pages, key=lambda page: (page.index, page.id))
    page_by_id = {page.id: page for page in ordered_pages}
    size_cache: dict[str, tuple[int, int]] = dict(sizes or {})

    # ---- 1. 校验映射（重复 / 失效 / 陈旧） --------------------------------
    first_owner: dict[str, str] = {}
    for name, page_id in mapping.items():
        if name not in file_set:
            report.blocking.append(f"映射里有任务中不存在的文件：{name}")
            continue
        page = page_by_id.get(page_id)
        if page is None:
            report.blocking.append(f"{name} 的映射指向已不存在的复合图页")
            continue
        owner = first_owner.get(page_id)
        if owner is not None:
            report.blocking.append(
                f"映射重复：{owner} 与 {name} 都指向「{page.display_name}」"
            )
            continue
        first_owner[page_id] = name

    valid_mapping = {
        name: page_id
        for page_id, name in first_owner.items()
        if name in file_set and page_id in page_by_id
    }
    if not valid_mapping:
        report.blocking.append("还没有任何页面映射")

    # ---- 2. 本地问题按文件分组（编号升序，保证两端 index 一致） ------------
    issues_by_file: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.file in file_set:
            issues_by_file.setdefault(issue.file, []).append(issue)
    for rows in issues_by_file.values():
        rows.sort(key=lambda issue: (issue.issue_no, issue.issue_id))

    # ---- 3. 远端现有问题按页分组 -----------------------------------------
    remote_by_page: dict[str, list[RemoteIssue]] = {}
    for item in remote_issues:
        remote_by_page.setdefault(item.page_artwork_id, []).append(item)
    for rows in remote_by_page.values():
        rows.sort(key=lambda item: (item.index, item.id))

    # ---- 4. 已映射页：本地为唯一真相（0 条也显式发送） --------------------
    payload_pages: list[tuple[int, dict]] = []
    for page_id, name in first_owner.items():
        if page_id not in page_by_id or name not in file_set:
            continue
        page = page_by_id[page_id]
        rows = issues_by_file.get(name, [])

        size = size_cache.get(name)
        if size is None:
            size = read_psd_size(Path(base_dir) / name)
            if size is not None:
                size_cache[name] = size
        if size is None:
            report.missing_sizes.append(name)
            if any(_has_box(issue.rect) for issue in rows):
                report.blocking.append(f"{name} 读不到画布尺寸，无法归一化红框")

        if not page.image_uploaded:
            report.warnings.append(
                f"{page.display_name} 还没有上传复合图图片，问题会挂在空页上"
            )

        out_issues = [_local_issue(issue, size, report) for issue in rows]
        payload_pages.append(
            (page.index, {"page_artwork_id": page_id, "issues": out_issues})
        )
        report.pages_written += 1
        report.issues_written += len(out_issues)

    # ---- 5. 未映射的远端页：保留 / 清空 ----------------------------------
    mapped_ids = set(first_owner)
    for page in ordered_pages:
        if page.id in mapped_ids:
            continue
        existing = remote_by_page.get(page.id, [])
        if not existing:
            continue                      # 本来就没问题，清空也无损失
        if preserve_unmapped:
            out_issues = [_remote_issue(item) for item in existing]
            payload_pages.append(
                (page.index, {"page_artwork_id": page.id, "issues": out_issues})
            )
            report.pages_preserved += 1
            report.issues_preserved += len(out_issues)
        else:
            report.pages_cleared.append((page.display_name, len(existing)))

    # ---- 6. 未映射的本地页 ----------------------------------------------
    for name in file_list:
        if name in valid_mapping:
            continue
        count = len(issues_by_file.get(name, []))
        if count:
            report.unmapped_with_issues.append((name, count))
            report.blocking.append(f"本地 {name} 有 {count} 条问题，但还没有对应复合图页")
        else:
            report.unmapped_without_issues.append(name)

    # ---- 7. 排序 + 体积 --------------------------------------------------
    payload_pages.sort(key=lambda row: row[0])
    payload = {"pages": [page for _index, page in payload_pages]}
    report.estimated_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if report.estimated_bytes > max_payload_bytes:
        mb = report.estimated_bytes / (1024 * 1024)
        limit_mb = max_payload_bytes / (1024 * 1024)
        report.warnings.append(
            f"导入请求约 {mb:.1f} MB，超过 {limit_mb:.0f} MB 预警线；服务端可能拒绝"
        )

    return payload, report
