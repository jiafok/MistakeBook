"""End-to-end smoke test: register -> upload -> workspace -> commit -> list."""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")
os.environ.setdefault("STORAGE_LOCAL_DIR", tempfile.mkdtemp())
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.main import app  # noqa: E402


def make_paper_image() -> bytes:
    """Synthetic exam page: 3 question blocks, one with a red X."""
    img = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(img)

    for i, top in enumerate((120, 520, 920)):
        # question text lines
        draw.rectangle([60, top, 900, top + 8], fill="black")
        draw.rectangle([60, top + 40, 820, top + 48], fill="black")
        draw.rectangle([60, top + 80, 760, top + 88], fill="black")
        # student answer line
        draw.rectangle([60, top + 140, 600, top + 148], fill=(60, 60, 60))

    # red X next to question 2 (wrong marker)
    draw.line([880, 640, 940, 700], fill=(220, 30, 30), width=9)
    draw.line([940, 640, 880, 700], fill=(220, 30, 30), width=9)
    # red check next to question 3
    draw.line([880, 1040, 905, 1065], fill=(210, 40, 40), width=9)
    draw.line([905, 1065, 945, 1010], fill=(210, 40, 40), width=9)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    failures: list[str] = []

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "parent@example.com", "password": "secret123", "display_name": "家长"},
        )
        if r.status_code != 201:
            print("register failed", r.status_code, r.text[:300])
            return 1
        token = r.json()["access_token"]
        print("registered, role =", r.json()["user"]["role"])

        headers = {"Authorization": f"Bearer {token}"}

        # duplicate registration must fail
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "parent@example.com", "password": "secret123"},
        )
        if r.status_code != 409:
            failures.append(f"duplicate register expected 409, got {r.status_code}")

        # wrong password
        r = client.post(
            "/api/v1/auth/login", json={"email": "parent@example.com", "password": "wrong"}
        )
        if r.status_code != 401:
            failures.append(f"bad login expected 401, got {r.status_code}")

        # upload
        img = make_paper_image()
        r = client.post(
            "/api/v1/papers/upload",
            headers=headers,
            files={"files": ("paper.png", img, "image/png")},
            data={"title": "数学单元测", "subject": "数学"},
        )
        if r.status_code != 201:
            print("upload failed", r.status_code, r.text[:500])
            return 1
        paper = r.json()
        print("paper:", paper["id"], paper["status"], "pages:", paper["page_count"])
        if paper["status"] != "ready":
            failures.append(f"paper status expected ready, got {paper['status']}")

        # workspace
        r = client.get(f"/api/v1/papers/{paper['id']}/workspace", headers=headers)
        if r.status_code != 200:
            print("workspace failed", r.text[:500])
            return 1
        ws = r.json()
        regions = ws["regions"]
        print("regions:", len(regions), "counts:", ws["counts"])
        if len(regions) < 3:
            failures.append(f"expected >=3 regions, got {len(regions)}")

        probs = [reg["wrong_probability"] for reg in regions]
        bands = [reg["band"] for reg in regions]
        print("probabilities:", probs)
        print("bands:", bands)
        if any(p is None for p in probs):
            failures.append("some probabilities are null")
        if not any(b == "recommended" for b in bands):
            failures.append("red-X region was not flagged as recommended")

        # page image endpoint
        r = client.get(f"/api/v1/papers/{paper['id']}/pages/1/image", headers=headers)
        if r.status_code != 200 or not r.content:
            failures.append("page image not served")

        # crop endpoint
        r = client.get(
            f"/api/v1/papers/{paper['id']}/region/{regions[0]['id']}/crop", headers=headers
        )
        if r.status_code != 200:
            failures.append(f"region crop failed {r.status_code}")

        # save draft selection
        r = client.patch(
            f"/api/v1/papers/{paper['id']}/selections",
            headers=headers,
            json={"selections": {str(regions[0]["id"]): True}},
        )
        if r.status_code != 200:
            failures.append(f"draft selection failed {r.status_code}")

        # nothing must be recorded before commit
        r = client.get("/api/v1/mistakes", headers=headers)
        if r.json()["total"] != 0:
            failures.append("mistake recorded before commit (VIOLATION)")

        # commit two regions
        ids = [regions[0]["id"], regions[1]["id"]]
        r = client.post(
            f"/api/v1/papers/{paper['id']}/commit",
            headers=headers,
            json={"region_ids": ids},
        )
        if r.status_code != 200:
            print("commit failed", r.text[:500])
            return 1
        commit = r.json()
        print("commit:", commit["created"], commit["existing"], commit["failed"])
        if commit["created"] != 2:
            failures.append(f"expected 2 created, got {commit['created']}")

        # committed again -> existing
        r = client.post(
            f"/api/v1/papers/{paper['id']}/commit", headers=headers, json={"region_ids": ids}
        )
        commit2 = r.json()
        print("recommit:", commit2["created"], commit2["existing"])
        if commit2["created"] != 0 or commit2["existing"] != 2:
            failures.append("idempotency broken: re-commit created new records")

        r = client.get("/api/v1/mistakes", headers=headers)
        body = r.json()
        print("mistakes total:", body["total"])
        if body["total"] != 2:
            failures.append(f"expected 2 mistakes, got {body['total']}")

        # AI analysis (offline fallback)
        r = client.post(
            "/api/v1/ai/analyze",
            headers=headers,
            json={"question_id": body["items"][0]["question_id"], "apply": True},
        )
        if r.status_code != 200:
            failures.append(f"analysis failed {r.status_code}")
        else:
            print("analysis provider:", r.json()["provider"], "fallback:", r.json()["is_fallback"])

        # review plan + list
        mid = body["items"][0]["id"]
        r = client.post(f"/api/v1/mistakes/{mid}/review-plan", headers=headers)
        if r.status_code != 201:
            failures.append(f"review plan failed {r.status_code}")
        r = client.get("/api/v1/reviews?horizon=month", headers=headers)
        if r.status_code != 200:
            failures.append(f"reviews failed {r.status_code}")

        # analytics
        r = client.get("/api/v1/analytics", headers=headers)
        if r.status_code != 200:
            failures.append(f"analytics failed {r.status_code}")
        else:
            print("analytics total:", r.json()["total_mistakes"])

        # exports
        for fmt in ("markdown", "obsidian", "pdf", "docx"):
            r = client.get(f"/api/v1/export/{fmt}", headers=headers)
            if r.status_code != 200 or len(r.content) < 50:
                failures.append(f"export {fmt} failed {r.status_code} size={len(r.content)}")
            else:
                print(f"export {fmt}: {len(r.content)} bytes")

        # status
        r = client.get("/api/v1/status")
        if r.status_code != 200:
            failures.append(f"status failed {r.status_code}")
        else:
            st = r.json()
            print("status ocr:", st["ocr"]["ocr_provider"], "ai:", st["ai_provider"], st["ai_available"])

        # auth enforcement
        r = client.get("/api/v1/mistakes")
        if r.status_code != 401:
            failures.append(f"unauthenticated access expected 401, got {r.status_code}")

        # cross-user isolation
        r2 = client.post(
            "/api/v1/auth/register", json={"email": "other@example.com", "password": "secret123"}
        )
        other = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        r = client.get(f"/api/v1/papers/{paper['id']}/workspace", headers=other)
        if r.status_code != 404:
            failures.append(f"cross-user access expected 404, got {r.status_code}")
        r = client.post(
            f"/api/v1/papers/{paper['id']}/commit", headers=other, json={"region_ids": ids}
        )
        if r.status_code != 404:
            failures.append(f"cross-user commit expected 404, got {r.status_code}")

        # invalid file type
        r = client.post(
            "/api/v1/papers/upload",
            headers=headers,
            files={"files": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        if r.status_code != 201 or r.json()["status"] != "failed":
            failures.append("invalid extension was not rejected")

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
