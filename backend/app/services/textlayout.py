"""Turn OCR line boxes into readable question text.

Why this module exists
----------------------
Sorting OCR boxes by ``(y0, x0)`` is wrong for real worksheets, and the stored
text showed three concrete failures:

1. A question number is often detected as its own box whose ``y0`` is a few pixels
   *below* the question text on the same visual line, e.g. text at ``y=498`` and
   ``'7.'`` at ``y=502``. Sorting by ``y0`` puts the text first, so question 7's
   opening line was absorbed into question 6.
2. Table cells are laid out in columns. ``(y0, x0)`` ordering interleaves rows and
   produces nonsense such as ``气体压缩储存于`` / ``压强增大，气体分`` /
   ``子变小`` / ``钢瓶中`` instead of the two real cells.
3. Long cell text wraps, so one logical sentence arrives as several boxes and the
   naive join inserts a newline mid-sentence.

The fix is to reason in terms of *visual rows* and *table columns*:

* cluster boxes into rows by vertical overlap (not by exact ``y0``),
* order boxes inside a row by ``x0``,
* when several rows share a column structure, rebuild cells per column and merge
  a continuation row into the row above it,
* join wrapped fragments inside one cell without a separator.

Content is preserved verbatim; only whitespace and line breaks are rearranged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence


class Box(Protocol):
    """Anything with an axis-aligned box and text (OcrLine satisfies this)."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str


# Two boxes are on the same visual row when their vertical spans overlap by at
# least this fraction of the shorter one. Question numbers sit on the same line as
# their text but are smaller, so exact y0 equality would never match.
ROW_OVERLAP = 0.45

# Two boxes belong to one visual row when their vertical centres are within this
# fraction of the shorter box's height. Clustering by centre distance instead of
# overlap fraction is what keeps a tall banner heading from swallowing the first
# question line underneath it.
CENTRE_TOLERANCE = 0.5

# Boxes closer than this (relative to median text height) are horizontally
# adjacent within one cell rather than separate cells.
CELL_GAP_RATIO = 0.45

# Rough width of a full-width CJK glyph, used to convert px gaps into characters.
_CJK_WIDTH_HINT = 0.9


@dataclass
class Row:
    lines: list[Box]

    @property
    def y0(self) -> int:
        return min(line.y0 for line in self.lines)

    @property
    def y1(self) -> int:
        return max(line.y1 for line in self.lines)

    @property
    def x0(self) -> int:
        return min(line.x0 for line in self.lines)

    def text(self) -> str:
        return "".join(line.text.strip() for line in self.lines)


def _median(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _overlap_ratio(a: Box, b: Box) -> float:
    top = max(a.y0, b.y0)
    bottom = min(a.y1, b.y1)
    overlap = bottom - top
    if overlap <= 0:
        return 0.0
    shorter = min(a.y1 - a.y0, b.y1 - b.y0)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def cluster_rows(lines: Sequence[Box]) -> list[Row]:
    """Group boxes into visual rows, each ordered left to right."""
    rows: list[Row] = []
    for line in sorted(lines, key=lambda item: (item.y0, item.x0)):
        placed = False
        for row in rows:
            # Compare against the row's vertical centre so tall and short boxes on
            # the same line still match.
            probe: Box = row.lines[0]
            shorter = max(1, min(line.y1 - line.y0, probe.y1 - probe.y0))
            centre = (line.y0 + line.y1) / 2.0
            row_centre = (row.y0 + row.y1) / 2.0
            # Overlap alone is not enough: a 57px banner heading overlaps the 46px
            # question line below it by 52% of the shorter box, which used to glue
            # the section title onto question 1. Requiring the centres to be close
            # relative to the smaller box keeps true same-line boxes together and
            # pushes stacked lines apart.
            if abs(centre - row_centre) <= shorter * CENTRE_TOLERANCE:
                row.lines.append(line)
                placed = True
                break
        if not placed:
            rows.append(Row([line]))

    for row in rows:
        row.lines.sort(key=lambda item: item.x0)
    rows.sort(key=lambda item: row.y0)
    return rows


# OCR frequently splits a sentence right at its punctuation, so the same mark can
# arrive twice: '6.' + '.（跨学科...'. Collapse only *identical* marks, and only
# the sentence-level ones, so genuine content like '（1）' or '3.14' is untouched.
_DUPLICATED_PUNCT = (".", "。", "，", "、", "；", "：", "！", "？", ",", ";")


def _collapse_duplicated_punctuation(text: str) -> str:
    for mark in _DUPLICATED_PUNCT:
        doubled = mark * 2
        while doubled in text:
            text = text.replace(doubled, mark)
    return text


def _join_fragments(cells: Sequence[Box], gap_limit: float) -> str:
    """Join boxes on one row into text, inserting spaces only for real gaps."""
    parts: list[str] = []
    previous: Box | None = None
    for cell in cells:
        text = cell.text.strip()
        if not text:
            continue
        if previous is not None:
            gap = cell.x0 - previous.x1
            if gap > gap_limit:
                parts.append(" ")
        parts.append(text)
        previous = cell
    return "".join(parts)


def _split_cells(row: Row, gap_limit: float) -> list[str]:
    """Split one visual row into cell texts at gaps wider than ``gap_limit``."""
    groups: list[list[Box]] = []
    for box in row.lines:
        if not box.text.strip():
            continue
        if groups and (box.x0 - max(item.x1 for item in groups[-1])) <= gap_limit:
            groups[-1].append(box)
        else:
            groups.append([box])

    cells: list[str] = []
    for group in groups:
        value = "".join(item.text.strip() for item in group)
        if value:
            cells.append(value)
    return cells


def _row_join(row: Row, gap_limit: float) -> str:
    """Join every box on one row in reading order, keeping wide gaps as spaces."""
    parts: list[str] = []
    previous: Box | None = None
    for box in row.lines:
        value = box.text.strip()
        if not value:
            continue
        if previous is not None and (box.x0 - previous.x1) > gap_limit:
            parts.append(" ")
        parts.append(value)
        previous = box
    return "".join(parts).strip()


def _is_question_start(row: Row, pattern) -> bool:  # noqa: ANN001
    """True when this row begins a new question number."""
    for box in row.lines:
        if pattern.match(box.text.strip()):
            return True
    return False


def _display_width(text: str) -> int:
    """Approximate terminal/monospace width: CJK glyphs count double."""
    width = 0
    for char in text:
        width += 2 if ord(char) > 0x2E7F else 1
    return width


def _render_grid(grid: Sequence[Sequence[str]]) -> str:
    """Render a table as aligned pipe-separated rows."""
    if not grid:
        return ""
    width = max(len(cells) for cells in grid)
    padded = [[cell or "" for cell in cells] + [""] * (width - len(cells)) for cells in grid]

    column_widths = [0] * width
    for cells in padded:
        for index, cell in enumerate(cells):
            column_widths[index] = max(column_widths[index], _display_width(cell))

    out: list[str] = []
    for cells in padded:
        rendered: list[str] = []
        for index, cell in enumerate(cells):
            fill = " " * (column_widths[index] - _display_width(cell))
            rendered.append(cell + fill)
        line = " | ".join(rendered).rstrip()
        if line.replace("|", "").strip():
            out.append(line)
    return "\n".join(
        _collapse_duplicated_punctuation(line) for line in out
    )


_SECTION_HEADER = re.compile(r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．]")

# A bare page number such as the folio '13' printed in the margin.
_FOLIO = re.compile(r"^\d{1,4}$")


def _is_section_header(row: Row) -> bool:
    """True for a section banner such as '二、非选择题（本大题包括5个小题…'."""
    joined = row.text()
    if not joined:
        return False
    # Banner headings are one long sentence that starts with a Chinese numeral;
    # a normal question line is either numbered or far longer.
    return bool(_SECTION_HEADER.match(joined)) and len(joined) <= 60


def _is_folio(row: Row) -> bool:
    """True for a bare page-number row printed in the margin."""
    joined = row.text()
    return bool(_FOLIO.match(joined))


def _render_cells(cells: Sequence[str]) -> str:
    """Join one multi-cell row, keeping the column boundaries visible.

    A true grid was attempted and abandoned: continuation rows could not be matched
    reliably and the guesswork reordered content. Pipes preserve every character and
    still show which text sat in which column.
    """
    return " | ".join(cells)


# Chinese text has no word spaces, so a wrapped line can be rejoined with nothing
# between the fragments. These are the marks that genuinely end a line of prose.
_LINE_END = "。！？；：）)】”」』…"

# A row that opens a new list item or a new question always keeps its own line.
_LIST_START = re.compile(
    r"^\s*(?:"
    r"[A-Fa-f][.、．]"      # A.  B、
    r"|[A-Fa-f]$"           # a bare option letter in a table
    r"|[(（]\d{1,2}[)）]"     # (1) （2）
    r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]"          # circled numbering
    r"|\d{1,3}[.、．]"       # 1.  2、
    r"|(?:答案|解析|点评|分析|解)\s*[:：]"
    r")"
)

# Prose lines shorter than this are labels inside a figure, not wrapped text.
_MIN_WRAP_WIDTH = 8


def _reflow(pieces: list[str]) -> list[str]:
    """Join wrapped continuation lines onto the paragraph they belong to."""
    out: list[str] = []
    for piece in pieces:
        if not out:
            out.append(piece)
            continue
        previous = out[-1]
        previous_cells = previous.count(" | ")
        current_cells = piece.count(" | ")
        joinable = (
            current_cells == 0
            and previous_cells == 0
            and len(previous) >= _MIN_WRAP_WIDTH
            and len(piece) >= 2
            and previous[-1] not in _LINE_END
            and not _LIST_START.match(piece)
        )
        if joinable:
            out[-1] = previous + piece
        else:
            out.append(piece)
    return out


def build_question_text(lines: Sequence[Box], question_start_pattern) -> str:  # noqa: ANN001
    """Assemble readable text for one question from its OCR boxes.

    Breaks are anchored on question numbers rather than on raw row order. The column
    preamble, section banners (plus their wrapped continuation line) and the page
    folio are dropped: they belong to no question and used to leak into the stored
    text.
    """
    if not lines:
        return ""

    rows = cluster_rows(lines)
    if not rows:
        return ""

    heights = [line.y1 - line.y0 for line in lines if line.y1 > line.y0]
    median_height = _median(heights) or 20.0
    gap_limit = median_height * CELL_GAP_RATIO
    banner_limit = median_height * 0.6

    blocks: list[list[Row]] = []
    current: list[Row] | None = None
    banner_x: int | None = None
    banner_rows = 0

    for row in rows:
        if _is_section_header(row):
            banner_x = row.x0
            banner_rows = 0
            continue
        if banner_x is not None:
            # A banner usually wraps onto a second line that starts at the same
            # left margin. Stop as soon as a row is indented or numbers a question.
            indented = abs(row.x0 - banner_x) > banner_limit
            if indented or banner_rows >= 2 or _is_question_start(
                row, question_start_pattern
            ):
                banner_x = None
            else:
                banner_rows += 1
                continue
        if _is_question_start(row, question_start_pattern):
            current = [row]
            blocks.append(current)
        elif current is not None:
            current.append(row)
        else:
            current = [row]
            blocks.append(current)

    numbered = [
        block
        for block in blocks
        if any(_is_question_start(row, question_start_pattern) for row in block)
    ]
    if numbered:
        blocks = numbered

    out: list[str] = []
    for block in blocks:
        if len(block) > 1 and _is_folio(block[-1]):
            block = block[:-1]
        pieces: list[str] = []
        for row in block:
            cells = _split_cells(row, gap_limit)
            if len(cells) >= 2:
                # Multi-cell row: render the cells row-locally. Rebuilding a true
                # grid was tried and scrambled content when a continuation row could
                # not be matched reliably.
                value = _render_cells(cells)
            else:
                value = _row_join(row, gap_limit)
            value = _collapse_duplicated_punctuation(value)
            if value:
                pieces.append(value)
        if pieces:
            out.append("\n".join(_reflow(pieces)))
    return "\n".join(out)
