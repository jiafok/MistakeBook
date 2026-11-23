"""Convert legacy mistake records from "a screenshot of the question" to text + figures.

Why this exists
---------------
An earlier build recorded a mistake by cropping the *whole question* out of the
paper and storing it as ``question_image_key``. That is unprintable in a mistake
book: a screenshot of text cannot be re-typeset, cannot be searched, and prints at
whatever resolution the photo happened to have. The book therefore stores the
question as text (``Question.text``) and only the drawings as pictures
(:mod:`app.services.figures`).

Rows recorded by the old build still carry the screenshot and have no figure list,
so they keep rendering (and printing) as a picture of text. This module repairs
them in place: the drawings are located again from the stored page image, cut out,
stored, and the legacy screenshot reference is dropped. The question text is never
touched - it is already the authoritative question.

Two entry points
----------------
* :func:`repair_paper` - repair one paper, used by the API endpoint and the UI.
* :func:`repair_legacy_records` - repair every affected record, run once at startup
  so an upgraded install needs no manual step.

Both are idempotent: a region whose figure list was already computed is skipped, so
a second run is a no-op rather than a second round of cropping.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Mistake, Paper, Question, QuestionRegion, User
from ..storage import StorageError, StorageBackend
from .figures import (
    detect_page_figures,
    dump_figure_set,
    load_figure_set,
    store_figures,
    strip_figure_labels,
    tidy_question_text,
)
from .ocr import RegionEngine

logger = logging.getLogger("mistakebook.figures")

# A stored region and a freshly detected box must overlap at least this much to be
# considered the same question. A paper analysed by an older engine may number its
# questions differently, and a wrong match would print one question's diagram under
# another question's text - worse than printing no diagram at all.
_MATCH_IOU = 0.5


def _box_overlap(region: QuestionRegion, box) -> float:  # noqa: ANN001
    """Intersection-over-union of a stored region and a fresh detection."""
    left = max(region.x, box.x)
    top = max(region.y, box.y)
    right = min(region.x + region.width, box.x + box.width)
    bottom = min(region.y + region.height, box.y + box.height)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = region.width * region.height + box.width * box.height - intersection
    return intersection / union if union > 0 else 0.0


def repair_paper(
    db: Session,
    *,
    paper: Paper,
    user: User,
    storage: StorageBackend,
    engine: RegionEngine,
) -> dict[str, int]:
    """Re-derive the drawings of one paper and backfill them onto its records.

    Returns ``{"regions", "figures", "mistakes"}`` so the caller can report what
    happened. A page whose image is unreadable is skipped, never fatal.
    """
    regions_touched = 0
    figures_total = 0
    mistakes_updated = 0
    texts_cleaned = 0

    for page in sorted(paper.pages, key=lambda item: item.page_no):
        stored = list(
            db.scalars(
                select(QuestionRegion).where(
                    QuestionRegion.page_id == page.id,
                    QuestionRegion.paper_id == paper.id,
                )
            )
        )
        if not stored:
            continue

        # Two independent jobs, with different scopes:
        #  * the figure list is only computed for regions that never had one, so a
        #    paper analysed by the current build is never rewritten behind the user;
        #  * the text tidy is idempotent and runs for every region, because a legacy
        #    row's text still carries the labels that its new pictures now contain.
        pending = [region for region in stored if load_figure_set(region.figures_json) is None]
        if not pending and not any(region.question_id for region in stored):
            continue

        try:
            page_data = storage.read(page.storage_key)
        except StorageError:
            logger.warning("repair: page %s image unavailable", page.id)
            continue

        analysis = engine.analyze(page_data)
        figure_map = detect_page_figures(page_data, analysis.regions, analysis.lines)
        detected = {int(box.region_order): box for box in analysis.regions}

        for region in stored:
            best_order, best_score = None, 0.0
            for order, box in detected.items():
                score = _box_overlap(region, box)
                if score > best_score:
                    best_order, best_score = order, score
            if best_order is None or best_score < _MATCH_IOU:
                # The question moved or the engine split it differently; leave the
                # row alone so a later re-analysis can still fill it in.
                continue

            figures, _cleaned = figure_map.get(best_order, ([], ""))

            if region.question_id is not None:
                question = db.get(Question, region.question_id)
                if question is not None and figures:
                    # A word printed inside a picture is now drawn *with* the
                    # picture, so leaving it in the question text would print it
                    # twice. Only standalone occurrences are removed, which is why
                    # the same word inside an answer survives.
                    without_labels = strip_figure_labels(
                        question.text or "", analysis.lines, figures
                    )
                    tidied = tidy_question_text(without_labels)
                    if tidied != question.text:
                        question.text = tidied
                        texts_cleaned += 1

            if load_figure_set(region.figures_json) is not None:
                continue

            region.figures_json = dump_figure_set([figure.as_dict() for figure in figures])
            regions_touched += 1
            figures_total += len(figures)

            mistake = _recorded_mistake(db, user.id, region.question_id)
            if mistake is None or load_figure_set(mistake.figures_json) is not None:
                # Already converted (possibly to "text only"): never touch it twice.
                continue

            items: list[dict[str, object]] = []
            if figures:
                items = store_figures(
                    storage,
                    page_data=page_data,
                    page_width=page.width,
                    page_height=page.height,
                    boxes=figures,
                    user_id=user.id,
                    question_id=int(region.question_id or 0),
                )
            if items:
                mistake.figures_json = dump_figure_set(items)
            elif not _has_text(db, region.question_id):
                # No drawing and no text: the screenshot is the only content there
                # is, so dropping it would leave an empty record.
                continue
            else:
                # A text-only question is already printable; record the empty set
                # so the startup repair recognises this row as done.
                mistake.figures_json = dump_figure_set([])
            # The print path now has everything it needs as text plus drawings, so
            # the whole-question screenshot is no longer referenced. The file stays
            # on disk, which keeps the change reversible.
            if mistake.question_image_key:
                mistake.question_image_key = ""
                mistakes_updated += 1

    db.commit()
    return {
        "regions": regions_touched,
        "figures": figures_total,
        "mistakes": mistakes_updated,
        "texts": texts_cleaned,
    }


def _recorded_mistake(db: Session, user_id: int, question_id: int | None) -> Mistake | None:
    if question_id is None:
        return None
    return db.scalar(
        select(Mistake).where(
            Mistake.user_id == user_id,
            Mistake.question_id == question_id,
            Mistake.status == "active",
        )
    )


def _has_text(db: Session, question_id: int | None) -> bool:
    if question_id is None:
        return False
    text = db.scalar(select(Question.text).where(Question.id == question_id))
    return bool((text or "").strip())


def repair_legacy_records(
    db: Session, *, storage: StorageBackend, engine: RegionEngine
) -> dict[str, int]:
    """Repair every mistake that still prints as a screenshot of the question.

    Run once at startup. An install with nothing to repair costs one indexed query.
    """
    rows = db.execute(
        select(Mistake, Paper)
        .join(Paper, Paper.id == Mistake.paper_id)
        .where(
            Mistake.question_image_key != "",
            Mistake.figures_json == "[]",
            Mistake.status == "active",
        )
    ).all()
    if not rows:
        return {"papers": 0, "regions": 0, "figures": 0, "mistakes": 0}

    owners = {
        paper.id: db.get(User, paper.user_id) for _mistake, paper in rows
    }
    papers: dict[int, Paper] = {paper.id: paper for _mistake, paper in rows}

    totals = {"papers": 0, "regions": 0, "figures": 0, "mistakes": 0}
    for paper_id, paper in papers.items():
        user = owners.get(paper_id)
        if user is None:
            continue
        result = repair_paper(
            db, paper=paper, user=user, storage=storage, engine=engine
        )
        totals["papers"] += 1
        totals["regions"] += result["regions"]
        totals["figures"] += result["figures"]
        totals["mistakes"] += result["mistakes"]

    logger.info(
        "repaired legacy screenshot records | papers=%s regions=%s figures=%s mistakes=%s",
        totals["papers"],
        totals["regions"],
        totals["figures"],
        totals["mistakes"],
    )
    return totals


def figures_need_repair(db: Session) -> bool:
    """True when at least one mistake still prints as a screenshot of the question.

    Checked before the repair runs so a healthy install pays one indexed query at
    startup and nothing else.
    """
    exists = db.scalar(
        select(Mistake.id)
        .where(
            Mistake.question_image_key != "",
            Mistake.figures_json == "[]",
            Mistake.status == "active",
        )
        .limit(1)
    )
    return exists is not None


def paper_needs_repair(db: Session, paper_id: int) -> bool:
    """True when a paper still has regions with no computed figure list."""
    regions = db.scalars(
        select(QuestionRegion.figures_json).where(QuestionRegion.paper_id == paper_id)
    )
    return any(load_figure_set(raw) is None for raw in regions)


__all__ = [
    "figures_need_repair",
    "paper_needs_repair",
    "repair_legacy_records",
    "repair_paper",
]
