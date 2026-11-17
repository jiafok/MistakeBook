from __future__ import annotations

from fastapi import APIRouter, Depends

from fastapi import Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..schemas import StatusOut
from ..services.ai import get_ai_provider
from ..services.ai_settings import get_ai_config
from ..services.ocr import get_region_engine

router = APIRouter(tags=["system"])

VERSION = "1.1.0"


@router.get("/status", response_model=StatusOut)
def status(db: Session = Depends(get_db)) -> StatusOut:
    engine = get_region_engine()
    provider = get_ai_provider(get_ai_config(db))
    return StatusOut(
        app=settings.app_name,
        version=VERSION,
        storage_backend=settings.storage_backend,
        ai_provider=provider.name,
        ai_available=provider.available(),
        ocr=engine.status,
        limits={
            "max_upload_files": settings.max_upload_files,
            "max_file_mb": round(settings.max_file_bytes / (1024 * 1024), 1),
            "max_pdf_pages": settings.max_pdf_pages,
            "allowed_extensions": sorted(settings.allowed_extension_set),
        },
        thresholds={
            "recommend": settings.probability_recommend,
            "suspect": settings.probability_suspect,
        },
        review_intervals=settings.review_interval_list,
    )


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
