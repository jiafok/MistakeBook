"""Ebbinghaus review scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Mistake, ReviewPlan, ReviewRecord, utcnow


def create_plan(db: Session, mistake: Mistake) -> ReviewPlan:
    intervals = settings.review_interval_list
    plan = ReviewPlan(
        user_id=mistake.user_id,
        mistake_id=mistake.id,
        stage=0,
        due_at=utcnow() + timedelta(days=intervals[0]),
        status="pending",
    )
    db.add(plan)
    return plan


def advance_plan(db: Session, plan: ReviewPlan, result: str) -> ReviewPlan:
    """result: pass | fail | skip"""
    intervals = settings.review_interval_list

    db.add(
        ReviewRecord(
            plan_id=plan.id,
            user_id=plan.user_id,
            result=result,
            reviewed_at=utcnow(),
        )
    )

    if result == "fail":
        # reset to the first interval, but keep history
        plan.stage = 0
        plan.due_at = utcnow() + timedelta(days=intervals[0])
        plan.status = "pending"
        plan.last_result = result
        return plan

    if result == "skip":
        plan.due_at = utcnow() + timedelta(days=1)
        plan.status = "pending"
        plan.last_result = result
        return plan

    next_stage = plan.stage + 1
    if next_stage >= len(intervals):
        plan.stage = len(intervals) - 1
        plan.status = "done"
        plan.last_result = result
        plan.due_at = utcnow()
        return plan

    plan.stage = next_stage
    plan.due_at = utcnow() + timedelta(days=intervals[next_stage])
    plan.status = "pending"
    plan.last_result = result
    return plan


def due_plans(db: Session, user_id: int, horizon_days: int, only_due: bool = True) -> list[ReviewPlan]:
    now = utcnow()
    upper = now + timedelta(days=horizon_days)
    stmt = select(ReviewPlan).where(
        ReviewPlan.user_id == user_id,
        ReviewPlan.status == "pending",
        ReviewPlan.due_at <= upper,
    )
    if only_due:
        stmt = stmt.where(ReviewPlan.due_at <= now)
    return list(db.scalars(stmt.order_by(ReviewPlan.due_at.asc())))


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
