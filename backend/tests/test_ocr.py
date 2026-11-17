"""Regression checks for OCR orientation, column assignment and question boxes.

The important behaviour is geometric: each numbered question must become exactly
one box that hugs its own text, with left/right columns never merged. This is what
the on-image selection UI depends on.
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.papers import _extract_pages
from app.services.ocr import (
    NullOcr,
    OcrLine,
    QUESTION_START_RE,
    RegionEngine,
    _group_question_segments,
    _ink_threshold,
    _regions_from_ocr_lines,
    _split_x_from_lines,
    _text_signal,
)


def _line(x0: int, y0: int, x1: int, y1: int, text: str) -> OcrLine:
    return OcrLine(x0, y0, x1, y1, text, 0.95)


def test_exif_orientation_is_applied_before_storage() -> None:
    image = Image.new("RGB", (40, 80), "white")
    exif = Image.Exif()
    exif[274] = 6
    pages = _extract_pages(_image_bytes(image, "JPEG", exif=exif), "jpg")
    normalized = Image.open(io.BytesIO(pages[0][0]))
    assert normalized.size == (80, 40), normalized.size


def _image_bytes(image: Image.Image, fmt: str = "PNG", **save_kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def test_question_number_regex_only_matches_real_starts() -> None:
    assert QUESTION_START_RE.match("1.构成氯化钠的微观粒子是")
    assert QUESTION_START_RE.match("12、某物质")
    assert QUESTION_START_RE.match("第 4 题 计算")
    # Must not treat these as question starts.
    assert not QUESTION_START_RE.match("(1) 12 x8 = 96")
    assert not QUESTION_START_RE.match("13")
    assert not QUESTION_START_RE.match("一、选择题")
    assert not QUESTION_START_RE.match("A.原子")


def test_one_box_per_question_in_a_single_column() -> None:
    lines = [
        _line(80, 100, 400, 130, "姓名：某某"),          # header, must be dropped
        _line(80, 200, 500, 230, "1.第一题题干"),
        _line(90, 240, 500, 270, "A.选项甲"),
        _line(80, 300, 500, 330, "2.第二题题干"),
        _line(90, 340, 500, 370, "A.选项乙"),
        _line(80, 400, 500, 430, "3.第三题题干"),
    ]
    regions = _regions_from_ocr_lines(lines, 1000, 1200, split_x=None)

    assert len(regions) == 3, [r.text for r in regions]
    assert regions[0].text.startswith("1.")
    assert regions[1].text.startswith("2.")
    assert regions[2].text.startswith("3.")
    # The header line must not become a selectable box.
    assert all("姓名" not in r.text for r in regions)
    # Boxes must hug their own text, not the whole page width.
    for region in regions:
        assert region.width < 1000, region.width


def test_two_columns_are_never_merged() -> None:
    lines = [
        _line(80, 200, 480, 230, "1.左栏第一题"),
        _line(80, 260, 480, 290, "2.左栏第二题"),
        _line(80, 320, 480, 350, "3.左栏第三题"),
        _line(540, 200, 940, 230, "4.右栏第一题"),
        _line(540, 260, 940, 290, "5.右栏第二题"),
        _line(540, 320, 940, 350, "6.右栏第三题"),
    ]
    split_x = _split_x_from_lines(lines, 1000)
    assert split_x is not None, "expected a two-column gutter"

    regions = _regions_from_ocr_lines(lines, 1000, 1200, split_x)
    assert len(regions) == 6, [r.text for r in regions]

    left = [r for r in regions if r.x + r.width / 2 < split_x]
    right = [r for r in regions if r.x + r.width / 2 >= split_x]
    assert len(left) == 3 and len(right) == 3
    # No box may straddle the gutter.
    assert all(r.x + r.width <= split_x + 5 for r in left)
    assert all(r.x >= split_x - 5 for r in right)


def test_single_column_page_has_no_gutter() -> None:
    lines = [
        _line(80, 200 + index * 60, 800, 230 + index * 60, f"{index + 1}.题目内容")
        for index in range(6)
    ]
    assert _split_x_from_lines(lines, 1000) is None


def test_otsu_threshold_handles_grey_photo() -> None:
    # A photographed page: grey paper with dark text, not white paper.
    image = Image.new("L", (200, 200), 165)
    draw = ImageDraw.Draw(image)
    for y in range(20, 180, 20):
        draw.rectangle([20, y, 180, y + 6], fill=40)

    threshold = _ink_threshold(image)
    # Must sit between the ink and the paper, so the page is not "all ink".
    assert 40 < threshold < 165, threshold


def test_algebra_variable_x_is_not_a_wrong_mark() -> None:
    assert _text_signal("12 x8 = 96") == (0.0, "")
    assert _text_signal("solve for x")[0] == 0
    assert _text_signal("teacher mark: X")[0] > 0


def test_header_lines_are_not_questions() -> None:
    lines = [
        _line(80, 10, 400, 40, "物质构成的奥秘"),
        _line(80, 50, 400, 80, "一、选择题"),
        _line(80, 200, 500, 230, "1.真正的第一题"),
        _line(80, 260, 500, 290, "2.真正的第二题"),
    ]
    groups = _group_question_segments(lines)
    assert len(groups) == 2, [g[0].text for g in groups]
    assert groups[0][0].text.startswith("1.")


if __name__ == "__main__":
    test_exif_orientation_is_applied_before_storage()
    print("ok  EXIF orientation applied")
    test_question_number_regex_only_matches_real_starts()
    print("ok  question-number regex is strict")
    test_one_box_per_question_in_a_single_column()
    print("ok  one box per question")
    test_two_columns_are_never_merged()
    print("ok  two columns stay separate")
    test_single_column_page_has_no_gutter()
    print("ok  single column has no gutter")
    test_otsu_threshold_handles_grey_photo()
    print("ok  otsu threshold handles grey photos")
    test_algebra_variable_x_is_not_a_wrong_mark()
    print("ok  algebraic x is not a grading mark")
    test_header_lines_are_not_questions()
    print("ok  header lines excluded")
    print("ALL OCR CHECKS PASSED")
