from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import AiAnalysis, Mistake, Paper, Question, User
from ..services.export import (
    ExportFigure,
    ExportItem,
    safe_filename,
    to_docx,
    to_markdown,
    to_pdf,
)
from ..services.figures import load_figure_set
from ..storage import StorageError, get_storage

router = APIRouter(prefix="/export", tags=["export"])

MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "markdown": "text/markdown; charset=utf-8",
    "obsidian": "text/markdown; charset=utf-8",
}


def _load_figures(mistake: Mistake) -> list[ExportFigure]:
    """Read the stored figure crops so they can be embedded in the export."""
    figures: list[ExportFigure] = []
    storage = get_storage()
    for item in load_figure_set(mistake.figures_json) or []:
        key = str(item.get("key") or "")
        if not key:
            continue
        try:
            data = storage.read(key)
        except StorageError:
            # A missing picture must not break the whole export; the text is the
            # authoritative question and still prints correctly.
            continue
        labels = item.get("labels")
        figures.append(
            ExportFigure(
                data=data,
                width=int(item.get("width", 0) or 0),
                height=int(item.get("height", 0) or 0),
                labels=[entry for entry in labels if isinstance(entry, dict)]
                if isinstance(labels, list)
                else [],
            )
        )
    return figures


def _collect(db: Session, user: User, paper_id: int | None, subject: str | None) -> list[ExportItem]:
    stmt = select(Mistake).where(Mistake.user_id == user.id, Mistake.status == "active")
    if paper_id:
        stmt = stmt.where(Mistake.paper_id == paper_id)
    mistakes = list(db.scalars(stmt.order_by(Mistake.created_at.desc())))

    items: list[ExportItem] = []
    for mistake in mistakes:
        question = db.get(Question, mistake.question_id)
        if question is None:
            continue
        if subject and question.subject != subject:
            continue
        paper = db.get(Paper, mistake.paper_id) if mistake.paper_id else None
        analysis = db.scalar(
            select(AiAnalysis)
            .where(AiAnalysis.question_id == question.id)
            .order_by(AiAnalysis.created_at.desc())
        )
        steps: list[str] = []
        kps = [kp.name for kp in question.knowledge_points]
        if analysis is not None:
            try:
                parsed = json.loads(analysis.solution_steps_json or "[]")
                steps = [str(x) for x in parsed] if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                steps = []
            if not kps:
                try:
                    parsed_kps = json.loads(analysis.knowledge_points_json or "[]")
                    kps = [str(x) for x in parsed_kps] if isinstance(parsed_kps, list) else []
                except json.JSONDecodeError:
                    kps = []

        items.append(
            ExportItem(
                label=question.label or "题目",
                text=question.text,
                answer=question.answer_text,
                subject=question.subject,
                difficulty=analysis.difficulty if analysis else question.difficulty,
                knowledge_points=kps,
                error_reason=analysis.error_reason if analysis else "",
                error_detail=analysis.error_detail if analysis else "",
                solution=analysis.correct_solution if analysis else "",
                steps=steps,
                wrong_count=mistake.wrong_count,
                paper_title=paper.title if paper else "",
                created_at=mistake.created_at,
                figures=_load_figures(mistake),
            )
        )
    return items


@router.get("/{fmt}")
def export_mistakes(
    fmt: str,
    paper_id: int | None = Query(default=None),
    subject: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    if fmt not in MEDIA:
        raise HTTPException(status_code=400, detail="不支持的导出格式")

    items = _collect(db, user, paper_id, subject)
    if not items:
        raise HTTPException(status_code=404, detail="没有可导出的错题")

    stamp = datetime.now().strftime("%Y%m%d")
    title = f"错题本 {stamp}"

    if fmt in ("markdown", "obsidian"):
        body = to_markdown(items, title, obsidian=(fmt == "obsidian")).encode("utf-8")
        ext = "md"
    elif fmt == "pdf":
        body = to_pdf(items, title)
        ext = "pdf"
    else:
        body = to_docx(items, title)
        ext = "docx"

    filename = f"{safe_filename(title)}.{ext}"
    # HTTP headers are latin-1; RFC 5987 keeps non-ASCII names intact.
    from urllib.parse import quote

    ascii_fallback = f"mistakes-{stamp}.{ext}"
    disposition = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(body, media_type=MEDIA[fmt], headers={"Content-Disposition": disposition})
