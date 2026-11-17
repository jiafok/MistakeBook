"""Focused regression checks for OCR orientation and two-column pages."""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.papers import _extract_pages
from app.services.ocr import NullOcr, RapidOcr, RegionEngine, _find_column_split, _text_signal


def _image_bytes(image: Image.Image, fmt: str = "PNG", **save_kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def test_exif_orientation_is_applied_before_storage() -> None:
    image = Image.new("RGB", (40, 80), "white")
    exif = Image.Exif()
    exif[274] = 6
    pages = _extract_pages(_image_bytes(image, "JPEG", exif=exif), "jpg")
    normalized = Image.open(io.BytesIO(pages[0][0]))
    assert normalized.size == (80, 40), normalized.size


def test_two_columns_are_segmented_independently() -> None:
    image = Image.new("RGB", (1200, 1000), "white")
    draw = ImageDraw.Draw(image)
    for y in range(60, 900, 45):
        draw.text((80, y), "Left column question and answer", fill="black")
        draw.text((680, y), "Right column question and answer", fill="black")

    split = _find_column_split(image.convert("L"))
    assert split is not None, "expected the central gutter to be detected"

    engine = RegionEngine()
    engine.provider = NullOcr()
    analysis = engine.analyze(_image_bytes(image))
    assert analysis.width == 1200 and analysis.height == 1000
    assert len(analysis.regions) >= 2, len(analysis.regions)
    assert any(region.x < split for region in analysis.regions)
    assert any(region.x >= split for region in analysis.regions)
    assert all(region.x + region.width <= analysis.width for region in analysis.regions)


def test_rapidocr_result_boxes_are_parsed() -> None:
    provider = RapidOcr.__new__(RapidOcr)
    provider.available = True
    provider.reason = "ok"
    provider._engine = lambda _image: (
        [([[10, 20], [90, 20], [90, 45], [10, 45]], "化学试题", 0.97)],
        [1.0, 0.1, 0.2],
    )

    lines = provider.extract_lines(Image.new("RGB", (100, 100), "white"))
    assert len(lines) == 1
    assert (lines[0].x0, lines[0].y0, lines[0].x1, lines[0].y1) == (10, 20, 90, 45)
    assert lines[0].text == "化学试题"
    assert lines[0].confidence == 0.97


def test_algebra_variable_x_is_not_a_wrong_mark() -> None:
    assert _text_signal("12 x8 = 96") == (0.0, "")
    assert _text_signal("solve for x")[0] == 0
    assert _text_signal("teacher mark: X")[0] > 0


if __name__ == "__main__":
    test_exif_orientation_is_applied_before_storage()
    print("ok  EXIF orientation applied")
    test_two_columns_are_segmented_independently()
    print("ok  two-column segmentation")
    test_rapidocr_result_boxes_are_parsed()
    print("ok  RapidOCR detection boxes parsed")
    test_algebra_variable_x_is_not_a_wrong_mark()
    print("ok  algebraic x is not a grading mark")
    print("ALL OCR CHECKS PASSED")