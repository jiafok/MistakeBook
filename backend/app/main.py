from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .api import (
    admin,
    ai,
    analytics,
    auth,
    export,
    mistakes,
    papers,
    pwa,
    reviews,
    settings_api,
    system,
)
from .config import settings
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("mistakebook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info("MistakeBook AI started | db=%s storage=%s", settings.database_url, settings.storage_dir)
    _repair_legacy_mistakes()
    yield


def _repair_legacy_mistakes() -> None:
    """Upgrade records that still print as a screenshot of the question.

    An older build stored a mistake by cropping the whole question out of the paper,
    which cannot be re-typeset or searched when the book is printed. The drawings can
    be re-located from the stored page image, so the conversion is done here once
    instead of asking the user to re-submit every old mistake by hand. Any failure
    is logged and ignored: a broken repair must never block startup.
    """
    from .db import SessionLocal
    from .services.figurerepair import (
        backfill_missing_figures,
        figures_need_repair,
        repair_legacy_records,
    )
    from .services.ocr import get_region_engine
    from .storage import get_storage

    try:
        with SessionLocal() as db:
            if figures_need_repair(db):
                repair_legacy_records(db, storage=get_storage(), engine=get_region_engine())
            # Records made by a build that extracted the text but not the drawings
            # have nothing to convert, yet still print without their diagram.
            backfill_missing_figures(db, storage=get_storage(), engine=get_region_engine())
    except Exception as exc:  # noqa: BLE001 - startup must never fail on this
        logger.warning("figure repair skipped: %s", exc)


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    description="AI 错题本系统：OCR、切题、错题推荐与人工确认。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    auth,
    papers,
    mistakes,
    reviews,
    analytics,
    ai,
    export,
    settings_api,
    admin,
    system,
):
    app.include_router(module.router, prefix=settings.api_prefix)

# PWA resources must be served from the origin root, not under /api/v1:
# browsers look for /manifest.webmanifest and the service worker scope is "/".
app.include_router(pwa.router)


@app.exception_handler(Exception)
async def unhandled_exception(_request, exc: Exception) -> JSONResponse:  # noqa: ANN001
    logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# Optional: serve a built frontend if present (single-container deployment).
# Layout differs between local dev and Docker, so probe several candidates.
def _resolve_frontend_dist() -> Path | None:
    import os

    override = os.environ.get("FRONTEND_DIST_DIR", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve()
    # local dev: <repo>/backend/app/main.py  -> <repo>/frontend/dist
    candidates.append(here.parents[2] / "frontend" / "dist")
    # docker:    /app/app/main.py           -> /app/frontend/dist
    candidates.append(here.parents[1] / "frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


_frontend_dist = _resolve_frontend_dist()


class SpaStaticFiles(StaticFiles):
    """Static file server with SPA history fallback.

    Deep links such as /mistakes/12 are client-side routes; serving 404 would
    break refresh. Fall back to index.html for non-asset paths that are not found.
    """

    async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if path.startswith("api/") or path.startswith("docs"):
                raise
            if "." in Path(path).name and not path.endswith(".html"):
                raise
            return await super().get_response("index.html", scope)


if _frontend_dist is not None:
    logger.info("serving frontend from %s", _frontend_dist)
    app.mount(
        "/", SpaStaticFiles(directory=str(_frontend_dist), html=True), name="frontend"
    )
else:
    logger.info("no built frontend found; API-only mode")
