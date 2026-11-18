"""Regression tests for the original-question crop.

The mistake book must show the question exactly as printed, so the crop has to be a
faithful copy of the page pixels. These tests pin the properties that make it
faithful: no resampling loss of content, clamping at page edges, and never writing an
empty crop.
"""

import io
import sys

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.crop import PAD_X_RATIO, PAD_Y_RATIO, crop_key, crop_region

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}  {detail}")


class Region:
    def __init__(self, x, y, width, height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


def make_page(width=400, height=600):
    """White page with a black rectangle we can look for in the crop."""
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    draw.rectangle([100, 200, 200, 260], fill="black")
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue()


# --- 1. the crop contains the printed pixels -----------------------------
page = make_page()
region = Region(90, 190, 130, 90)
data = crop_region(page, region, 400, 600)
crop = Image.open(io.BytesIO(data)).convert("RGB")

dark = sum(1 for pixel in crop.getdata() if sum(pixel) < 300)
check("crop is a real image", len(data) > 500, len(data))
check("crop contains the printed mark", dark > 500, dark)

# --- 2. padding never clips the region, and is clamped to the page -------
tiny = Region(0, 0, 20, 20)
data = crop_region(page, tiny, 400, 600)
crop = Image.open(io.BytesIO(data))
pad_x = max(2, int(400 * PAD_X_RATIO))
pad_y = max(2, int(600 * PAD_Y_RATIO))
check(
    "corner region is clamped, not negative",
    crop.width >= 20 and crop.height >= 20,
    crop.size,
)
check(
    "corner region keeps requested padding where possible",
    crop.width >= 20 + pad_x - 2,
    crop.size,
)

# --- 3. a region at the far edge does not overflow the page -------------
edge = Region(380, 580, 20, 20)
data = crop_region(page, edge, 400, 600)
crop = Image.open(io.BytesIO(data))
check("edge region stays inside the page", crop.width <= 400, crop.size)

# --- 4. an empty region is rejected instead of saving a blank file ------
try:
    crop_region(page, Region(10, 10, 0, 0), 400, 600)
    check("zero-size region rejected", False, "no exception")
except ValueError:
    check("zero-size region rejected", True)

# --- 5. the storage key is stable so re-committing overwrites ------------
check("crop key is stable", crop_key(1, 86) == crop_key(1, 86), crop_key(1, 86))
check(
    "crop key is per user and question",
    crop_key(1, 86) != crop_key(2, 86) and crop_key(1, 86) != crop_key(1, 87),
)

# --- 6. page dimensions from metadata do not break a correct image ------
data = crop_region(page, region, 0, 0)
check("zero metadata falls back to the real image size", len(data) > 500, len(data))

print()
if failures:
    print(f"{len(failures)} CROP CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL CROP CHECKS PASSED")
