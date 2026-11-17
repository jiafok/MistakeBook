from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import Mistake, Question, ReviewPlan, ReviewRecord, User
from ..schemas import ReviewItemOut, ReviewResultRequest
from ..services.review import advance_plan, due_plans

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=list[ReviewItemOut])
def list_reviews(
    horizon: str = Query(default="today", pattern="^(today|week|month)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReviewItemOut]:
    days = {"today": 0, "week": 7, "month": 30}[horizon]
    plans = due_plans(db, user.id, horizon_days=days, only_due=(horizon == "today"))

    out: list[ReviewItemOut] = []
    for plan in plans:
        mistake = db.get(Mistake, plan.mistake_id)
        if mistake is None:
            continue
        question = db.get(Question, mistake.question_id)
        out.append(
            ReviewItemOut(
                plan_id=plan.id,
                mistake_id=mistake.id,
                label=question.label if question else "",
                text=question.text if question else "",
                subject=question.subject if question else "未分类",
                stage=plan.stage,
                due_at=plan.due_at,
                knowledge_points=[kp.name for kp in (question.knowledge_points if question else [])],
            )
        )
    return out


@router.post("/{plan_id}/result", response_model=dict)
def submit_review(
    plan_id: int,
    payload: ReviewResultRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    plan = db.get(ReviewPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="复习计划不存在")
    advance_plan(db, plan, payload.result)
    db.commit()
    return {"ok": True, "stage": plan.stage, "status": plan.status, "due_at": plan.due_at}


@router.get("/history", response_model=list[dict])
def review_history(
    db: Session = Depends(get_db), user: User = Depends(get_current_user), limit: int = 50
) -> list[dict]:
    rows = db.execute(
        select(ReviewRecord, ReviewPlan)
        .join(ReviewPlan, ReviewPlan.id == ReviewRecord.plan_id)
        .where(ReviewRecord.user_id == user.id)
        .order_by(ReviewRecord.reviewed_at.desc())
        .limit(max(1, min(200, limit)))
    ).all()
    return [
        {
            "plan_id": plan.id,
            "mistake_id": plan.mistake_id,
            "result": record.result,
            "reviewed_at": record.reviewed_at.isoformat(),
        }
        for record, plan in rows
    ]
