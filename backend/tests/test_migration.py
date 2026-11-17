"""Verify the users-table migration against a simulated legacy database."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
DB = TMP / "legacy.db"

# Build a legacy schema identical to the pre-username version.
conn = sqlite3.connect(DB)
conn.executescript(
    """
    CREATE TABLE users (
        id INTEGER NOT NULL,
        email VARCHAR(255) NOT NULL,
        display_name VARCHAR(120) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(32) NOT NULL,
        is_active BOOLEAN NOT NULL,
        settings_json TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    );
    """
)
rows = [
    (1, "parent@example.com", "家长", "hash1", "admin", 1, "{}", "2026-01-01", "2026-01-01"),
    (2, "jeffrey@example.com", "杰夫", "hash2", "member", 1, "{}", "2026-01-02", "2026-01-02"),
    (3, "parent@school.edu.cn", "另一位", "hash3", "member", 1, "{}", "2026-01-03", "2026-01-03"),
    (4, "a@b.com", "甲", "hash4", "member", 0, "{}", "2026-01-04", "2026-01-04"),
]
conn.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)", rows)
conn.commit()
conn.close()

os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["STORAGE_LOCAL_DIR"] = str(TMP / "storage")
os.environ["SECRET_KEY"] = "migration-test"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


print("== run migration on legacy db ==")
init_db()

conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
print("columns after:", cols)
check("username" in cols, "username column added")

# email must now be nullable: inspect the rebuilt DDL
ddl = conn.execute("select sql from sqlite_master where name='users'").fetchone()[0]
check("email VARCHAR(255)," in ddl.replace("\n", " ").replace("  ", " ") or "email VARCHAR(255), " in ddl, "email is nullable in DDL")

data = list(conn.execute("select id, username, email, display_name, role, is_active from users order by id"))
for row in data:
    print("   ", row)

check(len(data) == 4, "all 4 legacy users preserved")
check(all(r[1] for r in data), "every user has a username")
check(len({r[1] for r in data}) == 4, "usernames are unique")
check({r[0] for r in data} == {1, 2, 3, 4}, "ids preserved")

by_id = {r[0]: r for r in data}
check(by_id[1][1] == "parent", "user 1 username derived from email local part")
check(by_id[2][1] == "jeffrey", "user 2 username derived correctly")
# both emails start with 'parent' -> second must be de-duplicated
check(by_id[3][1].startswith("parent") and by_id[3][1] != "parent", "clashing username de-duplicated")
check(by_id[3][2] == "parent@school.edu.cn", "original email preserved")
check(by_id[1][4] == "admin", "role preserved for admin")
check(by_id[4][5] == 0, "is_active preserved (disabled user stays disabled)")

print("== idempotency: run again ==")
conn.close()
init_db()
conn = sqlite3.connect(DB)
data2 = list(conn.execute("select id, username from users order by id"))
check(len(data2) == 4, "second run does not duplicate rows")
check([r[1] for r in data2] == [r[1] for r in data], "second run leaves usernames unchanged")

print("== nullable email accepted now ==")
ph = "x"
conn.execute(
    "insert into users (username, email, display_name, password_hash, role, is_active, "
    "settings_json, created_at, updated_at) values (?,?,?,?,?,?,?,?,?)",
    ("newuser", None, "新用户", ph, "member", 1, "{}", "2026-02-01", "2026-02-01"),
)
conn.commit()
check(True, "insert with NULL email succeeded")

print("== unique username enforced ==")
try:
    conn.execute(
        "insert into users (username, email, display_name, password_hash, role, is_active, "
        "settings_json, created_at, updated_at) values (?,?,?,?,?,?,?,?,?)",
        ("newuser", "other@x.com", "重复", ph, "member", 1, "{}", "2026-02-02", "2026-02-02"),
    )
    conn.commit()
    check(False, "duplicate username was rejected")
except sqlite3.IntegrityError:
    check(True, "duplicate username rejected by DB constraint")

conn.close()

print()
if failures:
    print("FAILURES:")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)
print("ALL MIGRATION CHECKS PASSED")
