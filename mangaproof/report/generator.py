"""MangaProof 返修单 PDF 生成（需求 §45～§54）。

- 使用纯 Python PDF 库（reportlab），与 GUI、任务逻辑完全解耦（需求 §47）；
- PDF 红框由 PDF 矢量矩形绘制，绝不截图 GUI（需求 §52）；
- 问题编号 ①②③ 与正文一一对应（需求 §53）；
- 未完成时明确标注「任务状态：未完成」（需求 §54）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional

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
from reportlab.lib.utils import ImageReader

try:  # reportlab >= 5
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
except ImportError:  # reportlab 4.x
    from reportlab.pdfbase.pdfmetrics import UnicodeCIDFont

from reportlab.pdfbase import pdfmetrics

from mangaproof.review.state import PASSED, FAILED, TaskState
from mangaproof.report import templates as T

log = logging.getLogger("mangaproof.report.generator")

ZH_FONT = "STSong-Light"
_page_w, _page_h = A4


def _register_fonts() -> None:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(ZH_FONT))
        if hasattr(pdfmetrics, "addMapping"):  # reportlab 4.x 需要；5.x 已移除
            pdfmetrics.addMapping(ZH_FONT, 0, 0, ZH_FONT)
    except Exception:
        log.warning("中文字体注册失败，PDF 中文可能显示异常")


def _zh_style(size: float, leading: Optional[float] = None) -> ParagraphStyle:
    return ParagraphStyle(
        "zh",
        fontName=ZH_FONT,
        fontSize=size,
        leading=leading if leading is not None else size * 1.4,
    )


def circled_number(n: int) -> str:
    """① ② ③ … ⑳，超过 20 用 (21) 形式。"""
    base = 0x2460
    if 1 <= n <= 20:
        return chr(base + n - 1)
    return f"({n})"


def _np_to_png_bytes(img: np.ndarray) -> bytes:
    """numpy RGBA → 白底合成 → PNG 字节。"""
    pil = Image.fromarray(img)
    if pil.mode == "RGBA":
        bg = Image.new("RGBA", pil.size, (255, 255, 255, 255))
        pil = Image.alpha_composite(bg, pil).convert("RGB")
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    buf = BytesIO()
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
) -> Path:
    """生成返修单。

    layer_ids_by_file: {相对路径: 图层 id 列表}（顺序即文档顺序）；
    image_provider(rel_path) -> PSDDocument 或 None（供提取 merged image），
    由调用方注入，生成器不依赖 GUI。
    """
    _register_fonts()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    layer_counts = {rel: len(ids) for rel, ids in layer_ids_by_file.items()}
    all_counts = task.count_all(layer_counts)
    complete = all_counts["unreviewed"] == 0

    doc = SimpleDocTemplate(
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
    story.extend(_build_cover(task, all_counts, complete))
    story.append(PageBreak())
    # ---- PSD 总览（需求 §51.2） ----
    story.extend(_build_overview(task, layer_ids_by_file))
    story.append(PageBreak())
    # ---- 问题明细（需求 §51.3、§52、§53） ----
    failed_layers = _collect_failed_layers(task)
    if not failed_layers:
        story.append(
            Paragraph(
                "本任务暂无未通过问题。",
                _zh_style(14),
            )
        )
    else:
        for i, (rel_path, layer_id, issues) in enumerate(failed_layers):
            if i > 0:
                story.append(PageBreak())
            story.extend(
                _build_layer_detail_page(task, rel_path, layer_id, issues, image_provider)
            )

    doc.build(story)
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


def _build_overview(task: TaskState, layer_ids_by_file: Dict[str, List[str]]):
    story = [Paragraph(T.OVERVIEW_TITLE, _zh_style(18, 24)), Spacer(1, 5 * mm)]

    data = [[T.COLUMN_FILE, T.COLUMN_PROGRESS, T.COLUMN_ISSUE_COUNT]]
    for record in task.files:
        rel = record.relative_path
        ids = layer_ids_by_file.get(rel, [])
        counts = task.count_file(rel, ids)
        reviewed = counts["reviewed"]
        data.append([
            Paragraph(record.file_name, _zh_style(11, 15)),
            f"{reviewed}/{counts['total']}",
            str(counts["issues"]),
        ])

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
    return story


def _collect_failed_layers(task: TaskState):
    """收集 (rel_path, layer_id, [issues])，按文件顺序、图层顺序排列。"""
    grouped: Dict[str, List] = {}
    for issue in task.issues:
        grouped.setdefault((issue.file, issue.layer_id, issue.layer_name), []).append(issue)
    order = {
        (issue.file, issue.layer_id): idx
        for idx, issue in enumerate(task.issues)
    }
    items = list(grouped.items())
    items.sort(key=lambda kv: min(i.issue_no for i in kv[1]))
    return [(k[0], k[1], v) for k, v in items]


def _build_layer_detail_page(
    task: TaskState,
    rel_path: str,
    layer_id: str,
    issues,
    image_provider,
):
    """单图层问题明细页：页面图像 + 矢量红框 + 问题编号 + 批注（需求 §52）。"""
    # 按问题编号排序
    issues = sorted(issues, key=lambda i: i.issue_no)

    layer_name = issues[0].layer_name or layer_id
    story = [
        Paragraph(
            f"{rel_path}　—　{T.LAYER_LABEL}：{layer_name}",
            _zh_style(14, 18),
        ),
        Spacer(1, 4 * mm),
    ]

    # ---- 图像 + 红框矢量图（自定义 Flowable 直接绘制，需求 §52） ----
    annotated = AnnotatedPageFlowable(issues, image_provider, rel_path)
    if annotated.image_available:
        story.append(annotated)
        story.append(Spacer(1, 5 * mm))

    # ---- 问题编号列表（需求 §53 一一对应） ----
    for issue in issues:
        label = f"{circled_number(issues.index(issue) + 1)} {T.TYPE_LABEL}：{issue.type}"
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
    图像为 PSD 自带 merged image（与红框世界坐标同坐标系）。
    """

    def __init__(self, issues, image_provider, rel_path: str):
        super().__init__()
        self.issues = sorted(issues, key=lambda i: i.issue_no)
        self.rel_path = rel_path
        self.width = 120 * mm
        self.height = 170 * mm
        self.hAlign = "CENTER"
        self.image_available = False
        self._png_bytes: Optional[bytes] = None
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
                self._png_bytes = _np_to_png_bytes(merged)
                self._img_w, self._img_h = merged.shape[1], merged.shape[0]
                self.image_available = True
            except Exception:
                log.exception("提取 merged image 失败：%s", rel_path)

    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)

    def draw(self) -> None:
        c = self.canv
        img_w, img_h = self._img_w, self._img_h
        if self._png_bytes is None or img_w <= 0 or img_h <= 0:
            return
        scale = min(self.width / img_w, self.height / img_h)
        draw_w, draw_h = img_w * scale, img_h * scale
        # drawOn 已 translate 到本 Flowable 原点
        off_x = (self.width - draw_w) / 2.0
        off_y = (self.height - draw_h) / 2.0

        c.drawImage(ImageReader(BytesIO(self._png_bytes)), off_x, off_y, draw_w, draw_h)

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
            # 纯数字 + Helvetica-Bold：canvas 路径下 CID 字体对 ① 编码异常，
            # 使用标准字体保证徽标数字可靠渲染（正文列表仍用 ① 对应）。
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(
                bx + badge_w / 2.0, by + badge_h / 2.0 - 2.4, str(n)
            )
