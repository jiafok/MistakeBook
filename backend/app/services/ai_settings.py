"""Instance-level runtime settings stored in the database.

Why the DB and not only env vars: an admin should be able to configure the AI
provider from the settings page without rebuilding the container. Environment
values act as defaults; stored settings take precedence.

Secrets are encrypted at rest with a key derived from SECRET_KEY, so a copied
SQLite file does not leak API keys.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SystemSetting

AI_KEYS = ("ai_provider", "ai_api_key", "ai_base_url", "ai_model", "ai_timeout_seconds")

AI_PROVIDERS: list[dict[str, str]] = [
    {"value": "none", "label": "不启用", "needs_key": "false", "needs_base": "false"},
    {"value": "openai", "label": "OpenAI", "needs_key": "true", "needs_base": "false"},
    {"value": "deepseek", "label": "DeepSeek", "needs_key": "true", "needs_base": "false"},
    {"value": "glm", "label": "GLM（智谱）", "needs_key": "true", "needs_base": "false"},
    {"value": "gemini", "label": "Gemini", "needs_key": "true", "needs_base": "false"},
    {"value": "claude", "label": "Claude", "needs_key": "true", "needs_base": "false"},
    {"value": "ollama", "label": "Ollama（本地）", "needs_key": "false", "needs_base": "true"},
]

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "glm": "glm-4",
    "gemini": "gemini-1.5-flash",
    "claude": "claude-3-5-sonnet-latest",
    "ollama": "llama3",
}


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Key changed or value written by an older install: treat as unusable.
        return ""


@dataclass
class AiRuntimeConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout: int

    @property
    def configured(self) -> bool:
        if self.provider in ("", "none"):
            return False
        if self.provider == "ollama":
            return bool(self.base_url)
        return bool(self.api_key)


def _get_row(db: Session, key: str) -> SystemSetting | None:
    return db.scalar(select(SystemSetting).where(SystemSetting.key == key))


def get_ai_config(db: Session) -> AiRuntimeConfig:
    """Stored settings win; environment variables are the fallback."""
    provider = settings.ai_provider or "none"
    api_key = settings.ai_api_key or ""
    base_url = settings.ai_base_url or ""
    model = settings.ai_model or ""
    timeout = settings.ai_timeout_seconds

    row = _get_row(db, "ai_provider")
    if row is not None and row.value:
        provider = row.value

    row = _get_row(db, "ai_api_key")
    if row is not None and row.value:
        api_key = decrypt_secret(row.value)

    row = _get_row(db, "ai_base_url")
    if row is not None:
        base_url = row.value

    row = _get_row(db, "ai_model")
    if row is not None:
        model = row.value

    row = _get_row(db, "ai_timeout_seconds")
    if row is not None and row.value.isdigit():
        timeout = int(row.value)

    return AiRuntimeConfig(
        provider=provider.strip().lower() or "none",
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=max(5, min(300, timeout)),
    )


def save_ai_config(
    db: Session,
    *,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    timeout: int | None,
) -> None:
    """api_key None means "leave unchanged"; empty string means "clear"."""
    pairs: dict[str, tuple[str, bool]] = {
        "ai_provider": (provider.strip().lower(), False),
    }
    if api_key is not None:
        pairs["ai_api_key"] = (encrypt_secret(api_key.strip()), True)
    if base_url is not None:
        pairs["ai_base_url"] = (base_url.strip(), False)
    if model is not None:
        pairs["ai_model"] = (model.strip(), False)
    if timeout is not None:
        pairs["ai_timeout_seconds"] = (str(max(5, min(300, timeout))), False)

    for key, (value, is_secret) in pairs.items():
        row = _get_row(db, key)
        if row is None:
            db.add(SystemSetting(key=key, value=value, is_secret=is_secret))
        else:
            row.value = value
            row.is_secret = is_secret
    db.commit()


def config_for_api(db: Session) -> dict[str, Any]:
    """Safe representation for the settings UI. Never returns the raw key."""
    cfg = get_ai_config(db)
    stored = _get_row(db, "ai_api_key")
    key_source = "db" if stored is not None and stored.value else ("env" if settings.ai_api_key else "none")
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "timeout_seconds": cfg.timeout,
        "has_api_key": bool(cfg.api_key),
        "api_key_source": key_source,
        "api_key_masked": ("•" * 8 + cfg.api_key[-4:]) if cfg.api_key else "",
        "configured": cfg.configured,
        "providers": AI_PROVIDERS,
        "default_models": DEFAULT_MODELS,
        "env_fallback": {
            "provider": settings.ai_provider,
            "has_api_key": bool(settings.ai_api_key),
            "base_url": bool(settings.ai_base_url),
            "model": settings.ai_model,
        },
    }
