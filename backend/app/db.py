from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _prepare_sqlite_path(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if raw and raw != ":memory:":
            from pathlib import Path

            Path(raw).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.database_url)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    # Naive UTC keeps Python/SQLite/PostgreSQL comparisons consistent.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)

    # create_all only adds missing tables; apply column-level migrations after.
    from .migrations import run_migrations

    run_migrations(engine)
