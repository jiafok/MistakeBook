"""Paper upload, page rendering, region detection and workspace APIs."""

from __future__ import annotations

import io
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
from ..services.crop import crop_key, crop_region
from ..services.dedup import content_hash
from ..services.ocr import get_region_engine
from ..services.subject import detect_subject as infer_subject
from ..storage import StorageError, get_storage

router = APIRouter(prefix="/papers", tags=["papers"])

MAX_PIXELS = 40_000_000


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
                question = Question(
                    paper_id=paper.id,
                    section_id=section.id,
                    label=box.label,
                    text=box.text,
                    subject=paper.subject,
                    order_index=box.region_order,
                    content_hash=content_hash(box.text or box.label),
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
    for page in sorted(paper.pages, key=lambda item: item.page_no):
        try:
            data = storage.read(page.storage_key)
        except StorageError as exc:
            raise HTTPException(status_code=409, detail=f"第 {page.page_no} 页图片不可用") from exc

        analysis = engine.analyze(data)

        # Drop the old geometry and any selections tied to it.
        old_regions = db.scalars(
            select(QuestionRegion).where(QuestionRegion.page_id == page.id)
        ).all()
        for region in old_regions:
            db.execute(
                RegionSelection.__table__.delete().where(
                    RegionSelection.region_id == region.id
                )
            )
            db.delete(region)
            cleared += 1
        db.flush()

        section = db.scalar(
            select(Section).where(Section.paper_id == paper.id, Section.order_index == page.page_no - 1)
        )
        if section is None:
            section = Section(paper_id=paper.id, title=f"第 {page.page_no} 页", order_index=page.page_no - 1)
            db.add(section)
            db.flush()

        for box in analysis.regions:
            question = Question(
                paper_id=paper.id,
                section_id=section.id,
                label=box.label,
                text=box.text,
                subject=paper.subject,
                order_index=box.region_order,
                content_hash=content_hash(box.text or box.label),
            )
            db.add(question)
            db.flush()
            db.add(
                QuestionRegion(
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
                )
            )

        page.width = analysis.width
        page.height = analysis.height
        page.ocr_text = analysis.text

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
    return PaperOut.model_validate(paper)


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
            existing += 1
            items.append(
                CommitItemResult(
                    region_id=region.id, status="existing", mistake_id=duplicate.id, reason="已存在"
                )
            )
            continue

        # Cut the question's own pixels out of the page so the mistake shows the
        # printed question exactly. Text reconstruction can never be identical:
        # figure labels are missed, fraction bars and chemical symbols are lost.
        image_key = ""
        try:
            crop = crop_region(
                get_storage().read(page.storage_key),
                region,
                page.width,
                page.height,
            )
            image_key = crop_key(user.id, question.id)
            get_storage().save(image_key, crop)
        except (StorageError, ValueError, OSError):
            # A missing crop must not block recording the mistake; the text is
            # still stored and the UI falls back to it.
            image_key = ""

        mistake = Mistake(
            user_id=user.id,
            question_id=question.id,
            paper_id=paper.id,
            region_id=region.id,
            question_image_key=image_key,
            selection_source=(
                "ai_preselected_accepted"
                if region.band == "recommended"
                else "user_explicit"
            ),
            wrong_probability_snapshot=region.wrong_probability,
            wrong_count=1,
            status="active",
        )
        db.add(mistake)
        db.flush()
        created += 1
        items.append(
            CommitItemResult(region_id=region.id, status="created", mistake_id=mistake.id)
        )

    db.commit()

    # Clear draft selections for committed regions
    db.query(RegionSelection).filter(
        RegionSelection.user_id == user.id,
        RegionSelection.region_id.in_([r.id for r in regions]),
    ).delete(synchronize_session=False)
    db.commit()

    return CommitOut(created=created, existing=existing, failed=failed, items=items)


@router.get("/{paper_id}/region/{region_id}/crop")
def get_region_crop(
    paper_id: int,
    region_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    paper = get_owned_paper(paper_id, db, user)
    region = db.get(QuestionRegion, region_id)
    if region is None or region.paper_id != paper.id:
        raise HTTPException(status_code=404, detail="区域不存在")
    page = db.get(PaperPage, region.page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="页面不存在")
    try:
        data = get_storage().read(page.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="页面图片不可用") from exc
    image = Image.open(io.BytesIO(data)).convert("RGB")
    box = (
        max(0, region.x),
        max(0, region.y),
        min(image.width, region.x + region.width),
        min(image.height, region.y + region.height),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise HTTPException(status_code=400, detail="区域无效")
    crop = image.crop(box)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")
