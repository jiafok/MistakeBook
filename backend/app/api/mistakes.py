from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import (
    AiAnalysis,
    KnowledgePoint,
    Mistake,
    Paper,
    Question,
    QuestionKnowledgePoint,
    ReviewPlan,
    Tag,
    QuestionTag,
    User,
)
from ..schemas import MistakeOut, MistakePageOut, MistakeUpdate
from ..services.dedup import content_hash
from ..services.review import create_plan

router = APIRouter(prefix="/mistakes", tags=["mistakes"])


def _analysis_for(db: Session, question_id: int) -> AiAnalysis | None:
    return db.scalar(
        select(AiAnalysis)
        .where(AiAnalysis.question_id == question_id)
        .order_by(AiAnalysis.created_at.desc())
    )


def _to_out(db: Session, mistake: Mistake) -> MistakeOut:
    question = db.get(Question, mistake.question_id)
    paper = db.get(Paper, mistake.paper_id) if mistake.paper_id else None
    analysis = _analysis_for(db, mistake.question_id)

    kps = [kp.name for kp in (question.knowledge_points if question else [])]
    tags = [t.name for t in (question.tags if question else [])]

    import json

    steps: list[str] = []
    if analysis is not None:
        try:
            parsed = json.loads(analysis.solution_steps_json or "[]")
            if isinstance(parsed, list):
                steps = [str(s) for s in parsed]
        except json.JSONDecodeError:
            steps = []

    return MistakeOut(
        id=mistake.id,
        question_id=mistake.question_id,
        label=question.label if question else "",
        text=question.text if question else "",
        answer_text=question.answer_text if question else "",
        subject=question.subject if question else "未分类",
        difficulty=analysis.difficulty if analysis else (question.difficulty if question else 3),
        knowledge_points=kps or (analysis and _json_list(analysis.knowledge_points_json)) or [],
        tags=tags,
        error_reason=analysis.error_reason if analysis else "",
        error_detail=analysis.error_detail if analysis else "",
        correct_solution=analysis.correct_solution if analysis else "",
        solution_steps=steps,
        wrong_count=mistake.wrong_count,
        wrong_probability=mistake.wrong_probability_snapshot,
        status=mistake.status,
        paper_title=paper.title if paper else "",
        created_at=mistake.created_at,
    )


def _json_list(raw: str) -> list[str]:
    import json

    try:
        parsed = json.loads(raw or "[]")
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


@router.get("", response_model=MistakePageOut)
def list_mistakes(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    q: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    knowledge_point: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    difficulty: int | None = Query(default=None),
    status_filter: str = Query(default="active", alias="status"),
    sort: str = Query(default="recent"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> MistakePageOut:
    stmt = (
        select(Mistake, Question)
        .join(Question, Question.id == Mistake.question_id)
        .where(Mistake.user_id == user.id)
    )
    if status_filter and status_filter != "all":
        stmt = stmt.where(Mistake.status == status_filter)
    if subject and subject.strip():
        stmt = stmt.where(Question.subject == subject.strip())
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(Question.text.like(needle), Question.label.like(needle)))
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)

    if knowledge_point:
        stmt = stmt.join(
            QuestionKnowledgePoint, QuestionKnowledgePoint.question_id == Question.id
        ).join(
            KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id
        ).where(KnowledgePoint.name == knowledge_point)

    if tag:
        stmt = stmt.join(QuestionTag, QuestionTag.question_id == Question.id).join(
            Tag, Tag.id == QuestionTag.tag_id
        ).where(Tag.name == tag, Tag.user_id == user.id)

    if sort == "wrong_count":
        stmt = stmt.order_by(Mistake.wrong_count.desc(), Mistake.created_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Mistake.created_at.asc())
    else:
        stmt = stmt.order_by(Mistake.created_at.desc())

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    items = [_to_out(db, mistake) for mistake, _question in rows]
    return MistakePageOut(total=int(total), page=page, page_size=page_size, items=items)


@router.get("/{mistake_id}", response_model=MistakeOut)
def get_mistake(
    mistake_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MistakeOut:
    mistake = db.get(Mistake, mistake_id)
    if mistake is None or mistake.user_id != user.id:
        raise HTTPException(status_code=404, detail="错题不存在")
    return _to_out(db, mistake)


@router.patch("/{mistake_id}", response_model=MistakeOut)
def update_mistake(
    mistake_id: int,
    payload: MistakeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MistakeOut:
    mistake = db.get(Mistake, mistake_id)
    if mistake is None or mistake.user_id != user.id:
        raise HTTPException(status_code=404, detail="错题不存在")
    question = db.get(Question, mistake.question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    if payload.subject is not None:
        question.subject = payload.subject.strip()[:64] or "未分类"
    if payload.difficulty is not None:
        question.difficulty = max(1, min(5, payload.difficulty))
    if payload.note is not None:
        mistake.note = payload.note[:2000]
    if payload.status in ("active", "archived"):
        mistake.status = payload.status

    analysis = _analysis_for(db, mistake.question_id)
    if analysis is not None:
        if payload.error_reason is not None:
            analysis.error_reason = payload.error_reason.strip()[:64]
        if payload.correct_solution is not None:
            analysis.correct_solution = payload.correct_solution[:4000]

    if payload.knowledge_points is not None:
        for existing in list(question.knowledge_points):
            question.knowledge_points.remove(existing)
        for name in payload.knowledge_points[:8]:
            name = name.strip()[:128]
            if not name:
                continue
            kp = db.scalar(
                select(KnowledgePoint).where(
                    KnowledgePoint.user_id == user.id,
                    KnowledgePoint.name == name,
                    KnowledgePoint.subject == question.subject,
                )
            )
            if kp is None:
                kp = KnowledgePoint(user_id=user.id, name=name, subject=question.subject)
                db.add(kp)
                db.flush()
            question.knowledge_points.append(kp)

    if payload.tags is not None:
        for existing in list(question.tags):
            question.tags.remove(existing)
        for name in payload.tags[:12]:
            name = name.strip()[:64]
            if not name:
                continue
            tag = db.scalar(select(Tag).where(Tag.user_id == user.id, Tag.name == name))
            if tag is None:
                tag = Tag(user_id=user.id, name=name)
                db.add(tag)
                db.flush()
            question.tags.append(tag)

    db.commit()
    db.refresh(mistake)
    return _to_out(db, mistake)


@router.delete("/{mistake_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mistake(
    mistake_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    mistake = db.get(Mistake, mistake_id)
    if mistake is None or mistake.user_id != user.id:
        raise HTTPException(status_code=404, detail="错题不存在")
    db.delete(mistake)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{mistake_id}/review-plan", status_code=status.HTTP_201_CREATED)
def ensure_review_plan(
    mistake_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    mistake = db.get(Mistake, mistake_id)
    if mistake is None or mistake.user_id != user.id:
        raise HTTPException(status_code=404, detail="错题不存在")
    existing = db.scalar(
        select(ReviewPlan).where(
            ReviewPlan.mistake_id == mistake.id, ReviewPlan.status == "pending"
        )
    )
    if existing is None:
        create_plan(db, mistake)
        db.commit()
    return {"ok": True}
