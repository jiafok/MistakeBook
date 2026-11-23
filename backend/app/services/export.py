"""Export service: Markdown (incl. Obsidian), PDF, DOCX."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from docx import Document
from docx.shared import Mm as DocxMm
from docx.shared import Pt
from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import FILL_EVEN_ODD
from reportlab.platypus import Flowable
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .redraw import Drawing, render_png, trace_figure

# A picture lifted out of the paper is stored at ~1.6x the page's own pixels, so
# its pixel size is roughly 96 DPI. Millimetres keep both printers in step.
_MM_PER_PX = 25.4 / 96.0

# Printing width for a figure: never wider than the text column, never so tall
# that one picture owns the page.
_FIGURE_MAX_WIDTH_MM = 120.0
_FIGURE_MAX_HEIGHT_MM = 90.0

# Formats that cannot carry vectors get the trace rendered at print resolution.
_RASTER_DPI = 300.0
_MAX_RASTER_SIDE = 2600


@dataclass
class ExportFigure:
    """One drawing from the original paper, ready to be printed.

    ``data`` is the crop taken from the paper, but it is never what reaches the
    page: the crop is traced into polygons on first use (``drawing()``) and the
    page is built from that geometry. The crop stays around so an untraceable
    picture can still be printed rather than silently dropped.
    """

    data: bytes
    width: int = 0
    height: int = 0
    # Words printed inside the picture, stored in crop pixels.
    labels: list[dict[str, object]] = field(default_factory=list)
    _drawing: Drawing | None = field(default=None, init=False, repr=False, compare=False)
    _traced: bool = field(default=False, init=False, repr=False, compare=False)
    _png: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def drawing(self) -> Drawing | None:
        """The traced geometry, or None when the crop cannot be traced."""
        if not self._traced:
            self._drawing = trace_figure(self.data, self.labels) if self.data else None
            self._traced = True
        return self._drawing

    def pixel_size(self) -> tuple[int, int]:
        width, height = self.width, self.height
        if width > 0 and height > 0:
            return width, height
        drawing = self.drawing()
        if drawing is not None:
            return drawing.width, drawing.height
        try:
            with PILImage.open(io.BytesIO(self.data)) as opened:
                return opened.size
        except Exception:
            return max(1, width), max(1, height)

    def display_mm(self) -> tuple[float, float]:
        width_px, height_px = self.pixel_size()
        scale = min(
            _FIGURE_MAX_WIDTH_MM / (width_px * _MM_PER_PX),
            _FIGURE_MAX_HEIGHT_MM / (height_px * _MM_PER_PX),
            1.0,
        )
        return width_px * _MM_PER_PX * scale, height_px * _MM_PER_PX * scale

    def printable_png(self) -> bytes:
        """Print-resolution PNG of the *redrawn* picture, for non-vector formats."""
        if self._png is not None:
            return self._png
        drawing = self.drawing()
        if drawing is None:
            self._png = self.data
            return self._png
        scale = _RASTER_DPI / 96.0
        longest = max(drawing.width, drawing.height) * scale
        if longest > _MAX_RASTER_SIDE:
            scale *= _MAX_RASTER_SIDE / longest
        self._png = render_png(drawing, scale)
        return self._png


@dataclass
class ExportItem:
    label: str
    text: str
    answer: str = ""
    subject: str = ""
    difficulty: int = 3
    knowledge_points: list[str] | None = None
    error_reason: str = ""
    error_detail: str = ""
    solution: str = ""
    steps: list[str] | None = None
    wrong_count: int = 1
    paper_title: str = ""
    created_at: datetime | None = None
    # Drawings that belong with the question text. The text itself is never a
    # picture, so the book stays reflowable and searchable.
    figures: list[ExportFigure] = field(default_factory=list)


def _esc_md(text: str) -> str:
    return text.replace("\r\n", "\n")


def to_markdown(items: Iterable[ExportItem], title: str, obsidian: bool = False) -> str:
    lines: list[str] = []
    if obsidian:
        lines.append("---")
        lines.append(f"title: {title}")
        lines.append(f"created: {datetime.now().strftime('%Y-%m-%d')}")
        lines.append("tags:")
        lines.append("  - 错题本")
        lines.append("---")
        lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    for idx, item in enumerate(items, start=1):
        heading = f"## {idx}. {item.label or '题目'}"
        lines.append(heading)
        lines.append("")
        meta: list[str] = []
        if item.subject:
            meta.append(f"学科：{item.subject}")
        if item.knowledge_points:
            meta.append(f"知识点：{'、'.join(item.knowledge_points)}")
        meta.append(f"难度：{item.difficulty}/5")
        meta.append(f"错误次数：{item.wrong_count}")
        if item.paper_title:
            meta.append(f"来源：{item.paper_title}")
        lines.append("> " + " ｜ ".join(meta))
        lines.append("")
        lines.append(_esc_md(item.text) or "（无题干文本）")
        lines.append("")
        # The export is a single self-contained file, so pictures are inlined as
        # data URIs instead of pointing at paths that would not travel with it.
        for fig_no, figure in enumerate(item.figures, start=1):
            encoded = base64.b64encode(figure.printable_png()).decode("ascii")
            lines.append(f"![题图 {fig_no}](data:image/png;base64,{encoded})")
            lines.append("")
        if item.answer:
            lines.append(f"**学生答案**：{_esc_md(item.answer)}")
            lines.append("")
        if item.error_reason:
            lines.append(f"**错误原因**：{item.error_reason}")
            if item.error_detail:
                lines.append("")
                lines.append(_esc_md(item.error_detail))
            lines.append("")
        if item.solution:
            lines.append("**正确解法**")
            lines.append("")
            lines.append(_esc_md(item.solution))
            lines.append("")
        if item.steps:
            lines.append("**解题步骤**")
            lines.append("")
            for step_no, step in enumerate(item.steps, start=1):
                lines.append(f"{step_no}. {_esc_md(step)}")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def to_pdf(items: list[ExportItem], title: str) -> bytes:
    buffer = io.BytesIO()
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font_name = "STSong-Light"
    except Exception:
        font_name = "Helvetica"

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName=font_name, fontSize=10.5, leading=16
    )
    h1 = ParagraphStyle("H1", parent=body, fontSize=18, leading=24, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=body, fontSize=13, leading=20, spaceBefore=8, spaceAfter=4)
    meta = ParagraphStyle("Meta", parent=body, fontSize=9, textColor="#555555", leading=13)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
    )
    flow = [Paragraph(_safe(title), h1), Spacer(1, 6)]

    for idx, item in enumerate(items, start=1):
        if idx > 1:
            flow.append(PageBreak())
        flow.append(Paragraph(_safe(f"{idx}. {item.label or '题目'}"), h2))
        bits: list[str] = []
        if item.subject:
            bits.append(f"学科：{item.subject}")
        if item.knowledge_points:
            bits.append(f"知识点：{'、'.join(item.knowledge_points)}")
        bits.append(f"难度：{item.difficulty}/5")
        bits.append(f"错误次数：{item.wrong_count}")
        if item.paper_title:
            bits.append(f"来源：{item.paper_title}")
        flow.append(Paragraph(_safe(" ｜ ".join(bits)), meta))
        flow.append(Spacer(1, 6))
        for paragraph in (item.text or "（无题干文本）").split("\n"):
            flow.append(Paragraph(_safe(paragraph), body))
        for figure in item.figures:
            flow.append(Spacer(1, 4))
            flow.append(_pdf_figure(figure, font_name))
        if item.answer:
            flow.append(Spacer(1, 4))
            flow.append(Paragraph(_safe(f"学生答案：{item.answer}"), body))
        if item.error_reason:
            flow.append(Spacer(1, 4))
            flow.append(Paragraph(_safe(f"错误原因：{item.error_reason}"), body))
        if item.error_detail:
            flow.append(Paragraph(_safe(item.error_detail), body))
        if item.solution:
            flow.append(Spacer(1, 4))
            flow.append(Paragraph(_safe("正确解法"), h2))
            for paragraph in item.solution.split("\n"):
                flow.append(Paragraph(_safe(paragraph), body))
        if item.steps:
            flow.append(Spacer(1, 4))
            flow.append(Paragraph(_safe("解题步骤"), h2))
            for step_no, step in enumerate(item.steps, start=1):
                flow.append(Paragraph(_safe(f"{step_no}. {step}"), body))

    doc.build(flow)
    return buffer.getvalue()


def _pdf_figure(figure: ExportFigure, font_name: str) -> Flowable:
    """A figure for the PDF: real vector geometry, or the crop if untraceable."""
    width_mm, height_mm = figure.display_mm()
    drawing = figure.drawing()
    if drawing is None:
        return PdfImage(
            io.BytesIO(figure.data),
            width=width_mm * mm,
            height=height_mm * mm,
        )
    return _VectorFigure(drawing, width_mm * mm, height_mm * mm, font_name)


class _VectorFigure(Flowable):
    """Draw a traced picture as PDF path operations, so it prints at any size."""

    def __init__(self, drawing: Drawing, width: float, height: float, font_name: str) -> None:
        super().__init__()
        self._drawing = drawing
        self.width = width
        self.height = height
        self._font_name = font_name

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        drawing = self._drawing
        scale_x = self.width / max(1, drawing.width)
        scale_y = self.height / max(1, drawing.height)

        def to_page(x: float, y: float) -> tuple[float, float]:
            # Pixel space grows downwards, PDF space upwards.
            return x * scale_x, self.height - y * scale_y

        canvas.saveState()
        path = canvas.beginPath()
        for ring in drawing.art_rings():
            pairs = ring.as_pairs()
            if len(pairs) < 3:
                continue
            path.moveTo(*to_page(*pairs[0]))
            for point in pairs[1:]:
                path.lineTo(*to_page(*point))
            path.close()
        # Even-odd fill makes a contour's nesting depth decide ink vs hole, which is
        # exactly how the binary ink mask looked.
        canvas.drawPath(path, stroke=0, fill=1, fillMode=FILL_EVEN_ODD)

        # Words inside the picture print as words, not as traced outlines: an 8 pt
        # glyph loses its shape long before its outline does.
        for label in drawing.labels:
            size = label.height * scale_y
            if size < 1.0:
                continue
            canvas.setFont(self._font_name, size)
            canvas.setFillColorRGB(0, 0, 0)
            _, baseline = to_page(label.x, label.y + label.height)
            canvas.drawString(label.x * scale_x, baseline, label.text)
        canvas.restoreState()


def _safe(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def to_docx(items: list[ExportItem], title: str) -> bytes:
    document = Document()
    style = document.styles["Normal"]
    style.font.size = Pt(10.5)

    document.add_heading(title, level=0)

    for idx, item in enumerate(items, start=1):
        document.add_heading(f"{idx}. {item.label or '题目'}", level=1)
        meta_parts: list[str] = []
        if item.subject:
            meta_parts.append(f"学科：{item.subject}")
        if item.knowledge_points:
            meta_parts.append(f"知识点：{'、'.join(item.knowledge_points)}")
        meta_parts.append(f"难度：{item.difficulty}/5")
        meta_parts.append(f"错误次数：{item.wrong_count}")
        if item.paper_title:
            meta_parts.append(f"来源：{item.paper_title}")
        document.add_paragraph(" ｜ ".join(meta_parts))

        for paragraph in (item.text or "（无题干文本）").split("\n"):
            document.add_paragraph(paragraph)
        for figure in item.figures:
            width_mm, _ = figure.display_mm()
            document.add_picture(io.BytesIO(figure.printable_png()), width=DocxMm(width_mm))
        if item.answer:
            document.add_paragraph(f"学生答案：{item.answer}")
        if item.error_reason:
            paragraph = document.add_paragraph()
            paragraph.add_run("错误原因：").bold = True
            paragraph.add_run(item.error_reason)
        if item.error_detail:
            document.add_paragraph(item.error_detail)
        if item.solution:
            document.add_heading("正确解法", level=2)
            for line in item.solution.split("\n"):
                document.add_paragraph(line)
        if item.steps:
            document.add_heading("解题步骤", level=2)
            for step_no, step in enumerate(item.steps, start=1):
                document.add_paragraph(f"{step_no}. {step}", style="List Number")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def safe_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")
    return cleaned[:80] or "mistakes"
