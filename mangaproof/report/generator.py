# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof 返修单 PDF 生成（需求 §45～§54）。

- 使用纯 Python PDF 库（reportlab），与 GUI、任务逻辑完全解耦（需求 §47）；
- 字体与界面一致：优先内嵌程序自带的 MiSans（font/MiSans-Medium.ttf，
  与 Qt 界面同一份文件），字体缺失时回退 reportlab 内置 CID 宋体；
- PDF 红框由 PDF 矢量矩形绘制，绝不截图 GUI（需求 §52）；
- 问题编号 ①②③ 与正文一一对应（需求 §53）；
- 未完成时明确标注「任务状态：未完成」（需求 §54）；
- 可选 progress_cb(done, total, message) 汇报页面级进度，供 GUI 进度框
  显示（大任务不阻塞界面）；回调内抛出 ReportCancelled 即中断生成，
  此时 PDF 尚未开始落盘，目标文件不受影响。
"""

from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.utils import ImageReader

try:  # reportlab >= 5
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
except ImportError:  # reportlab 4.x
    from reportlab.pdfbase.pdfmetrics import UnicodeCIDFont

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from mangaproof.review.state import PASSED, FAILED, TaskState
from mangaproof.report import templates as T

log = logging.getLogger("mangaproof.report.generator")

# 正文字体：优先 MiSans（程序自带，与界面同一字体），缺失时回退内置 CID 宋体。
# 二者均以「字体名」字符串在 reportlab 中注册，下面的模块级变量在
# _register_fonts() 后指向实际生效的字体名（样式表在调用时读取）。
MISANS_NAME = "MiSans"
ZH_FONT_FALLBACK = "STSong-Light"
ZH_FONT = ZH_FONT_FALLBACK
# 徽标数字字体：CID 字体在 canvas 路径下编码不可靠（① 等字符异常），
# 回退时保持标准 Helvetica-Bold；MiSans 可用时一并切换为 MiSans。
BADGE_FONT_FALLBACK = "Helvetica-Bold"
BADGE_FONT = BADGE_FONT_FALLBACK

# ①～⑳ 在当前字体中的覆盖上限（MiSans 只覆盖 ①～⑩，其余回退 (11) 写法，
# 避免 PDF 中出现缺字空白；CID 宋体按 GB 字符集同样保守取 10）
_circled_max = 10
_font_ready = False

_page_w, _page_h = A4

# 进度回调：(已完成步数, 总步数, 阶段说明)
ProgressCb = Callable[[int, int, str], None]


class ReportCancelled(Exception):
    """用户取消生成返修单（由 progress_cb 抛出，逐级向上传播）。"""


def _emit(
    progress_cb: Optional[ProgressCb], done: int, total: int, message: str
) -> None:
    """上报进度（progress_cb 未提供时为无操作）。"""
    if progress_cb is not None:
        progress_cb(done, total, message)


class _BookmarkParagraph(Paragraph):
    """带书签/目录信息的段落。

    afterFlowable 只拿到绘制完的 flowable，因此把「书签键 / 目录标题 / 层级 /
    是否进目录」挂在段落对象上；页码由文档模板按**实际落页**回填，而不是
    预先估算——内容自动换页后页码依然准确。
    """

    def __init__(
        self,
        text: str,
        style: ParagraphStyle,
        bookmark_key: str = "",
        bookmark_title: str = "",
        bookmark_level: int = 0,
        in_toc: bool = True,
    ):
        super().__init__(text, style)
        self.bookmark_key = bookmark_key
        self.bookmark_title = bookmark_title or text
        self.bookmark_level = int(bookmark_level)
        self.bookmark_in_toc = in_toc


class ReportDocTemplate(SimpleDocTemplate):
    """返修单文档模板：构建时收集目录条目 + 写入 PDF 书签（大纲）。

    目录页码来自 afterFlowable（flowable 真正绘制完成之后），配合 multiBuild
    反复排版直到目录稳定：内容变长自动换页、目录本身占页变化都不会错位。
    """

    def afterFlowable(self, flowable) -> None:
        key = getattr(flowable, "bookmark_key", "")
        if not key:
            return
        title = getattr(flowable, "bookmark_title", "") or key
        level = int(getattr(flowable, "bookmark_level", 0))
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=level, closed=(level > 0))
        if getattr(flowable, "bookmark_in_toc", True):
            self.notify("TOCEntry", (level, title, self.page, key))


def _build_toc() -> TableOfContents:
    """目录 flowable（样式跟正文一致，使用当前中文字体）。"""
    toc = TableOfContents(dotsMinLevel=0)
    toc.levelStyles = [
        ParagraphStyle(
            "toc0", fontName=ZH_FONT, fontSize=12, leading=21,
            spaceBefore=3, leftIndent=0, firstLineIndent=0,
        ),
        ParagraphStyle(
            "toc1", fontName=ZH_FONT, fontSize=10.5, leading=18,
            spaceBefore=0, leftIndent=14, firstLineIndent=0,
        ),
    ]
    return toc


def _register_fonts() -> None:
    """注册返修单字体（同进程只做一次）。

    优先 MiSans（与界面同一份字体文件，内嵌子集不影响体积），
    找不到或注册失败时回退 reportlab 内置 CID 宋体，保证 PDF 始终可生成。
    """
    global _font_ready
    if _font_ready:
        return
    _font_ready = True          # 失败也不重试：避免每次导出重复解析字体表
    if _register_misans():
        return

    try:
        pdfmetrics.registerFont(UnicodeCIDFont(ZH_FONT_FALLBACK))
        if hasattr(pdfmetrics, "addMapping"):  # reportlab 4.x 需要；5.x 已移除
            pdfmetrics.addMapping(ZH_FONT_FALLBACK, 0, 0, ZH_FONT_FALLBACK)
    except Exception:
        log.warning("中文字体注册失败，PDF 中文可能显示异常")


def _register_misans() -> bool:
    """注册 MiSans 并切换正文/徽标字体；成功返回 True。"""
    global ZH_FONT, BADGE_FONT, _circled_max

    from mangaproof.fonts import find_font_path

    path = find_font_path()
    if path is None:
        log.warning("未找到 MiSans 字体，返修单回退内置宋体")
        return False
    try:
        pdfmetrics.registerFont(TTFont(MISANS_NAME, str(path)))
        face = pdfmetrics.getFont(MISANS_NAME).face
    except Exception:
        log.warning("MiSans 字体注册失败（%s），返修单回退内置宋体", path, exc_info=True)
        return False

    ZH_FONT = MISANS_NAME
    BADGE_FONT = MISANS_NAME
    _circled_max = _covered_circled_max(face)
    log.info("返修单 PDF 使用 MiSans 字体：%s（①～⑳ 覆盖到 %d）", path, _circled_max)
    return True


def _covered_circled_max(face) -> int:
    """字体实际覆盖到的 ①～⑳ 上限（缺字时回退 (11) 写法，不出现空白方块）。"""
    char_to_glyph = getattr(face, "charToGlyph", None) or {}
    for n in range(20, 0, -1):
        if char_to_glyph.get(0x2460 + n - 1):
            return n
    return 0


def _zh_style(size: float, leading: Optional[float] = None) -> ParagraphStyle:
    return ParagraphStyle(
        "zh",
        fontName=ZH_FONT,
        fontSize=size,
        leading=leading if leading is not None else size * 1.4,
    )


def circled_number(n: int) -> str:
    """① ② ③ …，超出当前字体覆盖范围时用 (11) 形式。"""
    base = 0x2460
    if 1 <= n <= _circled_max:
        return chr(base + n - 1)
    return f"({n})"


def _encode_page_image(
    img: np.ndarray, image_format: str = "png", quality: int = 80
) -> bytes:
    """numpy RGBA → 白底合成 → 图片字节（PNG 无损 / JPEG 压缩）。

    JPEG 体积远小于 PNG（漫画页面常见大幅网点/渐变），代价是有损压缩：
    质量由 ``quality`` 控制（40～95）。透明区域统一合成到白底，
    与 PDF 页面背景一致。
    """
    pil = Image.fromarray(img)
    if pil.mode == "RGBA":
        bg = Image.new("RGBA", pil.size, (255, 255, 255, 255))
        pil = Image.alpha_composite(bg, pil).convert("RGB")
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    buf = BytesIO()
    if image_format == "jpeg":
        pil.save(buf, format="JPEG", quality=int(quality), optimize=True)
    else:
        pil.save(buf, format="PNG")
    return buf.getvalue()


def resolve_report_path(base_dir: Path, custom_name: str, default_name: str) -> Path:
    """解析返修单输出路径（需求 §48、§49、§49.1）。

    - 用户输入含 .pdf → 不得出现 .pdf.pdf；
    - 未自定义 → 单 PSD 用 PSD 名，文件夹用文件夹名。
    """
    name = (custom_name or "").strip()
    if not name:
        name = default_name
    if name.lower().endswith(".pdf"):
        filename = name
    else:
        filename = name + ".pdf"
    return base_dir / filename


def default_report_name(
    task_type: str, base_dir: Path, psd_file_name: str = ""
) -> str:
    """默认返修单名称（需求 §49.1 + 固定路径格式优化）。

    自动嵌字脚本的固定输出路径为 .../<章节名>/output/*.psd：
    当 PSD 所在文件夹名为 output（不区分大小写）时，
    默认名取上一级文件夹名（即章节名），而不是 "output"。
    """
    if task_type == "single":
        folder = base_dir                       # PSD 所在目录
        if folder.name.lower() == "output" and folder.parent.name:
            return folder.parent.name
        return Path(psd_file_name or "report").stem

    folder = base_dir                           # 打开的文件夹
    if folder.name.lower() == "output" and folder.parent.name:
        return folder.parent.name
    return folder.name


def generate_report(
    task: TaskState,
    layer_ids_by_file: Dict[str, List[str]],
    report_path: Path,
    image_provider: Callable[[str], Optional[object]],
    progress_cb: Optional[ProgressCb] = None,
    image_format: str = "png",
    image_quality: int = 80,
    hide_clean_files: bool = True,
) -> Path:
    """生成返修单。

    layer_ids_by_file: {相对路径: 图层 id 列表}（顺序即文档顺序）；
    image_provider(rel_path) -> PSDDocument 或 None（供提取 merged image），
    由调用方注入，生成器不依赖 GUI；
    progress_cb(done, total, message)：页面级进度（耗时步骤见下），
    回调内抛出 ReportCancelled 可中断生成；
    image_format："png"（无损，默认）或 "jpeg"（有损压缩，体积小），
    image_quality：JPEG 质量 40～95；
    hide_clean_files：PSD 总览表是否隐藏「全部通过且无问题」的页（默认隐藏，
    表下会注明隐藏数量；未通过 / 未监制的页始终保留）。

    问题明细页按 PSD（页）合并：同一页的所有问题共用一张页面图像，
    按图层分组列在图像下方，不再「一个问题一张图/一页」。
    """
    # 进度步数：准备 1 步 + 封面/总览 1 步 + 每个问题明细页 1 步 + 写入 PDF 1 步
    detail_pages = _collect_failed_pages(task, layer_ids_by_file)
    total_steps = 3 + len(detail_pages)

    _emit(progress_cb, 0, total_steps, "准备返修单…")
    _register_fonts()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    all_counts = task.count_all(layer_ids_by_file)
    complete = all_counts["unreviewed"] == 0

    doc = ReportDocTemplate(
        str(report_path),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=T.REPORT_TITLE,
    )
    story = []

    # ---- 首页（需求 §51.1） ----
    _emit(progress_cb, 1, total_steps, "生成封面与总览…")
    story.extend(_build_cover(task, all_counts, complete))
    story.append(PageBreak())

    # ---- 目录（需求 §52 定位）：有明细页时才插入；页码由 multiBuild 回填 ----
    if detail_pages:
        story.append(
            _BookmarkParagraph(
                T.TOC_TITLE, _zh_style(18, 24), bookmark_key="toc", in_toc=False
            )
        )
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(T.TOC_HINT, _zh_style(9, 12)))
        story.append(Spacer(1, 4 * mm))
        story.append(_build_toc())
        story.append(PageBreak())

    # ---- PSD 总览（需求 §51.2） ----
    story.extend(_build_overview(task, layer_ids_by_file, hide_clean_files))
    story.append(PageBreak())

    # ---- 问题明细（需求 §51.3、§52、§53）：最耗时的一步（提取 merged +
    #      编码图片），按 PSD 页上报进度，取消请求在页边界生效 ----
    if not detail_pages:
        story.append(
            Paragraph(
                "本任务暂无未通过问题。",
                _zh_style(14),
            )
        )
    else:
        for i, (rel_path, layer_groups, page_issues) in enumerate(detail_pages):
            _emit(
                progress_cb,
                2 + i,
                total_steps,
                "生成问题明细页"
                f"（{i + 1}/{len(detail_pages)}）：{rel_path}"
                f"　—　{T.LABEL_ISSUES} {len(page_issues)} 处",
            )
            if i > 0:
                story.append(PageBreak())
            story.extend(
                _build_page_detail_page(
                    rel_path, layer_groups, page_issues, image_provider,
                    image_format, image_quality, page_index=i,
                )
            )

    # ---- 写入 PDF（reportlab 一次性落盘，中途不打断，避免半成品文件） ----
    # multiBuild：目录页码来自实际落页，需多次排版直到条目与页码稳定
    #（内容自动换页 / 目录自身占页变化都能收敛）；无目录时为单趟。
    _emit(progress_cb, total_steps - 1, total_steps, f"写入 PDF：{report_path.name}")
    doc.multiBuild(story)
    _emit(progress_cb, total_steps, total_steps, "生成完成")
    return report_path


# ---------------------------------------------------------------------------
# 各页面
# ---------------------------------------------------------------------------

def _build_cover(task: TaskState, counts: dict, complete: bool):
    title_style = ParagraphStyle(
        "title", fontName=ZH_FONT, fontSize=30, leading=40,
        alignment=1, textColor=colors.HexColor("#C0392B"),
    )
    story = [
        Spacer(1, 40 * mm),
        Paragraph(T.REPORT_TITLE, title_style),
        Spacer(1, 6 * mm),
        Paragraph(T.REPORT_TITLE_EN, _zh_style(12)),
        Spacer(1, 18 * mm),
    ]

    status_text = T.STATUS_COMPLETE if complete else T.STATUS_INCOMPLETE
    status_color = colors.HexColor("#1E8449") if complete else colors.HexColor("#C0392B")

    rows = [
        (T.LABEL_TASK, task.task_name),
        (T.LABEL_GENERATED_AT, datetime.now().strftime("%Y-%m-%d %H:%M")),
        (T.LABEL_STATUS, status_text),
        (T.LABEL_PSD_COUNT, str(counts["files"])),
        (T.LABEL_LAYER_COUNT, str(counts["total"])),
        (T.LABEL_PASSED, str(counts["passed"])),
        (T.LABEL_FAILED, str(counts["failed"])),
        (T.LABEL_UNREVIEWED, str(counts["unreviewed"])),
    ]
    if not complete:
        rows.append(
            (T.LABEL_REVIEWED, f"{counts['reviewed']} / {counts['total']}")
        )

    for label, value in rows:
        story.append(
            Table(
                [[Paragraph(label, _zh_style(13, 20)), Paragraph(value, _zh_style(13, 20))]],
                colWidths=[40 * mm, 80 * mm],
                style=TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#999999")),
                ]),
            )
        )
        story.append(Spacer(1, 3 * mm))

    return story


def _is_clean_file(counts: dict) -> bool:
    """PSD 是否「全部通过且无问题」（总览表可隐藏的干净页）。"""
    return (
        counts["issues"] == 0
        and counts["failed"] == 0
        and counts["unreviewed"] == 0
    )


def _overview_rows(task: TaskState, layer_ids_by_file: Dict[str, List[str]],
                   hide_clean_files: bool = False):
    """PSD 总览表数据行 + 被隐藏的干净页数量。

    干净页 = 全部图层通过且没有任何问题；未监制或存在未通过图层的页
    始终保留（返修单需要如实反映未完成/待修内容）。
    """
    rows = []
    hidden = 0
    for record in task.files:
        rel = record.relative_path
        ids = layer_ids_by_file.get(rel, [])
        counts = task.count_file(rel, ids)
        if hide_clean_files and _is_clean_file(counts):
            hidden += 1
            continue
        rows.append([
            Paragraph(record.file_name, _zh_style(11, 15)),
            f"{counts['reviewed']}/{counts['total']}",
            str(counts["issues"]),
        ])
    return rows, hidden


def _build_overview(task: TaskState, layer_ids_by_file: Dict[str, List[str]],
                    hide_clean_files: bool = False):
    story = [
        _BookmarkParagraph(
            T.OVERVIEW_TITLE, _zh_style(18, 24), bookmark_key="overview"
        ),
        Spacer(1, 5 * mm),
    ]

    rows, hidden = _overview_rows(task, layer_ids_by_file, hide_clean_files)
    if not rows:
        story.append(Paragraph(T.OVERVIEW_ALL_CLEAN, _zh_style(12, 16)))
        return story

    data = [[T.COLUMN_FILE, T.COLUMN_PROGRESS, T.COLUMN_ISSUE_COUNT]] + rows

    table = Table(data, colWidths=[70 * mm, 40 * mm, 40 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), ZH_FONT),
        ("FONTNAME", (0, 1), (-1, -1), ZH_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    if hidden:
        # 隐藏了干净页时如实说明，避免看报告的人以为漏页
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(
                T.OVERVIEW_HIDDEN_FMT.format(hidden),
                _zh_style(9, 12),
            )
        )
    return story


def _collect_failed_pages(task: TaskState, layer_ids_by_file: Dict[str, List[str]]):
    """按 PSD（页）收集问题，返回明细页清单。

    返回 ``[(rel_path, [(layer_id, layer_name, [issues]), ...], [issues]), ...]``：
    - 外层按文档顺序（PSD 顺序），每个 PSD 只出现一次（同一页的问题共用
      一张页面图像，不再一个图层/一个问题占一页）；
    - 内层图层组同样按文档顺序（图层顺序），组内按问题编号排序；
    - 末项是该页全部问题的扁平列表（顺序与图层组一致），供红框徽标编号使用。
    """
    grouped: Dict[Tuple[str, str, str], List] = {}
    for issue in task.issues:
        grouped.setdefault((issue.file, issue.layer_id, issue.layer_name), []).append(issue)

    file_rank = {r.relative_path: idx for idx, r in enumerate(task.files)}
    layer_rank = {
        rel: {lid: idx for idx, lid in enumerate(ids)}
        for rel, ids in layer_ids_by_file.items()
    }
    missing = 1 << 30

    def sort_key(kv):
        (rel, layer_id, _name), issues = kv
        return (
            file_rank.get(rel, missing),
            layer_rank.get(rel, {}).get(layer_id, missing),
            min(i.issue_no for i in issues),
        )

    pages: Dict[str, List] = {}
    for key, issues in sorted(grouped.items(), key=sort_key):
        rel, layer_id, layer_name = key
        pages.setdefault(rel, []).append(
            (layer_id, layer_name or layer_id, sorted(issues, key=lambda i: i.issue_no))
        )

    ordered: List[str] = []
    for record in task.files:
        if record.relative_path in pages:
            ordered.append(record.relative_path)
    ordered.extend(rel for rel in pages if rel not in set(ordered))

    result = []
    for rel in ordered:
        layer_groups = pages[rel]
        flat = [issue for _lid, _name, issues in layer_groups for issue in issues]
        result.append((rel, layer_groups, flat))
    return result


def _build_page_detail_page(
    rel_path: str,
    layer_groups,
    page_issues,
    image_provider,
    image_format: str = "png",
    image_quality: int = 80,
    page_index: int = 0,
):
    """单 PSD（页）问题明细：一张页面图像 + 全部红框 + 按图层分组的问题列表。

    编号在页内连续（① ② ③ …，顺序与图层组一致），与红框徽标一一对应
    （需求 §52、§53）；同一页的多个图层共用这一张图，避免重复占页。
    """
    layer_count = len(layer_groups)
    header = (
        f"{rel_path}　—　{T.LABEL_ISSUES} {len(page_issues)} 处"
        + (f"　·　{layer_count} 个图层" if layer_count > 1 else "")
    )
    story = [
        _BookmarkParagraph(
            header,
            _zh_style(14, 18),
            bookmark_key=f"page-{page_index}",
            bookmark_title=f"{rel_path}（{T.LABEL_ISSUES} {len(page_issues)} 处）",
        ),
        Spacer(1, 4 * mm),
    ]

    # ---- 图像 + 红框矢量图（自定义 Flowable 直接绘制，需求 §52） ----
    annotated = AnnotatedPageFlowable(
        page_issues, image_provider, rel_path, image_format, image_quality
    )
    if annotated.image_available:
        story.append(annotated)
        story.append(Spacer(1, 5 * mm))

    # ---- 问题编号列表（需求 §53 一一对应）：按图层分组，编号页内连续 ----
    number = 0
    for layer_id, layer_name, issues in layer_groups:
        if layer_count > 1:
            story.append(
                Spacer(1, 1 * mm)
                if number == 0
                else Spacer(1, 3 * mm)
            )
            story.append(
                _BookmarkParagraph(
                    f"{T.LAYER_LABEL}：{layer_name}",
                    _zh_style(12, 16),
                    bookmark_key=f"page-{page_index}-layer-{number}",
                    bookmark_title=f"{T.LAYER_LABEL}：{layer_name}",
                    bookmark_level=1,
                )
            )
        for issue in issues:
            number += 1
            label = f"{circled_number(number)} {T.TYPE_LABEL}：{issue.type}"
            story.append(Paragraph(label, _zh_style(12, 17)))
            if issue.comment:
                story.append(
                    Paragraph(
                        f"　　{T.COMMENT_LABEL}：{issue.comment}",
                        _zh_style(11, 16),
                    )
                )
            story.append(Spacer(1, 2 * mm))

    return story


class AnnotatedPageFlowable(Flowable):
    """「页面图像 + PDF 矢量红框 + 问题编号」Flowable（需求 §52、§53）。

    直接使用 PDF 自身的矢量矩形与字体，绝不截图 GUI。
    图像为 PSD 自带 merged image（与红框世界坐标同坐标系）；
    传入的问题顺序即页内编号顺序（1 起）。
    """

    def __init__(
        self,
        issues,
        image_provider,
        rel_path: str,
        image_format: str = "png",
        image_quality: int = 80,
    ):
        super().__init__()
        self.issues = list(issues)
        self.rel_path = rel_path
        self.width = 120 * mm
        self.height = 170 * mm
        self.hAlign = "CENTER"
        self.image_available = False
        self._image_bytes: Optional[bytes] = None
        self._img_w = 0
        self._img_h = 0

        doc_obj = None
        try:
            doc_obj = image_provider(rel_path)
        except Exception:
            log.exception("读取 PSD 图像失败：%s", rel_path)
        if doc_obj is not None:
            try:
                merged = doc_obj.merged_np()
                self._image_bytes = _encode_page_image(
                    merged, image_format, image_quality
                )
                self._img_w, self._img_h = merged.shape[1], merged.shape[0]
                self.image_available = True
            except Exception:
                log.exception("提取 merged image 失败：%s", rel_path)

    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)

    def draw(self) -> None:
        c = self.canv
        img_w, img_h = self._img_w, self._img_h
        if self._image_bytes is None or img_w <= 0 or img_h <= 0:
            return
        scale = min(self.width / img_w, self.height / img_h)
        draw_w, draw_h = img_w * scale, img_h * scale
        # drawOn 已 translate 到本 Flowable 原点
        off_x = (self.width - draw_w) / 2.0
        off_y = (self.height - draw_h) / 2.0

        c.drawImage(ImageReader(BytesIO(self._image_bytes)), off_x, off_y, draw_w, draw_h)

        # PDF 矢量红框（需求 §52）：世界坐标 → PDF 坐标（y 轴向上）
        for idx, issue in enumerate(self.issues):
            x, y, w, h = issue.rect
            if w <= 0 or h <= 0:
                continue
            px = off_x + x * scale
            py = off_y + (img_h - y - h) * scale
            rect_top = off_y + (img_h - y) * scale
            c.setStrokeColor(colors.HexColor("#E53935"))
            c.setLineWidth(2.0)
            c.rect(px, py, w * scale, h * scale, stroke=1, fill=0)

            # 问题编号徽标（需求 §53）：红底圆角矩形 + 白色数字，
            # 与 UI Viewer 一致放在红框左上角外侧；空间不足时退到框内。
            n = idx + 1
            badge_w, badge_h, badge_r = 8 * mm, 6 * mm, 1.2 * mm
            gap = 1.2 * mm
            bx = max(off_x + 0.5 * mm, min(px, off_x + self.width - badge_w - 0.5 * mm))
            if rect_top + gap + badge_h <= off_y + self.height - 0.5 * mm:
                by = rect_top + gap          # 框外上方（与 UI 一致）
            else:
                by = rect_top - badge_h - gap  # 空间不足 → 框内左上角
            c.setFillColor(colors.HexColor("#E53935"))
            c.roundRect(bx, by, badge_w, badge_h, badge_r, stroke=0, fill=1)
            # 纯数字 + 当前字体（MiSans 可用时与正文一致；回退到 CID 宋体时
            # canvas 路径下 ① 等字符编码不可靠，故用标准 Helvetica-Bold）。
            c.setFillColor(colors.white)
            c.setFont(BADGE_FONT, 7)
            c.drawCentredString(
                bx + badge_w / 2.0, by + badge_h / 2.0 - 2.4, str(n)
            )
