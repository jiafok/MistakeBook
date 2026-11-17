from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import (
    AiAnalysis,
    KnowledgePoint,
    Mistake,
    Paper,
    PracticeQuestion,
    Question,
    QuestionRegion,
    User,
)
from ..schemas import AnalysisOut, AnalysisRequest, PracticeOut, PracticeRequest, PracticeItem
from ..services.ai import get_ai_provider
from ..services.ai_settings import get_ai_config

router = APIRouter(tags=["ai"])


def _resolve_question(
    db: Session, user: User, region_id: int | None, question_id: int | None
) -> Question:
    if question_id:
        question = db.get(Question, question_id)
        if question is not None:
            paper = db.get(Paper, question.paper_id)
            if paper is not None and paper.user_id == user.id:
                return question
    if region_id:
        region = db.get(QuestionRegion, region_id)
        if region is not None:
            paper = db.get(Paper, region.paper_id)
            if paper is not None and paper.user_id == user.id and region.question_id:
                question = db.get(Question, region.question_id)
                if question is not None:
                    return question
    raise HTTPException(status_code=404, detail="题目不存在")


@router.post("/ai/analyze", response_model=AnalysisOut)
def analyze(
    payload: AnalysisRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AnalysisOut:
    question = _resolve_question(db, user, payload.region_id, payload.question_id)
    provider = get_ai_provider(get_ai_config(db))
    result = provider.analyze(question.text or question.label, question.subject)

    data = result.data
    if payload.apply:
        existing = db.scalar(
            select(AiAnalysis)
            .where(AiAnalysis.question_id == question.id)
            .order_by(AiAnalysis.created_at.desc())
        )
        target = existing or AiAnalysis(user_id=user.id, question_id=question.id)
        target.provider = result.provider
        target.model = result.model
        target.subject = str(data.get("subject", ""))[:64]
        target.knowledge_points_json = json.dumps(data.get("knowledge_points", []), ensure_ascii=False)
        target.difficulty = int(data.get("difficulty", 3))
        target.error_reason = str(data.get("error_reason", ""))[:64]
        target.error_detail = str(data.get("error_detail", ""))[:2000]
        target.correct_solution = str(data.get("correct_solution", ""))[:4000]
        target.solution_steps_json = json.dumps(
            data.get("solution_steps", []), ensure_ascii=False
        )
        target.raw_response = json.dumps(data, ensure_ascii=False)[:20000]
        target.is_fallback = result.is_fallback
        if existing is None:
            db.add(target)

        if data.get("subject"):
            question.subject = str(data["subject"])[:64]
        question.difficulty = int(data.get("difficulty", question.difficulty))

        for name in data.get("knowledge_points", [])[:8]:
            name = str(name).strip()[:128]
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
            if kp not in question.knowledge_points:
                question.knowledge_points.append(kp)

        db.commit()

    return AnalysisOut(
        provider=result.provider,
        model=result.model,
        is_fallback=result.is_fallback,
        error=result.error,
        subject=str(data.get("subject", "")),
        knowledge_points=[str(x) for x in data.get("knowledge_points", [])],
        difficulty=int(data.get("difficulty", 3)),
        error_reason=str(data.get("error_reason", "")),
        error_detail=str(data.get("error_detail", "")),
        correct_solution=str(data.get("correct_solution", "")),
        solution_steps=[str(x) for x in data.get("solution_steps", [])],
    )


@router.post("/ai/practice", response_model=PracticeOut)
def generate_practice(
    payload: PracticeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PracticeOut:
    question: Question | None = None
    if payload.mistake_id:
        mistake = db.get(Mistake, payload.mistake_id)
        if mistake is None or mistake.user_id != user.id:
            raise HTTPException(status_code=404, detail="错题不存在")
        question = db.get(Question, mistake.question_id)
    elif payload.question_id:
        question = _resolve_question(db, user, None, payload.question_id)

    if question is None:
        raise HTTPException(status_code=404, detail="未找到用于出题的题目")

    provider = get_ai_provider(get_ai_config(db))
    result = provider.generate_practice(question.text or question.label, payload.count, question.subject)

    items = [PracticeItem(**item) for item in result.data.get("questions", [])]

    if payload.save and result.ok:
        for item in items:
            created = Question(
                paper_id=question.paper_id,
                section_id=question.section_id,
                label="练习题",
                text=item.text,
                subject=question.subject,
                difficulty=question.difficulty,
                is_practice=True,
            )
            db.add(created)
            db.flush()
            db.add(
                PracticeQuestion(
                    user_id=user.id,
                    question_id=created.id,
                    source_question_id=question.id,
                    text=item.text,
                    answer=item.answer,
                    explanation=item.explanation,
                    knowledge_point=item.knowledge_point,
                )
            )
        db.commit()

    return PracticeOut(
        provider=result.provider,
        model=result.model,
        is_fallback=result.is_fallback,
        error=result.error,
        questions=items,
    )


@router.get("/ai/practice", response_model=list[PracticeItem])
def list_practice(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PracticeItem]:
    rows = db.scalars(
        select(PracticeQuestion)
        .where(PracticeQuestion.user_id == user.id)
        .order_by(PracticeQuestion.created_at.desc())
        .limit(100)
    )
    return [
        PracticeItem(
            text=r.text, answer=r.answer, explanation=r.explanation, knowledge_point=r.knowledge_point
        )
        for r in rows
    ]
