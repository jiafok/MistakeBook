"""Redraw a figure from its geometry instead of pasting the paper's pixels.

Why not embed the crop
----------------------
A mistake book is printed. A crop is a photograph of a drawing: it carries scanner
noise, uneven lighting and paper texture, it prints soft above the size it was
photographed at, and it drags the sheet's greys into a document that is otherwise
clean black text. So the drawing is *redrawn*: its ink is traced into closed
polygons, and the words printed inside the picture are put back as real text at
their original position and size.

How the ink becomes geometry
----------------------------
Every contour of the ink mask is approximated into a polygon and kept together with
its nesting depth. Binary ink is then reproduced exactly by the even-odd fill rule:
a ring at even depth is solid ink, a ring at odd depth is a hole inside it. The same
ring set drives both the vector output (PDF) and the raster fallback (DOCX /
Markdown), so the two can never disagree.

Labels
------
Words inside a picture were recognised by OCR - that is why they exist as text at
all. Their glyph outlines are part of the trace, but printing outlines at 8 pt looks
worse than printing the word, so a label's rings are held back and the word is drawn
instead. When no font with the needed glyphs is installed the traced outlines are
used, so a word is never silently dropped.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .figures import _ink_mask

try:  # opencv arrives with the OCR engine; exporting must survive without it
    import cv2
except Exception:  # pragma: no cover - only on a broken install
    cv2 = None  # type: ignore[assignment]


# Tracing runs on the stored crop, which is already upscaled for printing. A
# contour is straightened until a vertex deviates more than this many pixels, so
# scanner noise is dropped while a drawn curve survives.
_APPROX_EPSILON = 0.9

# Guards so a mis-traced page can never produce an enormous path.
_MAX_RINGS = 4000
_MAX_POINTS = 60000

# A label box smaller than this in either direction is OCR noise, not a word.
_MIN_LABEL_SIDE = 4

# Fonts carrying CJK glyphs, most desirable first. The container image has none of
# the Windows ones, hence the Noto / WenQuanYi entries.
_CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


@dataclass(frozen=True)
class Ring:
    """One closed contour as a flat ``(x0, y0, x1, y1, ...)`` tuple of pixels."""

    points: tuple[float, ...]
    # 0 is ink, 1 is a hole inside it, 2 is ink inside that hole, ...
    depth: int = 0

    def as_pairs(self) -> list[tuple[float, float]]:
        return list(zip(self.points[0::2], self.points[1::2]))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = self.points[0::2]
        ys = self.points[1::2]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def is_hole(self) -> bool:
        return self.depth % 2 == 1


@dataclass(frozen=True)
class Label:
    """A word printed inside the picture, in the crop's pixel space."""

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def font_size(self) -> float:
        """Point size that reproduces the printed word.

        Measured from CJK glyphs: a character's ink fills very nearly the whole em
        box, so the ink height *is* the size the paper was typeset at.
        """
        return max(1.0, self.height * 1.02)

    def contains(self, ring: Ring) -> bool:
        left, top, right, bottom = ring.bounds
        slack = 3.0
        return (
            left >= self.x - slack
            and top >= self.y - slack
            and right <= self.x + self.width + slack
            and bottom <= self.y + self.height + slack
        )

    def refit(self, ink: tuple[float, float, float, float] | None) -> "Label":
        """Replace the OCR box with the glyph ink box found inside it.

        OCR rectangles are padded - they cover the line, not the strokes - so a
        font sized from the box prints noticeably larger than the paper did. The
        traced ink is the ground truth: it is exactly where the printed glyphs are.
        """
        if ink is None:
            return self
        left, top, right, bottom = ink
        width, height = right - left, bottom - top
        # Reject an ink box that is mostly some other part of the drawing: a real
        # label's ink nearly fills the row the recogniser marked.
        if height < self.height * 0.35 or height > self.height * 1.6:
            return self
        if width > self.width * 1.4 or width < self.width * 0.3:
            return self
        return Label(text=self.text, x=left, y=top, width=width, height=height)


@dataclass(frozen=True)
class Drawing:
    """A traced picture: vector geometry plus the words printed inside it."""

    width: int
    height: int
    rings: tuple[Ring, ...] = ()
    labels: tuple[Label, ...] = ()

    def art_rings(self) -> tuple[Ring, ...]:
        """Geometry of the artwork alone, without the traced label glyphs."""
        if not self.labels:
            return self.rings
        return tuple(ring for ring in self.rings if not self._in_any_label(ring))

    def label_rings(self, label: Label) -> tuple[Ring, ...]:
        return tuple(ring for ring in self.rings if label.contains(ring))

    def _in_any_label(self, ring: Ring) -> bool:
        return any(label.contains(ring) for label in self.labels)

    @property
    def is_empty(self) -> bool:
        return not self.rings and not self.labels


def trace_figure(png: bytes, raw_labels: object = None) -> Drawing | None:
    """Trace ``png`` into polygons and attach the labels printed inside it.

    ``raw_labels`` is the stored label list (``{"t","x","y","w","h"}`` in crop
    pixels). Returns None when the crop cannot be traced, so the caller can fall
    back to the stored picture instead of printing an empty figure.
    """
    labels = parse_labels(raw_labels)
    try:
        with Image.open(io.BytesIO(png)) as opened:
            image = opened.convert("L")
            width, height = image.size
            ink = np.asarray(_ink_mask(image), dtype=np.uint8) > 0
    except Exception:
        return None

    if not ink.any() or cv2 is None:
        return None

    try:
        contours, hierarchy = cv2.findContours(
            ink.astype(np.uint8), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )
    except Exception:  # pragma: no cover - defensive
        return None
    if hierarchy is None:
        return None

    parents = hierarchy[0]
    rings: list[Ring] = []
    total_points = 0
    for index, contour in enumerate(contours):
        if len(rings) >= _MAX_RINGS or total_points >= _MAX_POINTS:
            break
        approx = cv2.approxPolyDP(contour, _APPROX_EPSILON, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        points: list[float] = []
        for point in approx:
            points.append(float(point[0]))
            points.append(float(point[1]))
        rings.append(Ring(points=tuple(points), depth=_depth_of(parents, index)))
        total_points += len(approx)

    if not rings:
        return None
    return Drawing(
        width=width,
        height=height,
        rings=tuple(rings),
        labels=tuple(label.refit(_ink_bounds(ink, label)) for label in labels),
    )


def _ink_bounds(
    ink: np.ndarray, label: Label
) -> tuple[float, float, float, float] | None:
    """Tight box of the printed glyphs inside ``label``'s OCR rectangle.

    The OCR rectangle is padded and often clips neighbouring strokes (an arrow
    pointing at the label, the edge of the apparatus), so the ink in the whole
    rectangle is not the word. The word is the dominant run of ink rows; everything
    above and below it belongs to the drawing.
    """
    height, width = ink.shape
    left = max(0, int(label.x))
    top = max(0, int(label.y))
    right = min(width, int(round(label.x + label.width)))
    bottom = min(height, int(round(label.y + label.height)))
    if right - left < 2 or bottom - top < 2:
        return None

    window = ink[top:bottom, left:right]
    row_counts = window.sum(axis=1)
    if not row_counts.any():
        return None
    # Glyph rows carry real ink; a stroke of the drawing crossing the label's
    # rectangle is a couple of pixels wide and falls away below this.
    active = row_counts >= 3
    best = (0, 0)
    run_start: int | None = None
    for index, is_active in enumerate(active):
        if is_active and run_start is None:
            run_start = index
        elif not is_active and run_start is not None:
            if index - run_start > best[1] - best[0]:
                best = (run_start, index)
            run_start = None
    if run_start is not None and len(active) - run_start > best[1] - best[0]:
        best = (run_start, len(active))
    start, stop = best
    if stop - start < 2:
        return None

    band = window[start:stop]
    columns = np.flatnonzero(band.any(axis=0))
    if columns.size == 0:
        return None
    return (
        float(left + columns[0]),
        float(top + start),
        float(left + columns[-1] + 1),
        float(top + stop),
    )


def render_png(drawing: Drawing, scale: float) -> bytes:
    """Rasterise the trace at ``scale`` for formats that cannot carry vectors.

    Word documents and Markdown get a print-resolution render of the *trace*, not
    the original crop: at 300 DPI the polygons are indistinguishable from the
    printed drawing while the scanner noise is gone.
    """
    width = max(1, int(round(drawing.width * scale)))
    height = max(1, int(round(drawing.height * scale)))
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    # Painter order: outer ink first, then every hole, then ink inside those holes.
    for ring in sorted(drawing.art_rings(), key=lambda item: item.depth):
        draw.polygon(
            [(x * scale, y * scale) for x, y in ring.as_pairs()],
            fill=255 if ring.is_hole else 0,
        )

    font_path = cjk_font_path() if drawing.labels else None
    for label in drawing.labels:
        if font_path is None:
            for ring in drawing.label_rings(label):
                draw.polygon([(x * scale, y * scale) for x, y in ring.as_pairs()], fill=0)
            continue
        try:
            font = ImageFont.truetype(font_path, max(1, int(round(label.font_size * scale))))
        except Exception:  # pragma: no cover - defensive
            continue
        draw.text(
            (label.x * scale, (label.y + label.height) * scale),
            label.text,
            font=font,
            fill=0,
            anchor="ls",
        )

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def parse_labels(raw: object) -> tuple[Label, ...]:
    """Read the stored label list; unreadable entries are skipped, never fatal."""
    if not isinstance(raw, (list, tuple)):
        return ()
    labels: list[Label] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("t") or "").strip()
        try:
            x = float(item["x"])
            y = float(item["y"])
            width = float(item["w"])
            height = float(item["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or width < _MIN_LABEL_SIDE or height < _MIN_LABEL_SIDE:
            continue
        labels.append(Label(text=text, x=x, y=y, width=width, height=height))
    return tuple(labels)


def dump_labels(labels: tuple[Label, ...] | list[Label]) -> list[dict[str, object]]:
    """Serialise labels for storage next to the picture rectangle."""
    return [
        {
            "t": label.text,
            "x": round(label.x, 1),
            "y": round(label.y, 1),
            "w": round(label.width, 1),
            "h": round(label.height, 1),
        }
        for label in labels
    ]


def _depth_of(parents: np.ndarray, index: int) -> int:
    depth = 0
    parent = int(parents[index][3])
    seen = 0
    while parent != -1 and seen <= _MAX_RINGS:
        depth += 1
        parent = int(parents[parent][3])
        seen += 1
    return depth


_font_cache: dict[str, str | None] = {}


def cjk_font_path() -> str | None:
    """First installed font able to print Chinese, or None when there is none."""
    if "path" not in _font_cache:
        _font_cache["path"] = next(
            (path for path in _CJK_FONT_CANDIDATES if Path(path).exists()), None
        )
    return _font_cache["path"]
