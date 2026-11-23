"""Lift the *pictures* out of a question instead of screenshotting the question.

Why not keep a screenshot of the whole question
-----------------------------------------------
A mistake book has to be re-typeset and printed in bulk. A screenshot of the whole
question is a picture of text: it cannot be reflowed, it cannot be searched, and it
prints at whatever resolution the photo happened to have. So the question stays
**text** (``Question.text``), and only the parts that can never be reproduced as
text - apparatus diagrams, circuits, plotted curves, chemistry setups - travel as
pictures.

How a picture is found
----------------------
OCR already tells us where the *text* is. Whatever ink is left after blanking every
recognised text line is, by definition, something OCR could not read: a drawing, a
table frame, or a glyph the recogniser happened to miss. That leftover ink is
grouped into connected clusters and then filtered:

* a cluster that sits inside a single text row and is no bigger than a glyph is a
  character OCR missed (a bracket, a stray mark) - dropped;
* a thin rule inside a text row is a fraction bar or an underline - dropped;
* a cluster whose ink is *mostly* recognised text is a ruled table: the frame
  survives the text blanking, but the words inside it are text and belong in the
  reflowed question - dropped;
* a cluster that spans several text rows, or is far larger than the line height, is
  a drawing - kept, then grown to swallow the labels printed *inside* the drawing so
  those words are not printed twice (once as text, once inside the picture).

The words swallowed that way are remembered on the picture (``FigureBox.labels``).
They are not just deleted from the text: a printed drawing has to carry its own
labels (``浓氨水``, ``酚酞溶液``), so the export redraws them at the position and size
they had in the paper. See ``services/redraw.py``.

Coordinates are always in the page's own pixel space, which is what the question
crop uses as well.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from .ocr import _ink_threshold, _red_mask

# Pictures are shown and printed next to questions, so they share the 1.6x upscale
# that keeps question crops crisp on a high-DPI screen without inflating files.
FIGURE_SCALE = 1.6

# A drawing needs a margin so strokes are not clipped on the printed page.
FIGURE_PAD = 4

# Below this a region cannot hold a drawing worth printing.
_MIN_REGION_SIDE = 40

# Fractions of the median text line height. Kept together so the tuning is
# readable: a "glyph" is small relative to a line, a "drawing" is not.
_GAP_RATIO = 0.85  # strokes of one drawing are closer than this
_ROW_SLACK_RATIO = 0.30  # OCR boxes are tight; allow slack when matching rows
_MIN_INK_RATIO = 0.30  # a real drawing carries at least this much ink
_GLYPH_MAX_WIDTH_RATIO = 2.6
_GLYPH_MAX_HEIGHT_RATIO = 1.9
_MIN_FIGURE_HEIGHT_RATIO = 1.25

# A ruled table looks like a drawing once its text is blanked, because only the
# frame is left. Two signs together identify it: text covering most of the leftover
# ink, and several lines of body text enclosed by the frame (a table's cells).
# Coverage alone is not enough - a small labelled diagram is also mostly text - and
# that is why the enclosed line count is required too. Rules crossing the box were
# tried as a third sign and rejected: a plain rectangular apparatus has them too.
_TEXT_TABLE_COVERAGE = 0.55
_MIN_TABLE_ROWS = 3

# Question pictures are printed inside the page margins; ink that runs off the
# sheet is letterhead decoration (a swoosh, a watermark bar) and would only add a
# stray graphic to the mistake book.
_PAGE_EDGE_TOLERANCE = 2

# Anything thinner than this in one direction is a rule, not a drawing.
_RULE_MAX_THICKNESS = 4

# A hairline running across this much of the question box is page furniture (the
# column divider of the sheet, the rule between two questions), not a drawing.
_RULE_SPAN_RATIO = 0.90

# One question rarely holds more than a couple of pictures; the cap keeps a
# mis-detected page from producing dozens of tiny crops.
MAX_FIGURES = 4

# Bump when detection changes meaning; stored lists from an older version are
# treated as "not computed yet" instead of being trusted.
#
# 2: pictures carry the labels printed inside them, so the export can redraw them.
FIGURE_SET_VERSION = 2


@dataclass(frozen=True)
class FigureLabel:
    """A word printed *inside* a picture, in the picture's own pixel space.

    Kept with the picture so the export can print the label as real text at the
    place and size it had in the paper, instead of tracing its glyph outlines.
    """

    text: str
    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, object]:
        return {"t": self.text, "x": self.x, "y": self.y, "w": self.width, "h": self.height}

    @classmethod
    def from_dict(cls, raw: object) -> "FigureLabel | None":
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("t") or "").strip()
        try:
            label = cls(
                text=text,
                x=int(raw["x"]),
                y=int(raw["y"]),
                width=int(raw["w"]),
                height=int(raw["h"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not label.text or label.width <= 0 or label.height <= 0:
            return None
        return label


@dataclass(frozen=True)
class FigureBox:
    """One picture's rectangle, in the page's pixel space."""

    x: int
    y: int
    width: int
    height: int
    # Words printed inside the picture. They are stripped from the question text so
    # nothing prints twice, and redrawn with the picture so nothing is lost.
    labels: tuple[FigureLabel, ...] = ()

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }
        if self.labels:
            payload["labels"] = [label.as_dict() for label in self.labels]
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> "FigureBox | None":
        if not isinstance(raw, dict):
            return None
        try:
            box = cls(
                x=int(raw["x"]),
                y=int(raw["y"]),
                width=int(raw["width"]),
                height=int(raw["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if box.width <= 0 or box.height <= 0:
            return None
        labels = tuple(
            label
            for label in (FigureLabel.from_dict(item) for item in raw.get("labels") or ())
            if label is not None
        )
        return box if not labels else FigureBox(box.x, box.y, box.width, box.height, labels)


def detect_figures(
    image: Image.Image,
    box: tuple[int, int, int, int],
    text_lines: Sequence[object] = (),
    *,
    max_figures: int = MAX_FIGURES,
) -> list[FigureBox]:
    """Return the pictures printed inside ``box``, in ``image`` coordinates.

    ``box`` is ``(left, top, right, bottom)``; ``text_lines`` are OCR lines in the
    same coordinate space as ``image`` (anything with ``x0/y0/x1/y1/text``).
    Without recognised text there is no way to tell a drawing from a character, so
    an empty list is returned rather than a guess.
    """
    left, top, right, bottom = _clamp_box(box, image.size)
    if right - left < _MIN_REGION_SIDE or bottom - top < _MIN_REGION_SIDE:
        return []

    local_lines = _local_lines(text_lines, left, top, right, bottom)
    if not local_lines:
        return []

    line_height = _median([line.y1 - line.y0 for line in local_lines])
    if line_height <= 1:
        return []

    crop = image.crop((left, top, right, bottom))
    ink_mask = _ink_mask(crop)
    text_mask = _text_mask(crop.size, local_lines)
    leftover = ImageChops.subtract(ink_mask, text_mask)
    ink = np.asarray(leftover, dtype=np.uint8) > 0
    if not ink.any():
        return []

    rows = _text_rows(local_lines)
    drawing_min_ink = line_height * line_height * _MIN_INK_RATIO

    # Every speck takes part in the merge: the thin strokes that bridge the parts
    # of one drawing are small on their own, so discarding them before merging
    # tears a drawing apart and keeps only its densest fragment. Speck-sized
    # *clusters* are dropped after the merge instead, when they are still alone.
    components = [
        component
        for component in _components(ink)
        if min(component[2], component[3]) > 1
        and not _is_layout_rule(component, ink.shape)
    ]
    clusters = _merge_components(components, gap=int(round(line_height * _GAP_RATIO)))
    ink_binary = np.asarray(ink_mask, dtype=np.uint8) > 0
    text_binary = np.asarray(text_mask, dtype=np.uint8) > 0

    grown: list[FigureBox] = []
    for x, y, width, height, area in clusters:
        if area < drawing_min_ink:
            continue
        if _is_missed_glyph((x, y, width, height), rows, line_height):
            continue
        if _is_text_table((x, y, width, height), ink_binary, text_binary, local_lines, line_height):
            continue
        if _bleeds_off_page((x + left, y + top, width, height), image.size):
            continue
        region = FigureBox(x, y, width, height)
        for _ in range(3):
            region = _swallow_labels(region, local_lines)
        if (
            region.height < line_height * _MIN_FIGURE_HEIGHT_RATIO
            and not _is_wide_drawing(region, line_height)
        ):
            continue
        grown.append(region)

    grown.sort(key=lambda item: item.area, reverse=True)
    figures: list[FigureBox] = []
    for region in grown[:max_figures]:
        figure = FigureBox(
            x=max(0, region.x + left - FIGURE_PAD),
            y=max(0, region.y + top - FIGURE_PAD),
            width=region.width + FIGURE_PAD * 2,
            height=region.height + FIGURE_PAD * 2,
        )
        labels = _labels_inside(figure, local_lines, left, top)
        figures.append(figure if not labels else _with_labels(figure, labels))
    return figures


def _with_labels(figure: FigureBox, labels: tuple[FigureLabel, ...]) -> FigureBox:
    return FigureBox(figure.x, figure.y, figure.width, figure.height, labels)


def _labels_inside(
    figure: FigureBox, lines: Sequence[object], page_left: int, page_top: int
) -> tuple[FigureLabel, ...]:
    """Words printed fully inside ``figure``, in the picture's own pixel space.

    ``lines`` are in crop-local coordinates; ``page_left``/``page_top`` are where
    that crop sits on the page, which is what puts them back into page space before
    being made relative to the picture.
    """
    found: list[FigureLabel] = []
    for line in lines:
        x0 = line.x0 + page_left - figure.x
        y0 = line.y0 + page_top - figure.y
        x1 = line.x1 + page_left - figure.x
        y1 = line.y1 + page_top - figure.y
        content = " ".join(str(line.text or "").split())
        if not content:
            continue
        if not (-2 <= x0 and x1 <= figure.width + 2 and -2 <= y0 and y1 <= figure.height + 2):
            continue
        found.append(
            FigureLabel(
                text=content,
                x=max(0, x0),
                y=max(0, y0),
                width=min(figure.width, x1) - max(0, x0),
                height=min(figure.height, y1) - max(0, y0),
            )
        )
    return tuple(label for label in found if label.width > 0 and label.height > 0)


def detect_page_figures(
    page_data: bytes,
    regions: Sequence[object],
    text_lines: Sequence[object],
) -> dict[int, tuple[list[FigureBox], str]]:
    """Map ``region_order`` -> (pictures, question text with labels removed).

    ``regions`` is the analysed region list (``x/y/width/height/region_order/
    text``); ``text_lines`` are the page's OCR lines. The picture rectangles are
    returned in page pixel space so they can be stored and cropped later.
    """
    if not text_lines or not regions:
        return {}

    with Image.open(io.BytesIO(page_data)) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.load()

        result: dict[int, tuple[list[FigureBox], str]] = {}
        for region in regions:
            left = int(getattr(region, "x"))
            top = int(getattr(region, "y"))
            width = int(getattr(region, "width"))
            height = int(getattr(region, "height"))
            text = str(getattr(region, "text", "") or "")
            order = int(getattr(region, "region_order"))
            figures = detect_figures(image, (left, top, left + width, top + height), text_lines)
            result[order] = (figures, strip_figure_labels(text, text_lines, figures))
        return result


def strip_figure_labels(
    text: str, text_lines: Sequence[object], boxes: Sequence[FigureBox]
) -> str:
    """Remove words that are printed *inside* a picture from the question text.

    Those words were recognised as text but belong to the drawing (``浓氨水``,
    ``酚酞溶液`` on a labelled apparatus). Keeping them in the question text would
    print them twice: once as body text, once inside the picture. Only short lines
    fully enclosed by a picture are removed, so body text that merely touches a
    picture is never lost.

    A label is removed only where it stands alone. The same word usually appears
    inside the answers (``瓶中浓氨水变蓝``), and a blanket substitution would eat
    it there, leaving ``瓶中变蓝`` — a question that no longer makes sense.
    """
    if not text or not boxes:
        return text

    removals: list[str] = []
    for line in text_lines:
        content = str(getattr(line, "text", "") or "").strip()
        if not content or len(content) > 24:
            continue
        x0 = int(getattr(line, "x0"))
        y0 = int(getattr(line, "y0"))
        x1 = int(getattr(line, "x1"))
        y1 = int(getattr(line, "y1"))
        if any(
            box.x - 2 <= x0 and x1 <= box.right + 2 and box.y - 2 <= y0 and y1 <= box.bottom + 2
            for box in boxes
        ):
            removals.append(content)

    # Single characters are never stripped: one-pixel noise inside a diagram is
    # OCR'd as a stray character, and deleting every "上" in the question to get
    # rid of it costs far more than it saves.
    patterns = [
        re.compile(r"(?<!\S)" + re.escape(fragment) + r"(?!\S)")
        for fragment in {_collapse(fragment) for fragment in removals}
        if len(fragment.strip()) > 1
    ]
    if not patterns:
        return text

    cleaned_rows: list[str] = []
    for row in text.splitlines():
        original = row
        for pattern in patterns:
            row = pattern.sub("", row)
        if row != original:
            row = _trim_dangling_columns(row)
            row = _collapse(row)
            if not row.strip(" ，。、；：:（）()"):
                continue
        cleaned_rows.append(row)
    cleaned = "\n".join(cleaned_rows)
    return cleaned if cleaned.strip() else text


def _trim_dangling_columns(row: str) -> str:
    """Drop column separators that no longer have a cell on both sides."""
    if "|" not in row:
        return row
    cells = [cell.strip() for cell in row.split("|")]
    kept = [cell for cell in cells if cell]
    if len(kept) <= 1:
        # Both sides were labels inside the picture: the row is now one cell, or
        # nothing at all - and an empty result is dropped by the caller.
        return kept[0] if kept else ""
    return " | ".join(kept)


def _collapse(value: str) -> str:
    """Squeeze runs of whitespace and the gaps they leave before punctuation."""
    collapsed = " ".join(value.split())
    for mark in ("，", "。", "、", "；", "：", "（", "）"):
        collapsed = collapsed.replace(f" {mark}", mark)
    return collapsed


def crop_figure(page_data: bytes, box: FigureBox, page_width: int, page_height: int) -> bytes:
    """Return PNG bytes for one picture cut out of the stored page image."""
    with Image.open(io.BytesIO(page_data)) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        width, height = image.size
        if width <= 0 or height <= 0:
            width, height = page_width, page_height

        left, top, right, bottom = _clamp_box((box.x, box.y, box.right, box.bottom), (width, height))
        if right - left < 8 or bottom - top < 8:
            raise ValueError("figure too small to crop")

        crop = image.crop((left, top, right, bottom))
        if FIGURE_SCALE != 1.0:
            crop = crop.resize(
                (max(1, int(crop.width * FIGURE_SCALE)), max(1, int(crop.height * FIGURE_SCALE))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        crop.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def figure_key(user_id: int, question_id: int, index: int) -> str:
    """Stable storage key so re-committing overwrites instead of duplicating."""
    return f"mistakes/{user_id}/q{question_id}-fig{index}.png"


def store_figures(
    storage,
    *,
    page_data: bytes,
    page_width: int,
    page_height: int,
    boxes: Sequence[FigureBox],
    user_id: int,
    question_id: int,
) -> list[dict[str, object]]:
    """Cut every detected picture out of the page and store it for the mistake.

    The rectangles come from detection at upload time, so nothing is recomputed
    here. A single unreadable page or a bad rectangle never blocks recording the
    mistake: the text is stored either way and that figure is skipped.
    """
    if not boxes:
        return []

    items: list[dict[str, object]] = []
    for index, box in enumerate(boxes):
        try:
            data = crop_figure(page_data, box, page_width, page_height)
        except (ValueError, OSError):
            continue
        key = figure_key(user_id, question_id, index)
        try:
            storage.save(key, data)
        except Exception:  # noqa: BLE001 - StorageError or backend failure
            continue
        with Image.open(io.BytesIO(data)) as drawn:
            width, height = drawn.size
        item: dict[str, object] = {"key": key, "width": width, "height": height}
        if box.labels:
            # Same pixel space as the stored crop, which is the box scaled by
            # FIGURE_SCALE, so the export can place the labels on the picture.
            item["labels"] = [
                {
                    "t": label.text,
                    "x": round(label.x * FIGURE_SCALE, 1),
                    "y": round(label.y * FIGURE_SCALE, 1),
                    "w": round(label.width * FIGURE_SCALE, 1),
                    "h": round(label.height * FIGURE_SCALE, 1),
                }
                for label in box.labels
            ]
        items.append(item)
    return items


def dump_figure_set(items: Sequence[dict[str, object]]) -> str:
    """Serialise a picture list; the version marks detection as having run."""
    return json.dumps(
        {"v": FIGURE_SET_VERSION, "items": list(items)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_figure_set(raw: str | None) -> list[dict[str, object]] | None:
    """Read a stored picture list, or None when detection never ran on that row."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != FIGURE_SET_VERSION:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _clamp_box(
    box: tuple[int, int, int, int], size: tuple[int, int]
) -> tuple[int, int, int, int]:
    width, height = size
    left = max(0, min(int(box[0]), width))
    top = max(0, min(int(box[1]), height))
    right = max(left, min(int(box[2]), width))
    bottom = max(top, min(int(box[3]), height))
    return left, top, right, bottom


def _median(values: Sequence[int]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _ink_mask(image: Image.Image) -> Image.Image:
    """L-mode mask, 255 where there is ink, with red grading marks removed."""
    rgb = image.convert("RGB")
    gray = rgb.convert("L")
    threshold = _ink_threshold(gray)
    ink = gray.point(lambda value: 255 if value < threshold else 0)
    red = _red_mask(rgb).point(lambda value: 255 if value > 60 else 0)
    return ImageChops.subtract(ink, red)


def _local_lines(
    text_lines: Sequence[object], left: int, top: int, right: int, bottom: int
) -> list[object]:
    """Lines whose centre falls inside the box, exposed in box coordinates."""
    local: list[object] = []
    for line in text_lines:
        x0 = int(getattr(line, "x0"))
        y0 = int(getattr(line, "y0"))
        x1 = int(getattr(line, "x1"))
        y1 = int(getattr(line, "y1"))
        if y1 - y0 < 3 or x1 - x0 < 2:
            continue
        if not (left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom):
            continue
        local.append(
            _LocalLine(
                x0=max(0, x0 - left),
                y0=max(0, y0 - top),
                x1=min(right - left, x1 - left),
                y1=min(bottom - top, y1 - top),
                text=str(getattr(line, "text", "") or ""),
                confidence=float(getattr(line, "confidence", 0.0) or 0.0),
            )
        )
    return [line for line in local if line.x1 > line.x0 and line.y1 > line.y0]


@dataclass(frozen=True)
class _LocalLine:
    """A text line translated into the cropped region's coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str
    confidence: float


def _text_mask(size: tuple[int, int], lines: Sequence[object]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for line in lines:
        # One pixel of slack: OCR boxes are tight against the glyphs, so a sliver
        # left outside would look like leftover ink.
        draw.rectangle([line.x0 - 1, line.y0 - 1, line.x1 + 1, line.y1 + 1], fill=255)
    return mask


def _text_rows(lines: Sequence[object]) -> list[tuple[int, int]]:
    """Vertical bands covered by text lines, merged where they overlap."""
    bands: list[list[int]] = []
    for line in sorted(lines, key=lambda item: item.y0):
        bands.append([int(line.y0), int(line.y1)])

    merged = True
    while merged:
        merged = False
        for index in range(len(bands) - 1):
            first, second = bands[index], bands[index + 1]
            if second[0] <= first[1]:
                first[1] = max(first[1], second[1])
                bands.pop(index + 1)
                merged = True
                break
    return [(band[0], band[1]) for band in bands]


def _components(ink: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Connected components (8-connectivity) as ``(x, y, width, height, area)``.

    Row runs plus union-find: a question region holds a few thousand runs at most,
    so this stays fast without pulling in an image-processing dependency.
    """
    height = ink.shape[0]
    runs: list[tuple[int, int, int]] = []
    parent: list[int] = []

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    previous: list[int] = []
    for y in range(height):
        row = ink[y]
        if not row.any():
            previous = []
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        current: list[int] = []
        for start, end in zip(edges[0::2].tolist(), (edges[1::2] - 1).tolist()):
            index = len(runs)
            runs.append((y, int(start), int(end)))
            parent.append(index)
            current.append(index)

        for index in current:
            _, start, end = runs[index]
            for other in previous:
                _, other_start, other_end = runs[other]
                if other_start <= end + 1 and start <= other_end + 1:
                    root_a, root_b = find(other), find(index)
                    if root_a != root_b:
                        parent[root_b] = root_a
        previous = current

    boxes: dict[int, list[int]] = {}
    for index, (y, start, end) in enumerate(runs):
        root = find(index)
        record = boxes.get(root)
        if record is None:
            boxes[root] = [start, y, end, y, end - start + 1]
        else:
            record[0] = min(record[0], start)
            record[1] = min(record[1], y)
            record[2] = max(record[2], end)
            record[3] = max(record[3], y)
            record[4] += end - start + 1

    return [(x0, y0, x1 - x0 + 1, y1 - y0 + 1, area) for x0, y0, x1, y1, area in boxes.values()]


def _merge_components(
    components: Sequence[tuple[int, int, int, int, int]], gap: int
) -> list[tuple[int, int, int, int, int]]:
    """Join components closer than ``gap`` pixels: strokes of one drawing."""
    items = [list(component) for component in components]
    merged = True
    while merged:
        merged = False
        used = [False] * len(items)
        combined: list[list[int]] = []
        for index, item in enumerate(items):
            if used[index]:
                continue
            used[index] = True
            current = list(item)
            growing = True
            while growing:
                growing = False
                for other_index in range(index + 1, len(items)):
                    if used[other_index]:
                        continue
                    if _boxes_touch(current, items[other_index], gap):
                        current = _union(current, items[other_index])
                        used[other_index] = True
                        growing = True
                        merged = True
            combined.append(current)
        items = combined
    return [(item[0], item[1], item[2], item[3], item[4]) for item in items]


def _boxes_touch(first: Sequence[int], second: Sequence[int], gap: int) -> bool:
    return (
        first[0] <= second[0] + second[2] + gap
        and second[0] <= first[0] + first[2] + gap
        and first[1] <= second[1] + second[3] + gap
        and second[1] <= first[1] + first[3] + gap
    )


def _union(first: Sequence[int], second: Sequence[int]) -> list[int]:
    x0 = min(first[0], second[0])
    y0 = min(first[1], second[1])
    x1 = max(first[0] + first[2], second[0] + second[2])
    y1 = max(first[1] + first[3], second[1] + second[3])
    return [x0, y0, x1 - x0, y1 - y0, first[4] + second[4]]


def _is_layout_rule(
    component: Sequence[int], region_shape: tuple[int, int]
) -> bool:
    """True when a component is a printed page rule, not part of a drawing.

    A worksheet often has a hairline that runs the full height of the question box
    (the divider between two columns) or the full width (the rule above a question).
    It is thin, straight and spans the box, which makes it a perfect bridge: every
    drawing near it would be merged into one box that also contains the rule and
    every label along it. Dropping the rule keeps each drawing on its own.

    ``region_shape`` is ``(height, width)`` of the question box in pixels.
    """
    x, y, width, height, _area = component
    del x, y
    region_height, region_width = region_shape
    if width <= _RULE_MAX_THICKNESS and height >= region_height * _RULE_SPAN_RATIO:
        return True  # vertical divider
    if height <= _RULE_MAX_THICKNESS and width >= region_width * _RULE_SPAN_RATIO:
        return True  # horizontal rule
    return False


def _is_missed_glyph(
    cluster: Sequence[int], rows: Sequence[tuple[int, int]], line_height: float
) -> bool:
    """True when a cluster is a character (or rule) OCR did not recognise."""
    _x, y, width, height = cluster[0], cluster[1], cluster[2], cluster[3]
    slack = line_height * _ROW_SLACK_RATIO
    for row_top, row_bottom in rows:
        if not (y >= row_top - slack and y + height <= row_bottom + slack):
            continue
        if min(width, height) <= _RULE_MAX_THICKNESS:
            # Fraction bar, underline or table rule drawn on the text line.
            return True
        if (
            width <= line_height * _GLYPH_MAX_WIDTH_RATIO
            and height <= line_height * _GLYPH_MAX_HEIGHT_RATIO
        ):
            return True
    return False


def _is_text_table(
    cluster: Sequence[int],
    ink: np.ndarray,
    text: np.ndarray,
    lines: Sequence[object],
    line_height: float,
) -> bool:
    """True when a cluster is a ruled table frame around recognised text.

    A table's borders are not text, so blanking the OCR lines leaves the frame
    behind and it looks like a drawing. But the rows and columns *inside* that
    frame are ordinary body text that must stay in the reflowed question, so the
    frame is only a leftover artefact and the cluster is not a picture.

    Two signs are required together. Coverage alone misfires on a small diagram
    whose labels dominate its ink, and a rectilinear frame alone misfires on a
    plain rectangular apparatus. What no diagram has is *several lines of text*
    sitting wholly inside the box: that is a table's cells, one row per line.
    """
    x, y, width, height = cluster[0], cluster[1], cluster[2], cluster[3]
    if width <= 0 or height <= 0:
        return False
    rows = slice(max(0, y), max(0, y) + height)
    columns = slice(max(0, x), max(0, x) + width)
    ink_window = ink[rows, columns]
    text_window = text[rows, columns]
    if ink_window.size == 0:
        return False
    # Coverage is measured against the ink, not the box: an empty drawing area
    # must not score as "text" just because the box is large.
    inked = int(ink_window.sum())
    if inked <= 0:
        return False
    covered = int((ink_window & text_window).sum())
    if covered / inked < _TEXT_TABLE_COVERAGE:
        return False
    return len(_enclosed_rows(cluster, lines, line_height)) >= _MIN_TABLE_ROWS


def _enclosed_rows(
    cluster: Sequence[int], lines: Sequence[object], line_height: float
) -> list[int]:
    """Row indices of text lines printed wholly inside ``cluster``.

    ``line_height`` only sets the tolerance: an OCR box is a little tighter than
    the printed glyphs, so a cell's text can poke a few pixels past the rule.
    """
    x, y, width, height = cluster[0], cluster[1], cluster[2], cluster[3]
    slack = max(2.0, line_height * _ROW_SLACK_RATIO)
    found = set()
    for index, line in enumerate(lines):
        if (
            x - slack <= line.x0
            and line.x1 <= x + width + slack
            and y - slack <= line.y0
            and line.y1 <= y + height + slack
        ):
            found.add(index)
    return sorted(found)


def _bleeds_off_page(
    cluster: Sequence[int], page_size: tuple[int, int]
) -> bool:
    """True when a cluster's ink runs to the sheet edge.

    A question picture is placed inside the printable area. Ink that reaches the
    paper edge belongs to letterhead decoration, and cropping it would put a strip
    of ornament into the mistake book.
    """
    x, y, width, height = cluster[0], cluster[1], cluster[2], cluster[3]
    page_width, page_height = page_size
    return (
        x <= _PAGE_EDGE_TOLERANCE
        or y <= _PAGE_EDGE_TOLERANCE
        or x + width >= page_width - _PAGE_EDGE_TOLERANCE
        or y + height >= page_height - _PAGE_EDGE_TOLERANCE
    )


def _is_wide_drawing(region: FigureBox, line_height: float) -> bool:
    """A drawing may be short (a plotted curve) if it is clearly wider than text."""
    return (
        region.width >= line_height * _GLYPH_MAX_WIDTH_RATIO * 1.5
        and region.height >= line_height * 0.8
    )


def _swallow_labels(region: FigureBox, lines: Sequence[object]) -> FigureBox:
    """Grow ``region`` over the text lines printed inside it."""
    left, top, right, bottom = region.x, region.y, region.right, region.bottom
    for line in lines:
        middle_x = (line.x0 + line.x1) / 2
        middle_y = (line.y0 + line.y1) / 2
        if not (left - 2 <= middle_x <= right + 2 and top - 2 <= middle_y <= bottom + 2):
            continue
        # Only lines mostly inside the picture are labels; a line running past the
        # edge is body text sharing the row, and must not drag the box outwards.
        overlap = max(0, min(right, line.x1) - max(left, line.x0))
        inside = line.x0 >= left - 2 and line.x1 <= right + 2
        if not inside and overlap < (line.x1 - line.x0) * 0.6:
            continue
        left = min(left, line.x0)
        top = min(top, line.y0)
        right = max(right, line.x1)
        bottom = max(bottom, line.y1)
    return FigureBox(x=left, y=top, width=right - left, height=bottom - top)


def tidy_question_text(text: str) -> str:
    """Remove table separators left dangling at the ends of a row.

    The text layout joins table cells with ``" | "``. When a label printed *inside*
    a picture was removed from one end of such a row, the separator stayed behind
    and printed as a stray vertical bar (``A.瓶中浓氨水变蓝 |``).

    Only the *edges* are touched. Interior separators belong to real cells, and a
    maths expression such as ``|x|`` carries no spaces around its bars, so it is
    never a ``" | "`` row and is left alone.
    """
    if "|" not in text:
        return text

    changed = False
    rows: list[str] = []
    for row in text.splitlines():
        if "|" in row:
            trimmed = row.strip()
            while trimmed.startswith("|"):
                trimmed = trimmed[1:].lstrip()
            while trimmed.endswith("|"):
                trimmed = trimmed[:-1].rstrip()
            if trimmed != row:
                changed = True
            if not trimmed.strip(" ，。、；：:（）()"):
                # The row was nothing but labels inside the picture.
                continue
            rows.append(trimmed)
        else:
            rows.append(row)
    return "\n".join(rows) if changed else text
