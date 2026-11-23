"""Paper upload, page rendering, region detection and workspace APIs."""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..deps import get_current_user, get_owned_paper
from ..models import (
    Mistake,
    Paper,
    PaperPage,
    Question,
    QuestionRegion,
    RegionSelection,
    Section,
    User,
)
from ..schemas import (
    CommitItemResult,
    CommitOut,
    CommitRequest,
    PageOut,
    PaperOut,
    RegionOut,
    SelectionPatch,
    WorkspaceOut,
)
from ..services.dedup import content_hash
from ..services.figures import (
    FigureBox,
    detect_page_figures,
    dump_figure_set,
    load_figure_set,
    store_figures,
)
from ..services.figurerepair import repair_paper
from ..services.ocr import get_region_engine
from ..services.subject import detect_subject as infer_subject
from ..storage import StorageError, get_storage

router = APIRouter(prefix="/papers", tags=["papers"])

logger = logging.getLogger("mistakebook.papers")

MAX_PIXELS = 40_000_000

# A stored region and a fresh detection are the same question only when they overlap
# this much. Re-analysis may renumber or resize boxes; below this threshold the two
# are different questions, and letting them share an id would silently move a
# recorded mistake onto different content.
_REANALYZE_MATCH_IOU = 0.5

# Re-anchoring an already-recorded mistake to a fresh question needs a long shared
# run of text and a clear lead over the runner-up. The measured signal on a real
# worksheet is strong (0.36 against 0.07 for the next candidate), so a modest
# threshold separates "same question, re-cut" from "different question".
_REANCHOR_MIN_RUN = 8
_REANCHOR_MIN_SCORE = 0.2
_REANCHOR_MARGIN = 2.0


def _validate_magic(data: bytes, ext: str) -> bool:
    if ext == "pdf":
        return data[:5] == b"%PDF-"
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in ("jpg", "jpeg"):
        return data[:2] == b"\xff\xd8"
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _extract_pages(data: bytes, ext: str) -> list[tuple[bytes, str]]:
    """Return list of (image_bytes, original_name_hint)."""
    if ext == "pdf":
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if doc.page_count == 0:
                raise HTTPException(status_code=400, detail="PDF 没有可用页面")
            if doc.page_count > settings.max_pdf_pages:
                raise HTTPException(
                    status_code=400,
                    detail=f"PDF 页数 {doc.page_count} 超过上限 {settings.max_pdf_pages}",
                )
            out: list[tuple[bytes, str]] = []
            for page_no in range(doc.page_count):
                page = doc.load_page(page_no)
                pix = page.get_pixmap(dpi=150)
                out.append((pix.tobytes("png"), f"page-{page_no + 1}.png"))
            return out
        finally:
            doc.close()

    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.load()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="无法解析图片文件") from exc

    if image.width * image.height > MAX_PIXELS:
        raise HTTPException(status_code=400, detail="图片像素过大")

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return [(buffer.getvalue(), "page-1.png")]


def _store_figures(
    *,
    storage,
    page: PaperPage,
    region: QuestionRegion,
    user_id: int,
    question_id: int,
) -> list[dict[str, object]]:
    """Cut every detected picture out of the page and store it for the mistake.

    Detection ran at upload time and the rectangles are on the region, so nothing
    is recomputed here. A single unreadable page or a bad rectangle never blocks
    recording the mistake: the text is stored either way and that figure is skipped.
    """
    boxes = [box for box in (FigureBox.from_dict(raw) for raw in load_figure_set(region.figures_json) or []) if box]
    if not boxes:
        return []
    try:
        page_data = storage.read(page.storage_key)
    except StorageError:
        return []
    return store_figures(
        storage,
        page_data=page_data,
        page_width=page.width,
        page_height=page.height,
        boxes=boxes,
        user_id=user_id,
        question_id=question_id,
    )


def _region_state(
    db: Session, region: QuestionRegion, user_id: int, recorded: set[int]
) -> tuple[bool, bool]:
    selection = db.scalar(
        select(RegionSelection).where(
            RegionSelection.region_id == region.id, RegionSelection.user_id == user_id
        )
    )
    if selection is not None:
        return selection.is_selected, region.id in recorded
    # default: high-confidence regions are pre-selected (draft only)
    return region.band == "recommended", region.id in recorded


def _closest_region(box, candidates):  # noqa: ANN001, ANN201
    """The stored region that best corresponds to a fresh detection, or None.

    Matching is geometric: a re-analysis may renumber questions, split a merged box
    or move a boundary, so the order cannot be trusted. A region is only considered
    the same question when the overlap is substantial; below that it is a different
    question and must not inherit the old one's id (and therefore its mistake).
    """
    best = None
    best_score = 0.0
    for region in candidates:
        score = _region_overlap(region, box)
        if score > best_score:
            best = region
            best_score = score
    return best if best_score >= _REANALYZE_MATCH_IOU else None


def _region_overlap(region, box) -> float:  # noqa: ANN001
    """Intersection-over-union of a stored region and a freshly detected box."""
    left = max(region.x, box.x)
    top = max(region.y, box.y)
    right = min(region.x + region.width, box.x + box.width)
    bottom = min(region.y + region.height, box.y + box.height)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = region.width * region.height + box.width * box.height - intersection
    return intersection / union if union > 0 else 0.0


def _longest_common_run(first: str, second: str) -> int:
    """Length of the longest substring the two texts share, ignoring whitespace.

    A stale region's text and a fresh question's text rarely match as whole strings
    (one may carry a page header, the other a cleaned version), so the evidence is
    the longest run of characters they agree on. That run is stable for Chinese,
    which has no word boundaries to lose.
    """
    a = "".join(first.split())
    b = "".join(second.split())
    if not a or not b:
        return 0
    best = 0
    previous = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        char = a[i - 1]
        for j in range(1, len(b) + 1):
            if char == b[j - 1]:
                run = previous[j - 1] + 1
                current[j] = run
                if run > best:
                    best = run
        previous = current
    return best


def _reattach_target(
    stale_question_id: int | None,
    candidates,
    db: Session,
):
    """The new region whose question best matches the one a mistake was recorded on.

    Returns None unless one candidate is convincing *and* clearly ahead of the
    runner-up: moving a mistake onto a different question would put the wrong
    content in the mistake book, which is worse than leaving it where it is.
    """
    if stale_question_id is None or not candidates:
        return None
    stale_question = db.get(Question, stale_question_id)
    if stale_question is None:
        return None
    stale_text = stale_question.text or ""

    scored: list[tuple[float, object]] = []
    for region in candidates:
        question = db.get(Question, region.question_id) if region.question_id else None
        if question is None:
            continue
        run = _longest_common_run(stale_text, question.text or "")
        shorter = min(len("".join(stale_text.split())), len("".join((question.text or "").split())))
        if shorter <= 0 or run < _REANCHOR_MIN_RUN:
            continue
        scored.append((run / shorter, region))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_region = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < _REANCHOR_MIN_SCORE:
        return None
    if second_score > 0 and best_score / second_score < _REANCHOR_MARGIN:
        return None
    return best_region


@router.post("/upload", response_model=PaperOut, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    files: list[UploadFile] = File(...),
    title: str = Form(default=""),
    subject: str = Form(default="未分类"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaperOut:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=400, detail=f"单次最多上传 {settings.max_upload_files} 个文件"
        )

    storage = get_storage()
    # The client may send "auto"/empty/未分类 to ask the server to infer the
    # subject from the recognised text. Anything else is an explicit decision.
    requested_subject = (subject or "").strip()
    subject_is_auto = requested_subject in ("", "auto", "未分类")
    paper = Paper(
        user_id=user.id,
        title=(title.strip() or "未命名试卷")[:255],
        subject=("未分类" if subject_is_auto else requested_subject)[:64],
        subject_source=("auto" if subject_is_auto else "user"),
        status="processing",
        ocr_provider=settings.ocr_provider,
        source_names=",".join((f.filename or "") for f in files)[:2000],
    )
    db.add(paper)
    db.flush()

    engine = get_region_engine()
    page_no = 0
    failures: list[str] = []

    for upload in files:
        name = (upload.filename or "upload").strip()
        ext = Path(name).suffix.lower().lstrip(".")
        if ext not in settings.allowed_extension_set:
            failures.append(f"{name}: 不支持的文件类型")
            continue

        data = await upload.read()
        if not data:
            failures.append(f"{name}: 空文件")
            continue
        if len(data) > settings.max_file_bytes:
            failures.append(f"{name}: 超过 {settings.max_file_bytes // (1024 * 1024)}MB 限制")
            continue
        if not _validate_magic(data, ext):
            failures.append(f"{name}: 文件内容与扩展名不符")
            continue

        try:
            pages = _extract_pages(data, ext)
        except HTTPException as exc:
            failures.append(f"{name}: {exc.detail}")
            continue

        for image_bytes, hint in pages:
            page_no += 1
            key = f"papers/{user.id}/{paper.id}/p{page_no}-{uuid.uuid4().hex[:8]}.png"
            try:
                storage.save(key, image_bytes)
            except StorageError as exc:
                failures.append(f"{name}: 存储失败 {exc}")
                continue

            analysis = engine.analyze(image_bytes)
            # Split the question into text plus the pictures it carries. The text
            # stays text so the book can be re-typeset; only the drawings that no
            # OCR can reproduce are kept as rectangles and cropped later.
            figure_map = detect_page_figures(image_bytes, analysis.regions, analysis.lines)
            page = PaperPage(
                paper_id=paper.id,
                page_no=page_no,
                storage_key=key,
                original_name=hint,
                width=analysis.width,
                height=analysis.height,
                render_dpi=150,
                ocr_text=analysis.text,
            )
            db.add(page)
            db.flush()

            section = Section(paper_id=paper.id, title=f"第 {page_no} 页", order_index=page_no - 1)
            db.add(section)
            db.flush()

            for box in analysis.regions:
                figures, cleaned_text = figure_map.get(
                    box.region_order, ([], box.text)
                )
                question = Question(
                    paper_id=paper.id,
                    section_id=section.id,
                    label=box.label,
                    text=cleaned_text,
                    subject=paper.subject,
                    order_index=box.region_order,
                    content_hash=content_hash(cleaned_text or box.label),
                )
                db.add(question)
                db.flush()

                db.add(
                    QuestionRegion(
                        paper_id=paper.id,
                        page_id=page.id,
                        page_no=page_no,
                        question_id=question.id,
                        x=box.x,
                        y=box.y,
                        width=box.width,
                        height=box.height,
                        region_order=box.region_order,
                        wrong_probability=box.wrong_probability,
                        band=box.band,
                        detection_status=box.detection_status,
                        figures_json=dump_figure_set([f.as_dict() for f in figures]),
                    )
                )

    paper.page_count = page_no

    # Infer the subject from what was actually recognised, but never override an
    # explicit choice made by the user.
    if subject_is_auto and page_no > 0:
        collected = "\n".join((p.ocr_text or "") for p in paper.pages)
        detected = infer_subject(collected)
        if detected:
            paper.subject = detected[:64]
            paper.subject_source = "auto"

    if page_no == 0:
        paper.status = "failed"
        paper.error_message = "; ".join(failures)[:2000] or "没有可处理的页面"
    else:
        paper.status = "ready"
        paper.error_message = "; ".join(failures)[:2000]

    db.commit()
    db.refresh(paper)
    return PaperOut.model_validate(paper)


@router.get("", response_model=list[PaperOut])
def list_papers(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PaperOut]:
    rows = db.scalars(
        select(Paper).where(Paper.user_id == user.id).order_by(Paper.created_at.desc()).limit(200)
    )
    return [PaperOut.model_validate(p) for p in rows]


@router.get("/{paper_id}", response_model=PaperOut)
def get_paper(
    paper_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> PaperOut:
    return PaperOut.model_validate(get_owned_paper(paper_id, db, user))


@router.delete("/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_paper(
    paper_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    paper = get_owned_paper(paper_id, db, user)
    storage = get_storage()
    for page in paper.pages:
        try:
            storage.delete(page.storage_key)
        except StorageError:
            pass
    for region in paper.regions:
        try:
            storage.delete(f"crops/{region.id}.png")
        except StorageError:
            pass
    db.delete(paper)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{paper_id}/reanalyze", response_model=PaperOut)
def reanalyze_paper(
    paper_id: int,
    redetect_subject: bool = Query(default=False, alias="detect_subject"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PaperOut:
    """Re-run region detection on an already-uploaded paper.

    Regions are computed once at upload time. When the detection engine improves,
    older papers would otherwise keep stale boxes, so this re-derives them from
    the stored page images. User selections are cleared because region ids change.
    """
    paper = get_owned_paper(paper_id, db, user)
    storage = get_storage()
    engine = get_region_engine()

    cleared = 0
    reanchored = 0
    detached = 0
    for page in sorted(paper.pages, key=lambda item: item.page_no):
        try:
            data = storage.read(page.storage_key)
        except StorageError as exc:
            raise HTTPException(status_code=409, detail=f"第 {page.page_no} 页图片不可用") from exc

        analysis = engine.analyze(data)
        figure_map = detect_page_figures(data, analysis.regions, analysis.lines)

        existing = list(
            db.scalars(
                select(QuestionRegion).where(QuestionRegion.page_id == page.id)
            )
        )

        section = db.scalar(
            select(Section).where(Section.paper_id == paper.id, Section.order_index == page.page_no - 1)
        )
        if section is None:
            section = Section(paper_id=paper.id, title=f"第 {page.page_no} 页", order_index=page.page_no - 1)
            db.add(section)
            db.flush()

        # Re-analysis must not delete a question that a user has already recorded: a
        # mistake points at its region and at its question, and both ids must survive
        # so the record keeps its provenance. Existing regions are therefore matched
        # to the new detections geometrically and updated in place; only regions that
        # no longer exist *and* carry no mistake are removed.
        unmatched = {region.id: region for region in existing}
        used_questions: set[int] = set()
        # Every region that now represents this page, collected as the loop builds
        # them. Re-querying would miss the rows created in this same transaction:
        # the session runs with autoflush off.
        resolved: list[QuestionRegion] = []

        for box in analysis.regions:
            figures, cleaned_text = figure_map.get(box.region_order, ([], box.text))
            partner = _closest_region(box, list(unmatched.values()))
            if partner is not None:
                del unmatched[partner.id]
                partner.x = box.x
                partner.y = box.y
                partner.width = box.width
                partner.height = box.height
                partner.page_no = page.page_no
                partner.region_order = box.region_order
                partner.wrong_probability = box.wrong_probability
                partner.band = box.band
                partner.detection_status = box.detection_status
                partner.figures_json = dump_figure_set([f.as_dict() for f in figures])

                question = db.get(Question, partner.question_id) if partner.question_id else None
                if question is None:
                    question = Question(paper_id=paper.id, section_id=section.id)
                    db.add(question)
                    db.flush()
                    partner.question_id = question.id
                question.section_id = section.id
                question.label = box.label
                question.text = cleaned_text
                question.subject = paper.subject
                question.order_index = box.region_order
                question.content_hash = content_hash(cleaned_text or box.label)
                used_questions.add(question.id)
                resolved.append(partner)
                continue

            question = Question(
                paper_id=paper.id,
                section_id=section.id,
                label=box.label,
                text=cleaned_text,
                subject=paper.subject,
                order_index=box.region_order,
                content_hash=content_hash(cleaned_text or box.label),
            )
            db.add(question)
            db.flush()
            used_questions.add(question.id)
            new_region = QuestionRegion(
                paper_id=paper.id,
                page_id=page.id,
                page_no=page.page_no,
                question_id=question.id,
                x=box.x,
                y=box.y,
                width=box.width,
                height=box.height,
                region_order=box.region_order,
                wrong_probability=box.wrong_probability,
                band=box.band,
                detection_status=box.detection_status,
                figures_json=dump_figure_set([f.as_dict() for f in figures]),
            )
            db.add(new_region)
            resolved.append(new_region)

        for stale in list(unmatched.values()):
            mistake = db.scalar(
                select(Mistake).where(Mistake.region_id == stale.id).limit(1)
            )
            if mistake is not None:
                # The old box was cut by a different engine (often a whole column),
                # so the mistake it holds may point at the wrong question entirely -
                # its stored text can be a page header. Re-anchor it to the freshly
                # detected question whose text it actually resembles, and only keep
                # the old box when no candidate is convincing.
                target = _reattach_target(
                    stale.question_id, resolved, db
                )
                if target is not None:
                    # A region created in this pass has no id until it is flushed,
                    # and a mistake cannot reference a NULL region.
                    db.flush()
                    mistake.region_id = target.id
                    mistake.question_id = target.question_id
                    db.execute(
                        RegionSelection.__table__.delete().where(
                            RegionSelection.region_id == stale.id
                        )
                    )
                    old_question_id = stale.question_id
                    db.delete(stale)
                    cleared += 1
                    if (
                        old_question_id is not None
                        and old_question_id not in used_questions
                        and db.scalar(
                            select(Mistake.id)
                            .where(Mistake.question_id == old_question_id)
                            .limit(1)
                        )
                        is None
                    ):
                        old_question = db.get(Question, old_question_id)
                        if old_question is not None:
                            db.delete(old_question)
                    continue
                # The old box covered several questions (an old engine often cut a
                # whole column), so nothing in the data says which question the user
                # actually meant. Guessing would move the record onto different
                # content, which is worse than admitting it: the mistake is kept but
                # detached from its region, and the unusable box is dropped so the
                # workspace stops offering it for selection.
                mistake.region_id = None
                db.execute(
                    RegionSelection.__table__.delete().where(
                        RegionSelection.region_id == stale.id
                    )
                )
                old_question_id = stale.question_id
                db.delete(stale)
                cleared += 1
                continue
            db.execute(
                RegionSelection.__table__.delete().where(
                    RegionSelection.region_id == stale.id
                )
            )
            question_id = stale.question_id
            db.delete(stale)
            cleared += 1
            # A question with no mistake and no region left is an orphan produced by
            # this page's analysis, so it goes too.
            if question_id is not None and question_id not in used_questions:
                still_used = db.scalar(
                    select(Mistake.id).where(Mistake.question_id == question_id).limit(1)
                )
                if still_used is None:
                    other_region = db.scalar(
                        select(QuestionRegion.id)
                        .where(QuestionRegion.question_id == question_id)
                        .limit(1)
                    )
                    if other_region is None:
                        stale_question = db.get(Question, question_id)
                        if stale_question is not None:
                            db.delete(stale_question)
        db.flush()

    # Refresh an auto-detected subject, or any subject when the caller explicitly
    # asks for re-detection. A plain reanalyze never overrides a user's choice.
    if paper.subject_source == "auto" or redetect_subject:
        detected = infer_subject("\n".join((p.ocr_text or "") for p in paper.pages))
        if detected:
            paper.subject = detected[:64]
            if redetect_subject:
                paper.subject_source = "auto"

    paper.ocr_provider = settings.ocr_provider
    paper.status = "ready"
    paper.error_message = ""
    db.commit()
    db.refresh(paper)
    logger.info(
        "reanalyze paper %s | removed=%s reanchored=%s detached=%s",
        paper.id,
        cleared,
        reanchored,
        detached,
    )
    return PaperOut.model_validate(paper)


@router.post("/{paper_id}/repair-figures", response_model=dict)
def repair_figures(
    paper_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Re-derive question figures for a paper that was analysed before they existed.

    Papers analysed by an older build have ``figures_json`` empty on every region,
    and their mistakes carry a whole-question screenshot instead of text plus
    pictures. Printing those means printing a photograph of text, which cannot be
    re-typeset. This recomputes the figure rectangles from the stored page images
    and backfills them onto both the regions and the recorded mistakes. Text is
    left alone, because the stored text is already the authoritative question.
    """
    paper = get_owned_paper(paper_id, db, user)
    return repair_paper(
        db,
        paper=paper,
        user=user,
        storage=get_storage(),
        engine=get_region_engine(),
    )


@router.get("/{paper_id}/pages/{page_no}/image")
def get_page_image(
    paper_id: int,
    page_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    paper = get_owned_paper(paper_id, db, user)
    page = db.scalar(
        select(PaperPage).where(PaperPage.paper_id == paper.id, PaperPage.page_no == page_no)
    )
    if page is None:
        raise HTTPException(status_code=404, detail="页面不存在")
    try:
        data = get_storage().read(page.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="页面图片不可用") from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{paper_id}/workspace", response_model=WorkspaceOut)
def get_workspace(
    paper_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> WorkspaceOut:
    paper = get_owned_paper(paper_id, db, user)

    recorded = set(
        db.scalars(
            select(Mistake.region_id).where(
                Mistake.user_id == user.id,
                Mistake.paper_id == paper.id,
                Mistake.region_id.is_not(None),
            )
        )
    )
    recorded_ids = {int(r) for r in recorded if r is not None}

    pages = db.scalars(select(PaperPage).where(PaperPage.paper_id == paper.id).order_by(PaperPage.page_no))
    page_out = [
        PageOut(
            id=p.id,
            page_no=p.page_no,
            width=p.width,
            height=p.height,
            image_url=f"/api/v1/papers/{paper.id}/pages/{p.page_no}/image",
        )
        for p in pages
    ]

    regions = db.scalars(
        select(QuestionRegion)
        .where(QuestionRegion.paper_id == paper.id)
        .order_by(QuestionRegion.page_no, QuestionRegion.region_order)
    )
    region_out: list[RegionOut] = []
    selected_count = 0
    existing_count = 0
    new_count = 0

    for region in regions:
        question = db.get(Question, region.question_id) if region.question_id else None
        is_recorded = region.id in recorded_ids
        selected, _ = _region_state(db, region, user.id, recorded_ids)

        if is_recorded:
            existing_count += 1
            selected = True
        elif selected:
            selected_count += 1
            new_count += 1

        region_out.append(
            RegionOut(
                id=region.id,
                page_no=region.page_no,
                page_id=region.page_id,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                region_order=region.region_order,
                label=question.label if question else f"第 {region.region_order + 1} 题",
                text=question.text if question else "",
                wrong_probability=region.wrong_probability,
                band=region.band,
                detection_status=region.detection_status,
                is_selected=selected,
                is_recorded=is_recorded,
                figure_count=len(load_figure_set(region.figures_json) or ()),
            )
        )

    return WorkspaceOut(
        paper=PaperOut.model_validate(paper),
        pages=page_out,
        regions=region_out,
        counts={
            "selected": selected_count,
            "new": new_count,
            "existing": existing_count,
            "total": len(region_out),
        },
    )


@router.patch("/{paper_id}/selections", response_model=dict)
def patch_selections(
    paper_id: int,
    payload: SelectionPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    paper = get_owned_paper(paper_id, db, user)
    valid_ids = set(
        db.scalars(select(QuestionRegion.id).where(QuestionRegion.paper_id == paper.id))
    )
    updated = 0
    for raw_id, value in payload.selections.items():
        try:
            region_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if region_id not in valid_ids:
            # silently skip unknown ids; never leak other papers
            continue
        selection = db.scalar(
            select(RegionSelection).where(
                RegionSelection.region_id == region_id, RegionSelection.user_id == user.id
            )
        )
        if selection is None:
            db.add(
                RegionSelection(region_id=region_id, user_id=user.id, is_selected=bool(value))
            )
        else:
            selection.is_selected = bool(value)
            selection.updated_at = utcnow()
        updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/{paper_id}/commit", response_model=CommitOut)
def commit_selection(
    paper_id: int,
    payload: CommitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommitOut:
    paper = get_owned_paper(paper_id, db, user)

    requested = {int(r) for r in payload.region_ids if str(r).isdigit()}
    if not requested:
        raise HTTPException(status_code=400, detail="没有选择任何题目")

    regions = list(
        db.scalars(
            select(QuestionRegion).where(
                QuestionRegion.paper_id == paper.id, QuestionRegion.id.in_(requested)
            )
        )
    )
    if not regions:
        raise HTTPException(status_code=400, detail="所选题目不属于该试卷")

    pages = {p.id: p for p in db.scalars(select(PaperPage).where(PaperPage.paper_id == paper.id))}

    created = 0
    existing = 0
    failed = 0
    items: list[CommitItemResult] = []

    for region in regions:
        page = pages.get(region.page_id)
        if page is None:
            failed += 1
            items.append(CommitItemResult(region_id=region.id, status="failed", reason="页缺失"))
            continue
        # Server-side geometry validation (never trust the client)
        if (
            region.x < 0
            or region.y < 0
            or region.width <= 0
            or region.height <= 0
            or region.x + region.width > page.width
            or region.y + region.height > page.height
        ):
            failed += 1
            items.append(
                CommitItemResult(region_id=region.id, status="failed", reason="区域坐标越界")
            )
            continue

        question = db.get(Question, region.question_id) if region.question_id else None
        if question is None:
            failed += 1
            items.append(CommitItemResult(region_id=region.id, status="failed", reason="题目缺失"))
            continue

        duplicate = db.scalar(
            select(Mistake).where(
                Mistake.user_id == user.id,
                Mistake.question_id == question.id,
                Mistake.status == "active",
            )
        )
        if duplicate is not None:
            duplicate.wrong_count += 1
            duplicate.updated_at = utcnow()
            # A mistake recorded before figures were extracted has no pictures yet.
            # Re-committing the same question is the natural moment to backfill them,
            # otherwise the printed record would silently keep missing its diagram.
            if not load_figure_set(duplicate.figures_json):
                backfilled = _store_figures(
                    storage=get_storage(),
                    page=page,
                    region=region,
                    user_id=user.id,
                    question_id=question.id,
                )
                if backfilled:
                    duplicate.figures_json = dump_figure_set(backfilled)
            existing += 1
            items.append(
                CommitItemResult(
                    region_id=region.id, status="existing", mistake_id=duplicate.id, reason="已存在"
                )
            )
            continue

        mistake = Mistake(
            user_id=user.id,
            paper_id=paper.id,
            question_id=question.id,
            region_id=region.id,
            status="active",
            wrong_count=1,
        )
        db.add(mistake)
        created += 1
        items.append(CommitItemResult(region_id=region.id, status="created", mistake_id=mistake.id))

    db.commit()
    return CommitOut(paper_id=paper.id, created=created, existing=existing, failed=failed, items=items)
