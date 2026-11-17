"""Username login + admin oversight tests (read-only isolation)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/acc.db"
os.environ["STORAGE_LOCAL_DIR"] = f"{TMP}/storage"
os.environ["SECRET_KEY"] = "account-test"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.main import app  # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def make_image() -> bytes:
    img = Image.new("RGB", (900, 1200), "white")
    d = ImageDraw.Draw(img)
    for top in (100, 420, 740):
        d.rectangle([60, top, 820, top + 8], fill="black")
        d.rectangle([60, top + 40, 740, top + 48], fill="black")
        d.rectangle([60, top + 120, 600, top + 128], fill=(60, 60, 60))
    d.line([830, 460, 890, 520], fill=(220, 30, 30), width=10)
    d.line([890, 460, 830, 520], fill=(220, 30, 30), width=10)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    with TestClient(app) as client:
        print("== register with username only ==")
        r = client.post(
            "/api/v1/auth/register",
            json={"username": "mama", "password": "secret123", "display_name": "妈妈"},
        )
        check(r.status_code == 201, "username-only registration works")
        first = r.json()
        check(first["user"]["username"] == "mama", "username returned")
        check(first["user"]["email"] is None, "email optional and null")
        check(first["user"]["role"] == "admin", "first user is admin")
        admin_h = {"Authorization": f"Bearer {first['access_token']}"}

        print("== duplicate / invalid usernames ==")
        r = client.post("/api/v1/auth/register", json={"username": "mama", "password": "secret123"})
        check(r.status_code == 409, "duplicate username rejected")
        r = client.post("/api/v1/auth/register", json={"username": "ab", "password": "secret123"})
        check(r.status_code == 422, "too-short username rejected")
        r = client.post("/api/v1/auth/register", json={"username": "bad name!", "password": "secret123"})
        check(r.status_code == 422, "invalid characters rejected")
        r = client.post("/api/v1/auth/register", json={"username": "admin", "password": "secret123"})
        check(r.status_code == 400, "reserved username rejected")
        r = client.post("/api/v1/auth/register", json={"password": "secret123"})
        check(r.status_code == 422, "missing identifier rejected")

        print("== register with email derives username ==")
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "student@example.com", "password": "secret123"},
        )
        check(r.status_code == 201, "email-only registration works")
        student = r.json()["user"]
        check(student["username"] == "student", "username derived from email")
        student_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        print("== login by username or email ==")
        r = client.post("/api/v1/auth/login", json={"identifier": "mama", "password": "secret123"})
        check(r.status_code == 200, "login by username")
        r = client.post(
            "/api/v1/auth/login", json={"identifier": "student@example.com", "password": "secret123"}
        )
        check(r.status_code == 200, "login by email")
        r = client.post("/api/v1/auth/login", json={"username": "mama", "password": "secret123"})
        check(r.status_code == 200, "legacy 'username' field accepted")
        r = client.post("/api/v1/auth/login", json={"identifier": "mama", "password": "wrong"})
        check(r.status_code == 401, "wrong password rejected")
        r = client.post("/api/v1/auth/login", json={"identifier": "ghost", "password": "secret123"})
        check(r.status_code == 401, "unknown user rejected")
        body = r.json()["detail"]
        check("用户名或密码不正确" in body, "generic message does not reveal existence")

        print("== data isolation between members ==")
        img = make_image()
        r = client.post(
            "/api/v1/papers/upload",
            headers=student_h,
            files={"files": ("p.png", img, "image/png")},
            data={"title": "学生试卷", "subject": "数学"},
        )
        check(r.status_code == 201, "member can upload")
        paper_id = r.json()["id"]

        ws = client.get(f"/api/v1/papers/{paper_id}/workspace", headers=student_h).json()
        ids = [reg["id"] for reg in ws["regions"] if not reg["is_recorded"]][:2]
        r = client.post(
            f"/api/v1/papers/{paper_id}/commit", headers=student_h, json={"region_ids": ids}
        )
        check(r.json()["created"] == len(ids), "member recorded mistakes")

        r = client.get(f"/api/v1/papers/{paper_id}/workspace", headers=admin_h)
        check(r.status_code == 404, "admin cannot read member paper via normal API")
        r = client.get("/api/v1/mistakes", headers=admin_h)
        check(r.json()["total"] == 0, "admin's own mistake list stays empty")

        print("== admin oversight (read-only) ==")
        r = client.get("/api/v1/admin/users", headers=admin_h)
        check(r.status_code == 200, "admin lists users")
        users = r.json()
        check(len(users) == 2, "both accounts listed")
        student_row = next(u for u in users if u["username"] == "student")
        check(student_row["mistake_count"] == len(ids), "oversight shows member mistake count")
        check(student_row["paper_count"] == 1, "oversight shows member paper count")
        check(any(u["is_self"] for u in users), "self flagged")

        r = client.get(f"/api/v1/admin/users/{student['id']}/summary", headers=admin_h)
        check(r.status_code == 200, "admin reads member summary")
        summary = r.json()
        check(summary["mistake_count"] == len(ids), "summary mistake count correct")
        check("数学" in summary["subject_distribution"], "summary has subject distribution")

        r = client.get(f"/api/v1/admin/users/{student['id']}/mistakes", headers=admin_h)
        check(r.status_code == 200, "admin reads member mistakes")
        check(r.json()["total"] == len(ids), "member mistakes visible to admin")
        check(r.json()["items"][0]["text"] is not None, "mistake payload shaped like normal API")

        r = client.get(f"/api/v1/admin/users/{student['id']}/papers", headers=admin_h)
        check(r.status_code == 200 and len(r.json()) == 1, "admin reads member papers")

        print("== oversight is read-only ==")
        admin_mistake_id = r.json()[0]["id"] if r.json() else None
        # admin must not be able to mutate member data through normal endpoints
        member_mistake = client.get(
            f"/api/v1/admin/users/{student['id']}/mistakes", headers=admin_h
        ).json()["items"][0]
        r = client.patch(
            f"/api/v1/mistakes/{member_mistake['id']}",
            headers=admin_h,
            json={"subject": "篡改"},
        )
        check(r.status_code == 404, "admin cannot edit member mistake via normal API")
        r = client.delete(f"/api/v1/mistakes/{member_mistake['id']}", headers=admin_h)
        check(r.status_code == 404, "admin cannot delete member mistake")
        r = client.post(
            f"/api/v1/mistakes/{member_mistake['id']}/review-plan", headers=admin_h
        )
        check(r.status_code == 404, "admin cannot create review plan on member data")

        # and the member's data is unchanged
        r = client.get(f"/api/v1/mistakes/{member_mistake['id']}", headers=student_h)
        check(r.status_code == 200, "member data still intact after admin attempts")
        check(r.json()["subject"] == "数学", "subject unchanged")

        print("== member cannot use admin endpoints ==")
        r = client.get("/api/v1/admin/users", headers=student_h)
        check(r.status_code == 403, "member blocked from user list")
        r = client.get(f"/api/v1/admin/users/{student['id']}/summary", headers=student_h)
        check(r.status_code == 403, "member blocked from summary")
        r = client.get("/api/v1/admin/users")
        check(r.status_code == 401, "anonymous blocked")

        print("== admin looking up unknown user ==")
        r = client.get("/api/v1/admin/users/99999/summary", headers=admin_h)
        check(r.status_code == 404, "unknown user returns 404")

        print("== update own profile ==")
        r = client.patch(
            "/api/v1/auth/me", headers=student_h, json={"display_name": "小明"}
        )
        check(r.status_code == 200 and r.json()["display_name"] == "小明", "display name updated")
        r = client.patch(
            "/api/v1/auth/me",
            headers=student_h,
            json={"current_password": "bad", "new_password": "newpass123"},
        )
        check(r.status_code == 400, "password change requires correct current password")
        r = client.patch(
            "/api/v1/auth/me",
            headers=student_h,
            json={"current_password": "secret123", "new_password": "newpass123"},
        )
        check(r.status_code == 200, "password change succeeds with correct current password")
        r = client.post(
            "/api/v1/auth/login", json={"identifier": "student", "password": "newpass123"}
        )
        check(r.status_code == 200, "can log in with the new password")

        print("== PWA endpoints ==")
        r = client.get("/manifest.webmanifest")
        check(r.status_code == 200, "manifest served")
        m = r.json()
        check(m["display"] == "standalone", "manifest is standalone")
        check(len(m["icons"]) >= 2, "manifest declares icons")
        check(any(s["url"].startswith("/upload") for s in m["shortcuts"]), "upload shortcut present")
        r = client.get("/sw.js")
        check(r.status_code == 200, "service worker served")
        check("no-store" in r.headers.get("cache-control", "") or "no-cache" in r.headers.get("cache-control", ""), "sw not long-cached")
        check("/api/" in r.text, "sw excludes api from caching")

    print()
    if failures:
        print("FAILURES:")
        for item in failures:
            print(" -", item)
        return 1
    print("ALL ACCOUNT / OVERSIGHT / PWA CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
