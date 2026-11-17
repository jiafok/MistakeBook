from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import LoginRequest, RegisterRequest, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

_RESERVED = {"admin", "root", "system", "mistakebook", "api", "www"}


def _derive_username(source: str) -> str:
    base = source.split("@")[0] if "@" in source else source
    cleaned = re.sub(r"[^a-z0-9_.-]+", "", base.strip().lower())
    if len(cleaned) < 3:
        cleaned = (cleaned + "user")[:48]
    return cleaned[:48]


def _unique_username(db: Session, base: str) -> str:
    candidate = base
    index = 1
    while db.scalar(select(User).where(User.username == candidate)) is not None:
        index += 1
        suffix = f"_{index}"
        candidate = f"{base[: 48 - len(suffix)]}{suffix}"
    return candidate


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenOut:
    if not settings.allow_registration:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="本实例已关闭注册")

    if payload.email and db.scalar(select(User).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册")

    username = payload.username
    if username:
        if username in _RESERVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该用户名为系统保留")
        if db.scalar(select(User).where(User.username == username)) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该用户名已被使用")
    else:
        # Derive from the email and de-duplicate, so registration never fails on a clash.
        username = _unique_username(db, _derive_username(payload.email or "user"))

    is_first = (db.scalar(select(func.count(User.id))) or 0) == 0
    user = User(
        username=username,
        email=payload.email,
        display_name=(payload.display_name or "").strip() or username,
        password_hash=hash_password(payload.password),
        role="admin" if is_first else "member",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
    identifier = payload.resolved.lower()
    user = db.scalar(
        select(User).where(or_(User.username == identifier, User.email == identifier))
    )
    # Identical response for unknown account and wrong password.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码不正确"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被停用")

    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserOut:
    """Update own display name / password. Username and role are not self-editable."""
    display_name = payload.get("display_name")
    if isinstance(display_name, str):
        user.display_name = display_name.strip()[:120] or user.username

    new_password = payload.get("new_password")
    if isinstance(new_password, str) and new_password:
        current = payload.get("current_password")
        if not isinstance(current, str) or not verify_password(current, user.password_hash):
            raise HTTPException(status_code=400, detail="当前密码不正确")
        if len(new_password) < 6:
            raise HTTPException(status_code=400, detail="新密码至少 6 位")
        user.password_hash = hash_password(new_password)

    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
