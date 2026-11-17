"""Lightweight, idempotent migrations for existing SQLite deployments.

`Base.metadata.create_all()` only creates missing tables; it never alters existing
ones. This module applies the column changes older installs need, using a
table-rebuild strategy because SQLite cannot drop NOT NULL or add UNIQUE in place.

Every step is guarded so running it repeatedly is safe.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("mistakebook.migrations")

USERNAME_RE = re.compile(r"[^a-z0-9_.-]+")


def _columns(conn, table: str) -> list[str]:  # noqa: ANN001
    return [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]


def _table_exists(conn, table: str) -> bool:  # noqa: ANN001
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": table}
    ).first()
    return row is not None


def _derive_username(email: str, taken: set[str]) -> str:
    base = (email.split("@")[0] if email and "@" in email else email) or "user"
    base = USERNAME_RE.sub("", base.strip().lower()) or "user"
    base = base[:48]
    candidate = base
    index = 1
    while candidate in taken:
        index += 1
        suffix = f"_{index}"
        candidate = f"{base[: 48 - len(suffix)]}{suffix}"
    taken.add(candidate)
    return candidate


def migrate_users(engine: Engine) -> None:
    """Ensure users has username (unique, NOT NULL) and a nullable email."""
    if engine.dialect.name != "sqlite":
        logger.info("skipping users migration (dialect=%s)", engine.dialect.name)
        return

    with engine.begin() as conn:
        if not _table_exists(conn, "users"):
            return

        cols = _columns(conn, "users")
        # Read existing rows first: we need them to rebuild the table.
        rows = list(
            conn.execute(
                text(
                    "SELECT id, email, display_name, password_hash, role, is_active, "
                    "settings_json, created_at, updated_at FROM users ORDER BY id"
                )
            )
        )

        if "username" in cols:
            # Column exists; only backfill blanks (e.g. partial earlier migration).
            existing = {
                row[0]
                for row in conn.execute(text("SELECT username FROM users WHERE username IS NOT NULL"))
            }
            for record in rows:
                user_id, email = record[0], record[1]
                current = conn.execute(
                    text("SELECT username FROM users WHERE id=:i"), {"i": user_id}
                ).scalar()
                if current:
                    continue
                username = _derive_username(email or f"user{user_id}", existing)
                conn.execute(
                    text("UPDATE users SET username=:u WHERE id=:i"),
                    {"u": username, "i": user_id},
                )
                logger.info("backfilled username %s for user %s", username, user_id)
            return

        logger.info("rebuilding users table to add username / relax email")

        conn.execute(text("""
            CREATE TABLE users_new (
                id INTEGER NOT NULL,
                username VARCHAR(64) NOT NULL,
                email VARCHAR(255),
                display_name VARCHAR(120) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(32) NOT NULL,
                is_active BOOLEAN NOT NULL,
                settings_json TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (username),
                UNIQUE (email)
            )
        """))

        taken: set[str] = set()
        for record in rows:
            (
                user_id,
                email,
                display_name,
                password_hash,
                role,
                is_active,
                settings_json,
                created_at,
                updated_at,
            ) = record
            username = _derive_username(email or f"user{user_id}", taken)
            conn.execute(
                text(
                    "INSERT INTO users_new (id, username, email, display_name, password_hash, "
                    "role, is_active, settings_json, created_at, updated_at) "
                    "VALUES (:id, :username, :email, :display_name, :password_hash, :role, "
                    ":is_active, :settings_json, :created_at, :updated_at)"
                ),
                {
                    "id": user_id,
                    "username": username,
                    "email": email or None,
                    "display_name": display_name,
                    "password_hash": password_hash,
                    "role": role,
                    "is_active": is_active,
                    "settings_json": settings_json,
                    "created_at": created_at,
                    "updated_at": updated_at,
                },
            )
            logger.info("migrated user %s -> username=%s", user_id, username)

        conn.execute(text("DROP TABLE users"))
        conn.execute(text("ALTER TABLE users_new RENAME TO users"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"))
        logger.info("users table migration complete (%d rows)", len(rows))


def run_migrations(engine: Engine) -> None:
    try:
        migrate_users(engine)
    except Exception as exc:  # noqa: BLE001 - never block startup on migration issues
        logger.exception("migration failed: %s", exc)
