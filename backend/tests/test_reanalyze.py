"""Regression tests for re-analysing an already-uploaded paper.

Re-detection replaces every region box on a page, and a recorded mistake points at
both a region and a question. An earlier build deleted the old regions and inserted
new ones, which produced three failures that only show up on real data:

* the questions it created the first time were never deleted, so every re-analysis
  grew the questions table without bound (89 -> 98 -> 107 rows);
* a mistake's region was deleted underneath it, so its provenance was lost;
* papers analysed by an older engine kept whole-column boxes - a "question" that is
  half the page - which made selection in the workspace meaningless.

These tests pin the properties that prevent all three:

* re-analysing twice leaves the row counts unchanged;
* a recorded mistake survives, and its region and question still exist;
* the resulting boxes are per-question, never a whole page column;
* workspaces report the same boxes on a second run.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/reanalyze.db")
os.environ.setdefault("STORAGE_LOCAL_DIR", tempfile.mkdtemp())
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Mistake,
    Paper,
    PaperPage,
    Question,
    QuestionRegion,
    User,
)
from app.storage import get_storage  # noqa: E402
from app.services.figures import dump_figure_set  # noqa: E402

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}  {detail}")


# --- a page with three numbered questions -------------------------------
PAGE_W, PAGE_H = 900, 1200
page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
draw = ImageDraw.Draw(page)


def question_block(top: int, number: str) -> None:
    # Question number and body text as solid bars, which OCR-free analysis still
    # segments by projection.
    draw.rectangle([70, top, 70 + 40, top + 22], fill="black")
    for offset in (40, 74):
        draw.rectangle([70, top + offset, 70 + 640, top + offset + 18], fill="black")


question_block(120, "1")
question_block(420, "2")
question_block(720, "3")

buffer = io.BytesIO()
page.save(buffer, format="PNG")
page_bytes = buffer.getvalue()

storage = get_storage()
storage.save("papers/1/1/p1-test.png", page_bytes)

Base.metadata.create_all(bind=engine)


def counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            "questions": db.query(Question).count(),
            "regions": db.query(QuestionRegion).count(),
            "mistakes": db.query(Mistake).count(),
        }


def seed() -> tuple[int, int]:
    """A paper whose stored regions are whole-column boxes, like an old analysis.

    The mistake is recorded against one of those stale boxes, which is exactly the
    situation that made old installs select the wrong content.
    """
    with SessionLocal() as db:
        for model in (Mistake, QuestionRegion, Question, PaperPage, Paper, User):
            db.query(model).delete()
        db.commit()

        user = User(username="owner", email="owner@example.com", display_name="x", password_hash="x")
        db.add(user)
        db.flush()

        paper = Paper(user_id=user.id, title="旧试卷", subject="物理", status="ready", page_count=1)
        db.add(paper)
        db.flush()

        page_row = PaperPage(
            paper_id=paper.id,
            page_no=1,
            storage_key="papers/1/1/p1-test.png",
            width=PAGE_W,
            height=PAGE_H,
        )
        db.add(page_row)
        db.flush()

        # Two whole-column boxes: a stale engine's idea of "questions".
        region_ids = []
        for index, (x, width) in enumerate(((0, PAGE_W // 2), (PAGE_W // 2, PAGE_W // 2))):
            question = Question(
                paper_id=paper.id,
                label=f"第 {index + 1} 题",
                text=f"旧整栏文字 {index + 1} " + "填充" * 20,
                subject="物理",
                order_index=index,
            )
            db.add(question)
            db.flush()
            region = QuestionRegion(
                paper_id=paper.id,
                page_id=page_row.id,
                page_no=1,
                question_id=question.id,
                x=x,
                y=0,
                width=width,
                height=PAGE_H,
                region_order=index,
                band="normal",
                figures_json="[]",
            )
            db.add(region)
            db.flush()
            region_ids.append((region.id, question.id))

        mistake = Mistake(
            user_id=user.id,
            question_id=region_ids[0][1],
            paper_id=paper.id,
            region_id=region_ids[0][0],
            figures_json="[]",
            status="active",
        )
        db.add(mistake)
        db.commit()
        return user.id, paper.id


user_id, paper_id = seed()

from app.security import create_access_token  # noqa: E402

headers = {"Authorization": f"Bearer {create_access_token(subject=str(user_id))}"}

with TestClient(app) as client:
    before = counts()
    region_count_before = before["regions"]
    print("before:", before)

    response = client.post(f"/api/v1/papers/{paper_id}/reanalyze", headers=headers)
    check("reanalyze succeeds", response.status_code == 200, response.text[:200])

    after_first = counts()
    print("after 1:", after_first)

    client.post(f"/api/v1/papers/{paper_id}/reanalyze", headers=headers)
    after_second = counts()
    print("after 2:", after_second)

    check(
        "a second re-analysis changes no row counts",
        after_first == after_second,
        (after_first, after_second),
    )
    check(
        "re-analysis does not grow the questions table",
        after_second["questions"] <= after_first["questions"],
        after_second,
    )
    check("the mistake is never deleted", after_second["mistakes"] == 1, after_second)

    with SessionLocal() as db:
        mistake = db.query(Mistake).one()
        question = db.get(Question, mistake.question_id)
        check("the mistake still points at an existing question", question is not None, mistake.question_id)
        if mistake.region_id is not None:
            region = db.get(QuestionRegion, mistake.region_id)
            check("the mistake's region still exists", region is not None, mistake.region_id)
        else:
            # Detaching is allowed when the stale box cannot be resolved, but the
            # record itself must remain readable.
            check("a detached mistake keeps its question text", bool(question and question.text), question)

        regions = (
            db.query(QuestionRegion)
            .filter(QuestionRegion.paper_id == paper_id)
            .order_by(QuestionRegion.region_order)
            .all()
        )
        check("regions exist after re-analysis", len(regions) > 0, len(regions))
        check(
            "no region is a whole page column any more",
            all(region.width < PAGE_W * 0.95 or region.height < PAGE_H * 0.95 for region in regions),
            [(r.x, r.y, r.width, r.height) for r in regions],
        )
        check(
            "every region points at a question",
            all(region.question_id is not None for region in regions),
            [(r.id, r.question_id) for r in regions],
        )
        check(
            "region order is unique and dense",
            [region.region_order for region in regions] == list(range(len(regions))),
            [region.region_order for region in regions],
        )

    # The workspace must not drift between two runs.
    first_ws = client.get(f"/api/v1/papers/{paper_id}/workspace", headers=headers).json()
    client.post(f"/api/v1/papers/{paper_id}/reanalyze", headers=headers)
    second_ws = client.get(f"/api/v1/papers/{paper_id}/workspace", headers=headers).json()
    boxes = lambda ws: [(r["x"], r["y"], r["width"], r["height"]) for r in ws["regions"]]
    check("the workspace is stable across re-analyses", boxes(first_ws) == boxes(second_ws))
    check(
        "the workspace reports no whole-column box",
        all(
            r["width"] < PAGE_W * 0.95 or r["height"] < PAGE_H * 0.95
            for r in second_ws["regions"]
        ),
        [(r["x"], r["y"], r["width"], r["height"]) for r in second_ws["regions"]],
    )


    # --- committing a region must carry its drawings onto the record -------
    # An intermediate build stored the question text but dropped the drawings, so a
    # new mistake printed as text with its diagram missing. The rectangles are
    # already on the region, so the commit path must copy them across.
    with SessionLocal() as db:
        target = (
            db.query(QuestionRegion)
            .filter(
                QuestionRegion.paper_id == paper_id,
                QuestionRegion.figures_json != '{"v":2,"items":[]}',
            )
            .first()
        )
        if target is None:
            # No drawing was detected on this synthetic page, so graft one onto a
            # region to exercise the commit path regardless.
            target = (
                db.query(QuestionRegion)
                .filter(QuestionRegion.paper_id == paper_id)
                .first()
            )
            target.figures_json = dump_figure_set(
                [{"x": 70, "y": target.y + 10, "width": 120, "height": 90}]
            )
            db.commit()
        target_id = target.id

    response = client.post(
        f"/api/v1/papers/{paper_id}/commit",
        headers=headers,
        json={"region_ids": [target_id]},
    )
    check("commit succeeds", response.status_code == 200, response.text[:200])
    created_id = ((response.json().get("items") or [{}])[0]).get("mistake_id")
    check("commit returns the new mistake id", bool(created_id), response.json())
    if created_id:
        detail = client.get(f"/api/v1/mistakes/{created_id}", headers=headers).json()
        check(
            "a committed mistake carries its drawings",
            len(detail.get("figures") or []) > 0,
            detail.get("figures"),
        )
        # The fixture is drawn as solid bars, so OCR finds no characters: the detail
        # must still expose a text field. Whether it has content is checked against
        # what the question row actually holds, not against a hardcoded non-empty.
        with SessionLocal() as db:
            stored = db.get(Question, db.query(Mistake).filter(Mistake.id == created_id).one().question_id)
            stored_text = (stored.text or "") if stored is not None else ""
        check(
            "a committed mistake carries its question text",
            (detail.get("text") or "") == stored_text,
            (detail.get("text"), stored_text),
        )
        if detail.get("figures"):
            image = client.get(
                f"/api/v1/mistakes/{created_id}/figures/0", headers=headers
            )
            check("the drawing is served", image.status_code == 200, image.status_code)
            check(
                "the drawing is a real image",
                image.content[:4] == b"\x89PNG",
                image.content[:8],
            )

print()
if failures:
    print(f"{len(failures)} REANALYZE CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL REANALYZE CHECKS PASSED")
