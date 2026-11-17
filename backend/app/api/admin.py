"""Administrator oversight: read-only visibility into other accounts.

Design decision: oversight is strictly **read-only**. An admin can inspect another
account's papers, mistakes and analytics to supervise learning, but cannot create,
edit or delete their content. This keeps "监管查看" from becoming silent data
tampering, and keeps every mutation attributable to the account that owns it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import (
    KnowledgePoint,
    Mistake,
    Paper,
    Question,
    QuestionKnowledgePoint,
    ReviewPlan,
    User,
)
from ..schemas import MistakeOut, MistakePageOut, PaperOut, UserOut

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问监管视图")


def _target_user(db: Session, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return target


@router.get("/users", response_model=list[dict])
def list_users(
    db: Session = Depends(get_db), admin: User = Depends(get_current_user)
) -> list[dict]:
    _require_admin(admin)
    users = list(db.scalars(select(User).order_by(User.created_at.asc())))

    paper_counts = dict(
        db.execute(select(Paper.user_id, func.count(Paper.id)).group_by(Paper.user_id)).all()
    )
    mistake_counts = dict(
        db.execute(
            select(Mistake.user_id, func.count(Mistake.id))
            .where(Mistake.status == "active")
            .group_by(Mistake.user_id)
        ).all()
    )
    pending_reviews = dict(
        db.execute(
            select(ReviewPlan.user_id, func.count(ReviewPlan.id))
            .where(ReviewPlan.status == "pending")
            .group_by(ReviewPlan.user_id)
        ).all()
    )

    out: list[dict] = []
    for item in users:
        out.append(
            {
                "id": item.id,
                "username": item.username,
                "email": item.email,
                "display_name": item.display_name,
                "role": item.role,
                "is_active": item.is_active,
                "created_at": item.created_at,
                "paper_count": int(paper_counts.get(item.id, 0)),
                "mistake_count": int(mistake_counts.get(item.id, 0)),
                "pending_review_count": int(pending_reviews.get(item.id, 0)),
                "is_self": item.id == admin.id,
            }
        )
    return out


@router.get("/users/{user_id}/summary", response_model=dict)
def user_summary(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> dict:
    _require_admin(admin)
    target = _target_user(db, user_id)

    paper_count = db.scalar(select(func.count(Paper.id)).where(Paper.user_id == target.id)) or 0
    mistakes = list(db.scalars(select(Mistake).where(Mistake.user_id == target.id)))
    active = [m for m in mistakes if m.status == "active"]

    subjects: dict[str, int] = {}
    for mistake in active:
        question = db.get(Question, mistake.question_id)
        if question is None:
            continue
        subjects[question.subject] = subjects.get(question.subject, 0) + 1

    plans = list(db.scalars(select(ReviewPlan).where(ReviewPlan.user_id == target.id)))
    done = sum(1 for p in plans if p.status == "done")

    return {
        "user": UserOut.model_validate(target).model_dump(),
        "paper_count": int(paper_count),
        "mistake_count": len(active),
        "archived_count": len(mistakes) - len(active),
        "subject_distribution": subjects,
        "review_plan_count": len(plans),
        "review_done_count": done,
        "review_completion_rate": round(done / len(plans), 4) if plans else 0.0,
    }


@router.get("/users/{user_id}/papers", response_model=list[PaperOut])
def user_papers(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> list[PaperOut]:
    _require_admin(admin)
    target = _target_user(db, user_id)
    rows = db.scalars(
        select(Paper).where(Paper.user_id == target.id).order_by(Paper.created_at.desc()).limit(200)
    )
    return [PaperOut.model_validate(p) for p in rows]


@router.get("/users/{user_id}/mistakes", response_model=MistakePageOut)
def user_mistakes(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
    q: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> MistakePageOut:
    _require_admin(admin)
    target = _target_user(db, user_id)

    from .mistakes import _to_out

    stmt = (
        select(Mistake, Question)
        .join(Question, Question.id == Mistake.question_id)
        .where(Mistake.user_id == target.id, Mistake.status == "active")
    )
    if subject and subject.strip():
        stmt = stmt.where(Question.subject == subject.strip())
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(Question.text.like(needle), Question.label.like(needle)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(Mistake.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items: list[MistakeOut] = [_to_out(db, mistake) for mistake, _question in rows]
    return MistakePageOut(total=int(total), page=page, page_size=page_size, items=items)


@router.get("/users/{user_id}/weak-points", response_model=list[dict])
def user_weak_points(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> list[dict]:
    _require_admin(admin)
    target = _target_user(db, user_id)

    rows = db.execute(
        select(KnowledgePoint.name, func.count(Mistake.id))
        .select_from(Mistake)
        .join(Question, Question.id == Mistake.question_id)
        .join(QuestionKnowledgePoint, QuestionKnowledgePoint.question_id == Question.id)
        .join(KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id)
        .where(Mistake.user_id == target.id, Mistake.status == "active")
        .group_by(KnowledgePoint.name)
        .order_by(func.count(Mistake.id).desc())
        .limit(10)
    ).all()
    return [{"name": name, "mistake_count": int(count)} for name, count in rows]
