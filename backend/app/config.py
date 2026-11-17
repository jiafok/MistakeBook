from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MistakeBook AI"
    api_prefix: str = "/api/v1"
    debug: bool = False

    # SQLite by default; set DATABASE_URL for PostgreSQL
    database_url: str = "sqlite:///./data/mistakebook.db"

    # Auth
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7
    allow_registration: bool = True

    # Storage: local | nas | minio
    storage_backend: str = "local"
    storage_local_dir: str = "./data/storage"
    # NAS: point storage_local_dir at a mounted share instead.
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "mistakebook"
    minio_secure: bool = False

    # Upload limits
    max_upload_files: int = 500
    max_file_bytes: int = 30 * 1024 * 1024
    max_pdf_pages: int = 100
    allowed_extensions: str = "jpg,jpeg,png,webp,pdf"

    # OCR: rapidocr (bundled CPU models) | heuristic | paddle | tesseract
    ocr_provider: str = "rapidocr"

    # AI: none | openai | deepseek | glm | gemini | claude | ollama
    ai_provider: str = "none"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 60

    # Recommendation thresholds
    probability_recommend: int = 85
    probability_suspect: int = 50

    # Dedup
    dedup_similarity_threshold: float = 0.90

    # Ebbinghaus default intervals (days)
    review_intervals: str = "1,3,7,15,30"

    @property
    def allowed_extension_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()}

    @property
    def review_interval_list(self) -> list[int]:
        out: list[int] = []
        for part in self.review_intervals.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value > 0:
                out.append(value)
        return out or [1, 3, 7, 15, 30]

    @property
    def storage_dir(self) -> Path:
        return Path(self.storage_local_dir).expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
