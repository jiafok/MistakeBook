"""Export service: Markdown (incl. Obsidian), PDF, DOCX."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from docx import Document
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


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
