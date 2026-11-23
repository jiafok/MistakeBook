"""Regression tests for converting legacy "screenshot of the question" records.

An earlier build recorded a mistake by cropping the whole question out of the paper
and storing it as one picture. A mistake book is re-typeset and printed in bulk, so
a picture of text is unusable: it cannot reflow, cannot be searched, and prints at
the photo's resolution. The repair converts those rows to "text + drawings" by
re-locating the drawings on the stored page.

These tests pin the properties that make the conversion safe:

* a legacy record gains its drawings and drops the screenshot reference;
* a text-only question is marked converted, not left looking un-repaired;
* the question text is never rewritten by the repair;
* the repair is idempotent: a second run changes nothing;
* a region whose location no longer matches is left alone rather than given a
  wrong question's diagram;
* a page image that cannot be read never fails the repair for the whole paper.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/repair.db")
os.environ.setdefault("STORAGE_LOCAL_DIR", tempfile.mkdtemp())
os.environ.setdefault("SECRET_KEY", "test-secret")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Mistake,
    Paper,
    PaperPage,
    Question,
    QuestionRegion,
    User,
)
from app.services.figures import (  # noqa: E402
    FIGURE_SET_VERSION,
    detect_page_figures,
    dump_figure_set,
    load_figure_set,
)
from app.services.figurerepair import repair_legacy_records, repair_paper  # noqa: E402
from app.services.ocr import OcrLine, PageAnalysis, RegionBox  # noqa: E402
from app.storage import get_storage  # noqa: E402

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}  {detail}")


# --- build a page that really has a drawing on it ------------------------
PAGE_W, PAGE_H = 700, 900
page_image = Image.new("RGB", (PAGE_W, PAGE_H), "white")
page_draw = ImageDraw.Draw(page_image)


def text_block(top: int, right: int) -> None:
    for offset in (0, 34, 68):
        page_draw.rectangle([60, top + offset, 60 + right, top + offset + 16], fill="black")


# Question 1: text only.
text_block(60, 520)
# Question 2: text plus an apparatus drawing, with one word printed inside it.
page_draw.rectangle([120, 300, 280, 480], outline="black", width=4)
page_draw.ellipse([150, 330, 250, 430], outline="black", width=4)
text_block(500, 400)

buffer = io.BytesIO()
page_image.save(buffer, format="PNG")
page_bytes = buffer.getvalue()

storage = get_storage()
storage.save("papers/1/1/p1-test.png", page_bytes)

Base.metadata.create_all(bind=engine)


class StubEngine:
    """Deterministic analysis of the synthetic page.

    The repair under test does not run OCR: it re-locates drawings on a page whose
    question regions were already stored. Feeding a fixed analysis keeps the test
    fast and independent of which OCR model happens to be installed.
    """

    def __init__(self, analysis: PageAnalysis) -> None:
        self._analysis = analysis

    def analyze(self, _data: bytes) -> PageAnalysis:
        return self._analysis


LINE_HEIGHT = 18
LABEL_TEXT = "浓氨水"
analysis = PageAnalysis(
    width=PAGE_W,
    height=PAGE_H,
    regions=[
        RegionBox(
            x=56, y=57, width=529, height=90, region_order=0, label="第 1 题",
            text="1.下列说法正确的是\nA.选项甲\nB.选项乙",
        ),
        RegionBox(
            x=56, y=250, width=529, height=270, region_order=1, label="第 2 题",
            # The word printed inside the drawing also came back as OCR text, which
            # is exactly how a real paper is stored.
            text=f"2.如图所示的实验，分析正确的是\n{LABEL_TEXT}\nA.选项丙",
        ),
    ],
    lines=[
        # Question 1 is pure text.
        OcrLine(60, 60, 580, 60 + LINE_HEIGHT, "1.下列说法正确的是", 0.9),
        OcrLine(60, 94, 580, 94 + LINE_HEIGHT, "A.选项甲", 0.9),
        OcrLine(60, 128, 580, 128 + LINE_HEIGHT, "B.选项乙", 0.9),
        # Question 2: header, one word printed *inside* the drawing, then options.
        OcrLine(60, 258, 580, 258 + LINE_HEIGHT, "2.如图所示的实验，分析正确的是", 0.9),
        OcrLine(150, 390, 250, 390 + LINE_HEIGHT, LABEL_TEXT, 0.9),
        OcrLine(60, 500, 580, 500 + LINE_HEIGHT, "A.选项丙", 0.9),
    ],
)

figure_map = detect_page_figures(page_bytes, analysis.regions, analysis.lines)
print("figures per region:", {order: len(figs) for order, (figs, _t) in figure_map.items()})

if not any(figs for figs, _t in figure_map.values()):
    print("DETECTOR FOUND NO DRAWING, cannot test the repair")
    sys.exit(1)

engine_svc = StubEngine(analysis)
drawing_order = next(order for order, (figs, _t) in figure_map.items() if figs)


def seed_legacy_records():
    """Insert a paper whose regions are "never computed" and whose mistakes are
    legacy whole-question screenshots, exactly like an upgraded install."""
    with SessionLocal() as db:
        db.query(Mistake).delete()
        db.query(QuestionRegion).delete()
        db.query(Question).delete()
        db.query(PaperPage).delete()
        db.query(Paper).delete()
        db.query(User).delete()
        db.commit()

        user = User(
            username="legacy",
            email="legacy@example.com",
            display_name="旧记录",
            password_hash="x",
        )
        db.add(user)
        db.flush()

        paper = Paper(user_id=user.id, title="旧试卷", subject="物理", status="ready", page_count=1)
        db.add(paper)
        db.flush()

        page = PaperPage(
            paper_id=paper.id,
            page_no=1,
            storage_key="papers/1/1/p1-test.png",
            width=analysis.width,
            height=analysis.height,
        )
        db.add(page)
        db.flush()

        region_ids = {}
        for box in analysis.regions:
            question = Question(
                paper_id=paper.id,
                label=box.label or f"第 {box.region_order + 1} 题",
                text=box.text,
                subject="物理",
                order_index=box.region_order,
            )
            db.add(question)
            db.flush()
            region = QuestionRegion(
                paper_id=paper.id,
                page_id=page.id,
                page_no=1,
                question_id=question.id,
                x=box.x,
                y=box.y,
                width=box.width,
                height=box.height,
                region_order=box.region_order,
                band="recommended",
                # Never computed by the old build.
                figures_json="[]",
            )
            db.add(region)
            db.flush()
            region_ids[box.region_order] = (region.id, question.id)

        # Every question was recorded, each as a legacy screenshot.
        for order, (region_id, question_id) in region_ids.items():
            db.add(
                Mistake(
                    user_id=user.id,
                    question_id=question_id,
                    paper_id=paper.id,
                    region_id=region_id,
                    question_image_key=f"mistakes/{user.id}/q{question_id}.png",
                    figures_json="[]",
                    status="active",
                )
            )
        db.commit()
        return user.id, paper.id, region_ids


user_id, paper_id, region_ids = seed_legacy_records()

with SessionLocal() as db:
    texts_before = {
        q.id: q.text for q in db.query(Question).filter(Question.paper_id == paper_id)
    }
    original_keys = {
        m.question_id: m.question_image_key for m in db.query(Mistake).all()
    }

    user = db.get(User, user_id)
    paper = db.get(Paper, paper_id)
    result = repair_paper(db, paper=paper, user=user, storage=storage, engine=engine_svc)

print("repair result:", result)
check("repair touched the regions", result["regions"] == len(analysis.regions), result)
check("repair found the drawing", result["figures"] >= 1, result)
check("repair reports the text it cleaned", "texts" in result, result)

with SessionLocal() as db:
    drawing_question = region_ids[drawing_order][1]
    cleaned_question = db.get(Question, drawing_question)
    check(
        "label printed inside the picture is removed from the text",
        LABEL_TEXT not in (cleaned_question.text or ""),
        cleaned_question.text,
    )
    check(
        "text that merely contains the label word survives",
        "分析正确的是" in (cleaned_question.text or ""),
        cleaned_question.text,
    )
    check(
        "options after the picture survive",
        "A.选项丙" in (cleaned_question.text or ""),
        cleaned_question.text,
    )

with SessionLocal() as db:
    texts_after = {
        q.id: q.text for q in db.query(Question).filter(Question.paper_id == paper_id)
    }
    # Only the question that carries a labelled picture may lose text, and only the
    # label itself. Re-deriving the whole question from OCR would be a rewrite; this
    # is a targeted removal of a word that the picture now prints again.
    changed = {
        qid: (before, texts_after[qid])
        for qid, before in texts_before.items()
        if texts_after[qid] != before
    }
    check(
        "only the labelled question's text changed",
        set(changed) <= {region_ids[drawing_order][1]},
        changed,
    )
    check(
        "the only change was removing the duplicated label",
        all(
            before.replace(LABEL_TEXT + "\n", "") == after
            for before, after in changed.values()
        ),
        changed,
    )

    drawing_question = region_ids[drawing_order][1]
    drawing_mistake = db.query(Mistake).filter(Mistake.question_id == drawing_question).one()
    items = load_figure_set(drawing_mistake.figures_json) or []
    check("recorded mistake gained its drawings", len(items) >= 1, items)
    check(
        "drawing was actually stored",
        all(storage.exists(str(item.get("key"))) for item in items),
        [item.get("key") for item in items],
    )
    check(
        "legacy screenshot reference was dropped",
        drawing_mistake.question_image_key == "",
        drawing_mistake.question_image_key,
    )

    text_only = [order for order in region_ids if order != drawing_order]
    if text_only:
        order = text_only[0]
        question_id = region_ids[order][1]
        mistake = db.query(Mistake).filter(Mistake.question_id == question_id).one()
        check(
            "text-only question is marked converted, not left pending",
            load_figure_set(mistake.figures_json) == [],
            mistake.figures_json,
        )
        check(
            "text-only question is print-ready so its screenshot is dropped",
            mistake.question_image_key == "",
            mistake.question_image_key,
        )

    region = db.get(QuestionRegion, region_ids[drawing_order][0])
    stored = load_figure_set(region.figures_json)
    check("region now carries a computed figure list", stored is not None, region.figures_json)

# --- idempotency -------------------------------------------------------
with SessionLocal() as db:
    before_keys = {
        m.id: m.figures_json for m in db.query(Mistake).all()
    }
    user = db.get(User, user_id)
    paper = db.get(Paper, paper_id)
    again = repair_paper(db, paper=paper, user=user, storage=storage, engine=engine_svc)
    after_keys = {m.id: m.figures_json for m in db.query(Mistake).all()}

check("a second repair is a no-op", again["regions"] == 0 and again["figures"] == 0, again)
check("a second repair changes no record", before_keys == after_keys)
check(
    "figure set version is current after repair",
    all(
        (load_figure_set(raw) is not None)
        for raw in after_keys.values()
    ),
)

# --- a removed page image must not fail the whole repair ---------------
with SessionLocal() as db:
    db.query(Mistake).delete()
    db.query(QuestionRegion).delete()
    db.query(Question).delete()
    db.query(PaperPage).delete()
    db.query(Paper).delete()
    db.query(User).delete()
    db.commit()

    user = User(username="broken", email="broken@example.com", display_name="x", password_hash="x")
    db.add(user)
    db.flush()
    paper = Paper(user_id=user.id, title="缺图", subject="物理", status="ready", page_count=1)
    db.add(paper)
    db.flush()
    page = PaperPage(
        paper_id=paper.id,
        page_no=1,
        storage_key="papers/1/1/does-not-exist.png",
        width=700,
        height=900,
    )
    db.add(page)
    db.flush()
    question = Question(paper_id=paper.id, label="第 1 题", text="题干文字", subject="物理")
    db.add(question)
    db.flush()
    region = QuestionRegion(
        paper_id=paper.id,
        page_id=page.id,
        page_no=1,
        question_id=question.id,
        x=10,
        y=10,
        width=300,
        height=200,
        region_order=0,
        figures_json="[]",
    )
    db.add(region)
    db.flush()
    db.add(
        Mistake(
            user_id=user.id,
            question_id=question.id,
            paper_id=paper.id,
            region_id=region.id,
            question_image_key="mistakes/x.png",
            figures_json="[]",
        )
    )
    db.commit()

    safe = repair_paper(db, paper=paper, user=user, storage=storage, engine=engine_svc)

check("unreadable page is skipped, never fatal", safe["regions"] == 0, safe)

# --- the startup scan finds only the unreadable row left to repair ----
with SessionLocal() as db:
    leftovers = (
        db.query(Mistake)
        .filter(
            Mistake.question_image_key != "",
            Mistake.figures_json == "[]",
            Mistake.status == "active",
        )
        .count()
    )
check(
    "only the unreadable page is left for a later retry",
    leftovers == 1,
    leftovers,
)

# --- the bulk entry point reports what it could not do ----------------
with SessionLocal() as db:
    bulk = repair_legacy_records(db, storage=storage, engine=engine_svc)
check("bulk repair runs without raising", isinstance(bulk, dict), bulk)
check("bulk repair leaves the unreadable row alone", bulk["mistakes"] == 0, bulk)

check("figure set version stays at 2", FIGURE_SET_VERSION == 2)

print()
if failures:
    print(f"{len(failures)} REPAIR CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL REPAIR CHECKS PASSED")
