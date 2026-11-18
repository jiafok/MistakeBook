"""Cut a question's own pixels out of the page image.

Why crop instead of rebuilding text
-----------------------------------
OCR assembly can never be *identical* to the printed question: small figure labels
are missed by the detector, maths such as ``12m/n`` loses its fraction bar, and
chemical symbols come back mangled. For a mistake book the requirement is that the
reviewed question reads exactly like the original paper, so the dependable answer is
to keep the original pixels of the question region.

The crop is taken straight from the stored page image, so it carries the figure, the
table rules, the subscripts and the answer mark exactly as printed. Text is still
stored alongside for search, AI analysis and export, but it is no longer what the
learner reads.
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
    """Return PNG bytes for one question region cut from the page image.

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
    """Stable storage key so re-committing overwrites instead of duplicating."""
    return f"mistakes/{user_id}/q{question_id}.png"
