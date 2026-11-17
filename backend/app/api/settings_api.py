"""Settings API: instance-level AI configuration.

Only admins may change settings. Every authenticated user may read the sanitized
config so the UI can show what is active. The raw API key is never returned.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..services.ai import get_ai_provider
from ..services.ai_settings import (
    AI_PROVIDERS,
    DEFAULT_MODELS,
    config_for_api,
    get_ai_config,
    save_ai_config,
)

router = APIRouter(prefix="/settings", tags=["settings"])

VALID_PROVIDERS = {p["value"] for p in AI_PROVIDERS}


class AiConfigUpdate(BaseModel):
    provider: str = Field(max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)


class AiTestRequest(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: int | None = None


class AiTestResult(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int
    message: str


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可以修改实例设置"
        )


@router.get("/ai")
def read_ai_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    return config_for_api(db)


@router.put("/ai")
def update_ai_settings(
    payload: AiConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_admin(user)

    provider = payload.provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail="不支持的 AI 提供方")

    base_url = payload.base_url
    if provider == "ollama":
        if base_url is not None and not base_url.strip():
            raise HTTPException(status_code=400, detail="Ollama 需要填写服务地址")
    elif base_url is not None:
        base_url = base_url.strip()
        if base_url and not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="服务地址必须以 http:// 或 https:// 开头")

    # An empty api_key string clears the stored key; None leaves it untouched.
    if payload.api_key is not None and payload.api_key.strip().startswith("•"):
        # The UI sent back the masked placeholder; ignore it.
        key_arg: str | None = None
    else:
        key_arg = payload.api_key

    save_ai_config(
        db,
        provider=provider,
        api_key=key_arg,
        base_url=base_url,
        model=payload.model,
        timeout=payload.timeout_seconds,
    )
    return config_for_api(db)


@router.post("/ai/test", response_model=AiTestResult)
def test_ai_settings(
    payload: AiTestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiTestResult:
    """Probe the provider with a tiny request.

    Uses the values sent by the form when present, otherwise the stored config,
    so an admin can validate before saving.
    """
    _require_admin(user)

    cfg = get_ai_config(db)
    provider_name = (payload.provider or cfg.provider or "none").lower()
    if provider_name == "none":
        raise HTTPException(status_code=400, detail="请先选择 AI 提供方")

    if payload.api_key is not None and payload.api_key.strip().startswith("•"):
        api_key = cfg.api_key
    else:
        api_key = payload.api_key if payload.api_key is not None else cfg.api_key

    base_url = payload.base_url if payload.base_url is not None else cfg.base_url
    model = payload.model if payload.model is not None else cfg.model
    timeout = payload.timeout_seconds or cfg.timeout

    from ..services.ai_settings import AiRuntimeConfig

    probe_cfg = AiRuntimeConfig(
        provider=provider_name,
        api_key=api_key or "",
        base_url=base_url or "",
        model=model or DEFAULT_MODELS.get(provider_name, ""),
        timeout=max(5, min(300, timeout)),
    )

    provider = get_ai_provider(probe_cfg)
    if not provider.available():
        missing = "服务地址" if provider_name == "ollama" else "API Key"
        return AiTestResult(
            ok=False,
            provider=provider_name,
            model=probe_cfg.model,
            latency_ms=0,
            message=f"配置不完整：缺少{missing}",
        )

    import time

    started = time.perf_counter()
    try:
        reply = provider.chat(
            [
                {"role": "system", "content": "你是连通性测试助手，只回答一个词。"},
                {"role": "user", "content": "请只回复：OK"},
            ]
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        snippet = (reply or "").strip().replace("\n", " ")[:60]
        return AiTestResult(
            ok=True,
            provider=provider_name,
            model=probe_cfg.model,
            latency_ms=elapsed,
            message=f"连接成功，返回：{snippet or '(空响应)'}",
        )
    except httpx.HTTPStatusError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = ""
        try:
            detail = exc.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        return AiTestResult(
            ok=False,
            provider=provider_name,
            model=probe_cfg.model,
            latency_ms=elapsed,
            message=f"HTTP {exc.response.status_code}：{detail or exc}",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin as a message
        elapsed = int((time.perf_counter() - started) * 1000)
        return AiTestResult(
            ok=False,
            provider=provider_name,
            model=probe_cfg.model,
            latency_ms=elapsed,
            message=f"连接失败：{exc}",
        )
