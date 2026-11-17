from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Paper, QuestionRegion, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    token: str | None = Query(default=None, alias="token"),
    db: Session = Depends(get_db),
) -> User:
    raw = credentials.credentials if credentials else token
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_access_token(raw)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id and str(user_id).isdigit() else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def get_owned_paper(paper_id: int, db: Session, user: User) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None or paper.user_id != user.id:
        # Do not leak existence of other users' resources.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="试卷不存在")
    return paper


def get_owned_region(region_id: int, db: Session, user: User) -> QuestionRegion:
    region = db.get(QuestionRegion, region_id)
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="题目区域不存在")
    paper = db.get(Paper, region.paper_id)
    if paper is None or paper.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="题目区域不存在")
    return region
