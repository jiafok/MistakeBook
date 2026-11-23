"""Cut a rectangle out of a stored page image.

This is a plain pixel cutter, not the way a question is represented. The mistake
book stores the question as **text** (``Question.text``) so the book can be
re-typeset and printed in bulk; only the drawings that no OCR can reproduce travel
as pictures, and those are detected and cropped by
:mod:`app.services.figures`.

The whole-question crop this module can produce is therefore legacy: rows recorded
before figures existed still own a ``question_image_key`` and are still served from
it, but new mistakes store no such screenshot.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from ..models import QuestionRegion

# Crops are shown at up to 2x on high-DPI phones; 1.6 keeps them crisp without
# turning a single question into a multi-megabyte file.
CROP_SCALE = 1.6

# A little breathing room around the detected box so strokes on the border of the
# region are not clipped. Extra space *outside* the question can only add printed
# pixels, never drop any.
PAD_X_RATIO = 0.02
PAD_Y_RATIO = 0.015


def crop_region(
    page_data: bytes,
    region: QuestionRegion,
    page_width: int,
    page_height: int,
) -> bytes:
    """Return PNG bytes for one rectangle cut from the page image.

    ``region`` coordinates are in the page's own pixel space. They are clamped to the
    real image size, because a stored box may predate a re-analysis and sit a pixel
    or two outside the picture.
    """
    with Image.open(io.BytesIO(page_data)) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        actual_width, actual_height = image.size
        # Stored metadata can disagree with the file after an EXIF rotation, so the
        # file is the authority.
        width = actual_width or page_width
        height = actual_height or page_height

        if int(region.width) <= 0 or int(region.height) <= 0:
            raise ValueError("region has no area")

        pad_x = max(2, int(width * PAD_X_RATIO))
        pad_y = max(2, int(height * PAD_Y_RATIO))

        left = max(0, int(region.x) - pad_x)
        top = max(0, int(region.y) - pad_y)
        right = min(width, int(region.x) + int(region.width) + pad_x)
        bottom = min(height, int(region.y) + int(region.height) + pad_y)

        if right - left < 8 or bottom - top < 8:
            raise ValueError("region too small to crop")

        crop = image.crop((left, top, right, bottom))

        # Always upscale: a 130px-wide crop is unreadable on a phone, and the
        # quality loss from LANCZOS on text is far smaller than the loss from
        # showing the question at its raw pixel size.
        scale = CROP_SCALE
        if scale != 1.0:
            crop = crop.resize(
                (int(crop.width * scale), int(crop.height * scale)),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        crop.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def crop_key(user_id: int, question_id: int) -> str:
    """Legacy whole-question screenshot key; no longer written by new commits."""
    return f"mistakes/{user_id}/q{question_id}.png"
