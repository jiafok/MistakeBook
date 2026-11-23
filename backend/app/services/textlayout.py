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
            if gap > gap_limit or _option_break(previous, cell):
                parts.append(" ")
        parts.append(text)
        previous = cell
    return "".join(parts)


def _cell_text(group: Sequence[Box]) -> str:
    """Join one cell's boxes, keeping an option letter apart from its formula.

    OCR often drops the dot after an option letter ('B' instead of 'B.'), and gluing
    it straight onto the formula printed 'B12n/m'. A space keeps the option readable
    without inventing punctuation that is not on the page.
    """
    head = group[0].text.strip()
    if len(group) > 1 and _OPTION_LETTER.match(head):
        rest = "".join(item.text.strip() for item in group[1:])
        return (head if head[-1] in ".、．" else head + " ") + rest
    return "".join(item.text.strip() for item in group)


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
        value = _cell_text(group)
        if value.strip():
            cells.append(value)
    return cells


def _has_options(row: Row) -> bool:
    """True when this row lays several answer options out side by side."""
    return sum(1 for box in row.lines if _OPTION_LETTER.match(box.text.strip())) >= 2


def _split_options(row: Row, gap_limit: float) -> list[str]:
    """Split an option row so every option keeps its own formula.

    Options are printed in columns, and on a maths paper the formula follows its
    letter immediately: 'A.' then a fraction, 'B' then another fraction. Splitting on
    wide gaps alone tore 'B' away from its own fraction and glued it to the previous
    one. Here a cell always starts at an option marker, and an extra split is forced
    at a gutter wide enough to be a column boundary.
    """
    gutter = gap_limit * 3
    groups: list[list[Box]] = []
    previous: Box | None = None
    for box in row.lines:
        text = box.text.strip()
        if not text:
            continue
        starts_option = bool(_OPTION_LETTER.match(text))
        gap = box.x0 - previous.x1 if previous is not None else 0
        if previous is None or starts_option or gap > gutter:
            groups.append([box])
        else:
            groups[-1].append(box)
        previous = box

    cells: list[str] = []
    for group in groups:
        value = _cell_text(group)
        if value.strip():
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
        if previous is not None and (
            (box.x0 - previous.x1) > gap_limit or _option_break(previous, box)
        ):
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

# --- stacked maths -----------------------------------------------------------
# A worksheet prints fractions as a numerator above a denominator and scripts as a
# raised or lowered glyph. OCR reports those as separate boxes, so the naive row
# join produced "12m | 12n | ... / n" instead of "12m/n". Rebuilding the stack from
# geometry is what makes the formula survive into the stored text.
#
# Two boxes are stacked when their horizontal spans overlap by at least this much
# of the narrower one...
_STACK_X_OVERLAP = 0.60

# ...and they are vertically separated, but by no more than this multiple of the
# taller box's height. A bigger gap is the space between two printed lines.
_STACK_MAX_GAP_RATIO = 0.55

# A script is noticeably smaller than the text it belongs to.
_SCRIPT_HEIGHT_RATIO = 0.85

# A fraction's parts are short: "12m", "n", "2a+b". A wrapped line of prose also sits
# above another one with a small gap, so without this limit whole sentences were
# merged into fake fractions and the question text came out duplicated and mangled.
_MAX_FRACTION_PART = 8

# A fraction part never contains these: a comma, a full stop, a colon or a Chinese
# sentence mark means the box is prose.
_PROSE_MARK = re.compile(r"[，。、；：！？,;:!?（）()]")

# A fraction part is also narrow. A wrapped line of prose spans the whole column
# (hundreds of pixels), so this single check rejects every prose pair: a sentence
# sitting above the next sentence is ordinary text, never a fraction.
_MAX_FRACTION_WIDTH = 130

# A stack of real fraction parts can be a touch further apart than the gap between
# two printed lines, because the numerator and denominator are separated by the
# missing bar. Only used once the width guard has already excluded prose.
_STACK_MAX_GAP_RATIO_NARROW = 0.70

# A single option marker: 'A.' 'B、' 'C' 'D．'. On a maths paper the options are laid
# out side by side, so each marker starts its own option.
_OPTION_LETTER = re.compile(r"^[A-Fa-f]\s*[.、．]?$")

# A box this short sitting *inside* a rebuilt fraction is OCR noise from the bar or
# from a bundled glyph, not a third part of the formula.
_STACK_NOISE_MAX_CHARS = 2

# Stray glyphs sit *over* the stack. An option marker printed to the left overlaps
# the stack by nothing, so this threshold is what keeps 'A.' intact.
_STACK_NOISE_X_OVERLAP = 0.30


# A script attaches only to a formula. The base must end in a Latin letter, a digit or
# a closing bracket: 'm', 'x', 'H2O', '(x+1)'. Chinese prose ends in a word, and
# treating the next glyph as a superscript turned '最小粒子（' into '最小粒子⁽'.
_SCRIPT_BASE = re.compile(r"[A-Za-z0-9)⁾]\.?$")

# Unicode so the reconstructed formula prints as glyphs rather than as markup.
_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


@dataclass
class _MergedBox:
    """A synthetic box standing in for a stack of OCR boxes."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str


def _x_overlap_ratio(a: Box, b: Box) -> float:
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    if overlap <= 0:
        return 0.0
    narrower = min(a.x1 - a.x0, b.x1 - b.x0)
    return overlap / narrower if narrower > 0 else 0.0


def _is_narrow(box: Box) -> bool:
    """True when a box is small enough to be one part of a formula."""
    return (box.x1 - box.x0) <= _MAX_FRACTION_WIDTH


def _option_break(previous: Box, box: Box) -> bool:
    """True when ``box`` starts a new option, so it must not be glued on.

    Options are laid out side by side, so a wide gap alone is not a reliable
    separator: on a maths paper 'A.' sits immediately left of its fraction while
    'B' sits immediately right of a different one. Breaking before every option
    marker keeps each option's own formula paired with its own letter.
    """
    if not _OPTION_LETTER.match((box.text or "").strip()):
        return False
    return (box.x0 - previous.x1) > 0


def _is_stack_noise(box: Box, stack: Box) -> bool:
    """True when ``box`` is a stray glyph swallowed by a rebuilt fraction.

    OCR sometimes reads the missing fraction bar, or the tail of a glyph, as its own
    tiny box sitting *between* the two real parts, e.g. ``H`` beside ``12n`` over
    ``m``. A genuine third part of a two-level fraction never sits one or two
    characters wide *inside* the stack: anything that does is the bar or a split
    glyph. The check is deliberately narrow - short text, fully inside the stack
    vertically, and centred within it horizontally - so real content survives.
    """
    text = (box.text or "").strip()
    if not text or len(text) > _STACK_NOISE_MAX_CHARS:
        return False
    if box.y0 < stack.y0 or box.y1 > stack.y1:
        return False
    # It must actually sit over the stack, not merely beside it: 'A.' printed to the
    # left of the first fraction overlaps the stack in no way, and swallowing it
    # deleted a real option marker.
    if _x_overlap_ratio(box, stack) < _STACK_NOISE_X_OVERLAP:
        return False
    centre = (box.x0 + box.x1) / 2.0
    slack = box.x1 - box.x0  # allow the box to peek out by its own width
    return (stack.x0 - slack) <= centre <= (stack.x1 + slack)


def _stack_partner(
    box: Box, candidates: Sequence[Box], max_gap_ratio: float = _STACK_MAX_GAP_RATIO
) -> Box | None:
    """The box printed directly above or below ``box``; if any."""
    best: Box | None = None
    best_gap = 0
    for other in candidates:
        if other is box:
            continue
        if _x_overlap_ratio(box, other) < _STACK_X_OVERLAP:
            continue
        # Which one is on top?
        upper, lower = (box, other) if box.y0 <= other.y0 else (other, box)
        gap = lower.y0 - upper.y1
        if gap < 0:
            continue  # they share a baseline: same line, not a stack
        tallest = max(upper.y1 - upper.y0, lower.y1 - lower.y0)
        if tallest <= 0 or gap > tallest * max_gap_ratio:
            continue
        if best is None or gap < best_gap:
            best, best_gap = other, gap
    return best


def _script_of(base: Box, candidates: Sequence[Box]) -> tuple[str, Box] | None:
    """A raised or lowered glyph next to ``base``, with its script kind."""
    base_height = base.y1 - base.y0
    if base_height <= 0:
        return None
    base_text = (base.text or "").strip()
    # A script attaches to a formula, so the base must end in a maths character:
    # 'm²', 'x²', 'H₂O'. Chinese prose ends in a word, and treating the next glyph as
    # its superscript turned '最小粒子（C' into '最小粒子⁽C'.
    if not base_text or not _SCRIPT_BASE.match(base_text):
        return None
    for other in candidates:
        if other is base:
            continue
        height = other.y1 - other.y0
        if height <= 0 or height > base_height * _SCRIPT_HEIGHT_RATIO:
            continue
        # Horizontally adjacent, not overlapping: a script follows its base.
        gap = other.x0 - base.x1
        if gap < -2 or gap > base_height * 0.55:
            continue
        # Raised: the script's centre sits in the upper part of the base.
        centre = (other.y0 + other.y1) / 2.0
        if centre < base.y0 + base_height * 0.45:
            return ("super", other)
        if centre > base.y0 + base_height * 0.55:
            return ("sub", other)
    return None


def rebuild_stacked_math(lines: Sequence[Box]) -> list[Box]:
    """Replace stacked OCR boxes with one synthetic box holding real notation.

    A fraction prints on two levels with no rule between them in this paper's
    typesetting, and ``m²`` arrives as a separate small box. Joining those by row
    destroyed the formula (``12m | 12n`` on one line, ``n`` on the next), so the
    stack is rebuilt here before rows are formed.

    Only a high-confidence stack is rewritten: the two parts must sit right above
    one another and be close enough to belong to one expression. Everything else is
    passed through untouched, because a wrong merge corrupts prose as well.
    """
    remaining = list(lines)
    merged: list[_MergedBox] = []
    consumed: set[int] = set()

    for index, line in enumerate(lines):
        if id(line) in consumed:
            continue
        text = (line.text or "").strip()
        if not text:
            continue

        # 1. A fraction: this box sits directly above its denominator. Both parts must
        # be short and narrow, otherwise a sentence sitting above the next sentence
        # would be merged and the question body would come out duplicated.
        partner = None
        if _is_narrow(line):
            partner = _stack_partner(
                line, remaining, max_gap_ratio=_STACK_MAX_GAP_RATIO_NARROW
            )
        if partner is not None and not _is_narrow(partner):
            partner = None
        if partner is not None:
            upper, lower = (line, partner) if line.y0 <= partner.y0 else (partner, line)
            upper_text = (upper.text or "").strip()
            lower_text = (lower.text or "").strip()
            # Both parts must look like maths, not like wrapped prose: a sentence
            # sitting above the next line is ordinary text, and merging it produced
            # duplicated question bodies.
            if (
                upper_text
                and lower_text
                and len(upper_text) <= _MAX_FRACTION_PART
                and len(lower_text) <= _MAX_FRACTION_PART
                and not _PROSE_MARK.search(upper_text + lower_text)
            ):
                consumed.add(id(upper))
                consumed.add(id(lower))
                box = _MergedBox(
                    x0=min(upper.x0, lower.x0),
                    y0=min(upper.y0, lower.y0),
                    x1=max(upper.x1, lower.x1),
                    y1=max(upper.y1, lower.y1),
                    # A slash keeps the formula readable and unambiguous.
                    text=f"{upper_text}/{lower_text}",
                )
                # A one- or two-character box wedged inside the stack is noise from the
                # fraction bar, so it is dropped rather than left to join the
                # option line as a stray letter.
                for stray in remaining:
                    if id(stray) in consumed or stray is upper or stray is lower:
                        continue
                    if _is_stack_noise(stray, box):
                        consumed.add(id(stray))
                merged.append(box)
                continue

        # 2. A superscript or subscript attached to this box.
        script = _script_of(line, remaining)
        if script is not None:
            kind, unit = script
            unit_text = (unit.text or "").strip()
            if unit_text:
                consumed.add(id(line))
                consumed.add(id(unit))
                if kind == "super":
                    rendered = text + unit_text.translate(_SUPERSCRIPT)
                else:
                    rendered = text + unit_text.translate(_SUBSCRIPT)
                merged.append(
                    _MergedBox(
                        x0=line.x0,
                        y0=min(line.y0, unit.y0),
                        x1=max(line.x1, unit.x1),
                        y1=max(line.y1, unit.y1),
                        text=rendered,
                    )
                )
                continue

    result: list[Box] = []
    for line in lines:
        if id(line) not in consumed:
            result.append(line)
    result.extend(merged)
    return result


def build_question_text(lines: Sequence[Box], question_start_pattern) -> str:  # noqa: ANN001
    """Assemble readable text for one question from its OCR boxes.

    Breaks are anchored on question numbers rather than on raw row order. The column
    preamble, section banners (plus their wrapped continuation line) and the page
    folio are dropped: they belong to no question and used to leak into the stored
    text.
    """
    if not lines:
        return ""

    # Rebuild fractions and scripts before rows are formed: they arrive as separate
    # boxes, and the row join would flatten them into nonsense.
    lines = rebuild_stacked_math(lines)

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
        pieces: list[str] = []
        option_line: list[str] = []
        for row in block:
            # A bare number row is the page folio printed in the margin; it belongs
            # to no question, wherever it happens to sort.
            if _is_folio(row):
                continue
            if _has_options(row):
                # Options sit shoulder to shoulder on one printed line; keeping them
                # on separate lines would triple the height of every question.
                if option_line:
                    pieces.append("  ".join(option_line))
                    option_line = []
                option_line.extend(_split_options(row, gap_limit))
                continue
            if option_line:
                pieces.append("  ".join(option_line))
                option_line = []
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
        if option_line:
            pieces.append("  ".join(option_line))
        if pieces:
            out.append("\n".join(_reflow(pieces)))
    return "\n".join(out)
