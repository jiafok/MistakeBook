from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..deps import get_current_user
from ..models import (
    AiAnalysis,
    KnowledgePoint,
    Mistake,
    Question,
    QuestionKnowledgePoint,
    ReviewPlan,
    ReviewRecord,
    Tag,
    QuestionTag,
    User,
)
from ..schemas import AnalyticsOut, KnowledgePointOut, TagOut

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsOut)
def analytics(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> AnalyticsOut:
    mistakes = list(db.scalars(select(Mistake).where(Mistake.user_id == user.id)))
    total = len(mistakes)
    active_ids = [m.id for m in mistakes if m.status == "active"]

    cutoff = utcnow() - timedelta(days=7)
    new_recent = sum(1 for m in mistakes if m.created_at and m.created_at >= cutoff)

    by_subject: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    kp_counter: Counter[str] = Counter()
    repeat: list[dict[str, object]] = []

    for mistake in mistakes:
        question = db.get(Question, mistake.question_id)
        if question is None:
            continue
        by_subject[question.subject or "未分类"] += 1
        for kp in question.knowledge_points:
            kp_counter[kp.name] += 1
        analysis = db.scalar(
            select(AiAnalysis)
            .where(AiAnalysis.question_id == question.id)
            .order_by(AiAnalysis.created_at.desc())
        )
        if analysis is not None and analysis.error_reason:
            reason_counter[analysis.error_reason] += 1
        if mistake.wrong_count >= 3:
            repeat.append(
                {
                    "mistake_id": mistake.id,
                    "label": question.label,
                    "subject": question.subject,
                    "wrong_count": mistake.wrong_count,
                }
            )

    repeat.sort(key=lambda x: int(x["wrong_count"]), reverse=True)

    plans = list(db.scalars(select(ReviewPlan).where(ReviewPlan.user_id == user.id)))
    total_reviews = sum(len(p.records) for p in plans) if plans else 0
    done_plans = sum(1 for p in plans if p.status == "done")
    completion = (done_plans / len(plans)) if plans else 0.0

    # Weak knowledge points: repeated failures, or high mistake volume in last 30 days
    weak: list[dict[str, object]] = []
    recent_cutoff = utcnow() - timedelta(days=30)
    fail_counter: Counter[str] = Counter()
    records = db.execute(
        select(ReviewRecord, ReviewPlan, Mistake)
        .join(ReviewPlan, ReviewPlan.id == ReviewRecord.plan_id)
        .join(Mistake, Mistake.id == ReviewPlan.mistake_id)
        .where(ReviewRecord.user_id == user.id, ReviewRecord.result == "fail")
    ).all()
    for _record, _plan, mistake in records:
        question = db.get(Question, mistake.question_id)
        if question is None:
            continue
        for kp in question.knowledge_points:
            fail_counter[kp.name] += 1

    recent_mistakes = [m for m in mistakes if m.created_at and m.created_at >= recent_cutoff]
    recent_kp: Counter[str] = Counter()
    for mistake in recent_mistakes:
        question = db.get(Question, mistake.question_id)
        if question is None:
            continue
        for kp in question.knowledge_points:
            recent_kp[kp.name] += 1

    for name, count in kp_counter.most_common(20):
        fails = fail_counter.get(name, 0)
        recent = recent_kp.get(name, 0)
        if fails >= 2 or (recent >= 3 and fails >= 1):
            weak.append(
                {
                    "name": name,
                    "mistake_count": count,
                    "review_failures": fails,
                    "recent_30d": recent,
                    "reason": "复习连续失败" if fails >= 2 else "近期反复出错",
                }
            )

    return AnalyticsOut(
        total_mistakes=total,
        new_last_7_days=new_recent,
        by_subject=dict(by_subject),
        top_knowledge_points=[
            {"name": name, "count": count} for name, count in kp_counter.most_common(10)
        ],
        top_wrong_count=repeat[:10],
        error_reasons=dict(reason_counter),
        review_completion_rate=round(completion, 4),
        repeat_wrong=repeat[:10],
        weak_knowledge_points=weak[:10],
    )


@router.get("/knowledge-points", response_model=list[KnowledgePointOut])
def list_knowledge_points(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[KnowledgePointOut]:
    rows = list(db.scalars(select(KnowledgePoint).where(KnowledgePoint.user_id == user.id)))

    q_counts: Counter[int] = Counter(
        int(x)
        for x in db.scalars(
            select(QuestionKnowledgePoint.knowledge_point_id).join(
                Question, Question.id == QuestionKnowledgePoint.question_id
            )
        )
    )
    mistake_kp: Counter[int] = Counter(
        int(x)
        for x in db.scalars(
            select(QuestionKnowledgePoint.knowledge_point_id)
            .join(Question, Question.id == QuestionKnowledgePoint.question_id)
            .join(Mistake, Mistake.question_id == Question.id)
            .where(Mistake.user_id == user.id)
        )
    )

    out = [
        KnowledgePointOut(
            id=kp.id,
            name=kp.name,
            subject=kp.subject,
            question_count=q_counts.get(kp.id, 0),
            mistake_count=mistake_kp.get(kp.id, 0),
        )
        for kp in rows
    ]
    out.sort(key=lambda x: (-x.mistake_count, -x.question_count, x.name))
    return out


@router.get("/tags", response_model=list[TagOut])
def list_tags(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[TagOut]:
    rows = list(db.scalars(select(Tag).where(Tag.user_id == user.id)))
    counts: Counter[int] = Counter(
        int(x) for x in db.scalars(select(QuestionTag.tag_id))
    )
    out = [
        TagOut(id=t.id, name=t.name, question_count=counts.get(t.id, 0)) for t in rows
    ]
    out.sort(key=lambda x: (-x.question_count, x.name))
    return out
