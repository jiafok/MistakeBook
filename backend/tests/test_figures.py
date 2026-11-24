"""Regression tests for lifting *pictures* out of a question.

The mistake book is re-typeset and printed in bulk, so a question may never become a
screenshot: the text stays text and only the drawings travel as images. These tests
pin the properties that make that safe:

* a text-only question yields no pictures (nothing is screenshotted);
* a drawing is detected and its box covers the drawing;
* a ruled table is never mistaken for a drawing, and a labelled diagram never
  mistaken for a table;
* letterhead ink running off the sheet edge is not lifted as a picture;
* words printed inside a drawing are removed from the question text (no double
  printing), while the same word used in the answers survives;
* a picture key is stable, so re-committing overwrites instead of duplicating;
* the stored picture set round-trips and rejects stale versions.
"""

import io
import sys

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.figures import (
    FIGURE_SET_VERSION,
    FigureBox,
    crop_figure,
    detect_figures,
    dump_figure_set,
    figure_key,
    load_figure_set,
    strip_figure_labels,
)

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}  {detail}")


class Line:
    """Stand-in for an OCR line (the detector only reads these attributes)."""

    def __init__(self, x0, y0, x1, y1, text=""):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.text = text
        self.confidence = 0.9


def text_rows(count=6, top=20, height=22, left=10, right=280):
    """Evenly spaced text lines, like a paragraph of a real question."""
    return [
        Line(left, top + index * (height + 12), right, top + index * (height + 12) + height, "题干文字")
        for index in range(count)
    ]


def page_with(drawing=None, size=(320, 300)):
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    for line in text_rows(count=4):
        draw.rectangle([line.x0, line.y0, line.x1, line.y1], fill="black")
    if drawing is not None:
        drawing(draw)
    return page


def bare_page(drawings, size=(320, 300), rows=None):
    """Page whose only ink is the given drawings plus the text rows."""
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    for line in rows if rows is not None else text_rows(count=4):
        draw.rectangle([line.x0, line.y0, line.x1, line.y1], fill="black")
    for drawing in drawings:
        drawing(draw)
    return page


# --- 1. a text-only question is never turned into a picture ---------------
page = page_with()
boxes = detect_figures(page, (0, 0, 320, 300), text_rows(count=4))
check("text-only question yields no figures", boxes == [], boxes)

# --- 2. a drawing is found and its box covers it --------------------------
def beaker(draw):
    draw.rectangle([120, 170, 190, 250], outline="black", width=3)
    draw.ellipse([130, 190, 180, 240], outline="black", width=3)


page = page_with(beaker)
boxes = detect_figures(page, (0, 0, 320, 300), text_rows(count=4))
check("drawing is detected", len(boxes) == 1, boxes)
if boxes:
    box = boxes[0]
    check(
        "box covers the drawing",
        box.x <= 120 and box.y <= 170 and box.right >= 190 and box.bottom >= 250,
        box,
    )
    check("box is not the whole region", box.area < 320 * 300 * 0.9, box)

# --- 3. no recognised text means no reliable way to separate ink ----------
check(
    "no OCR lines yields no figures",
    detect_figures(page, (0, 0, 320, 300), []) == [],
)

# --- 4. labels printed inside a picture are stripped from the text --------
box = FigureBox(x=100, y=100, width=120, height=120)
lines = [
    Line(10, 10, 280, 32, "如图所示，下列说法正确的是"),
    Line(120, 140, 180, 160, "浓氨水"),
    Line(10, 300, 280, 322, "外部正文不应删除"),
]
cleaned = strip_figure_labels("如图所示，下列说法正确的是\n浓氨水\n外部正文不应删除", lines, [box])
check("label inside the picture is removed", "浓氨水" not in cleaned, cleaned)
check("body text outside the picture survives", "外部正文不应删除" in cleaned, cleaned)
check("body text before the picture survives", "如图所示" in cleaned, cleaned)

# --- 4b. a label word is only removed where it stands alone --------------
# The same word usually appears inside the answers; stripping it there leaves a
# question that no longer makes sense ("瓶中浓氨水变蓝" -> "瓶中变蓝").
mixed = strip_figure_labels(
    "探究浓氨水性质\nA.瓶中浓氨水变蓝\n浓氨水",
    [Line(120, 140, 180, 160, "浓氨水")],
    [box],
)
check("label is not removed from a longer word", "探究浓氨水性质" in mixed, mixed)
check("label is not removed from an answer", "A.瓶中浓氨水变蓝" in mixed, mixed)
check("standalone label line is removed", "\n浓氨水" not in mixed, mixed)

# --- 4c. a label joined to its neighbour by a column separator -----------
# The layout engine writes the gap it found as '|' or '/', so two labels printed
# over one another arrive as '蘸有少量/的棉签'. Requiring spaces on both sides let
# exactly that case through, and the label printed twice: once in the body, once
# inside the picture.
joined = strip_figure_labels(
    "有关分析或现象描述正确\n蘸有少量/的棉签\n该实验说明",
    [Line(120, 140, 180, 160, "蘸有少量"), Line(150, 165, 200, 185, "的棉签")],
    [box],
)
check(
    "a label joined by a separator is removed",
    "蘸有少量" not in joined and "的棉签" not in joined,
    joined,
)
check("surrounding body text survives the separator case", "该实验说明" in joined, joined)
check("the line before the labels survives", "有关分析" in joined, joined)

# --- 5. long lines are never treated as picture labels -------------------
long_line = [Line(110, 110, 210, 130, "这是一整行非常长的题干文字不应该被删除掉啊")]
check(
    "long line is not stripped as a label",
    strip_figure_labels(long_line[0].text, long_line, [box]) == long_line[0].text,
)

# --- 6. a picture key is stable, so re-committing overwrites -------------
check("figure key is stable", figure_key(1, 86, 0) == figure_key(1, 86, 0), figure_key(1, 86, 0))
check(
    "figure key is per user, question and index",
    len({figure_key(1, 86, 0), figure_key(2, 86, 0), figure_key(1, 87, 0), figure_key(1, 86, 1)}) == 4,
)

# --- 7. the stored picture set round-trips and rejects stale versions ----
payload = dump_figure_set([{"key": figure_key(1, 86, 0), "width": 40, "height": 30}])
restored = load_figure_set(payload)
check("figure set round-trips", restored is not None and len(restored) == 1, restored)
check("empty set reads as empty", load_figure_set(dump_figure_set([])) == [])
check("missing set reads as not computed", load_figure_set("") is None)
check("blank set reads as not computed", load_figure_set(None) is None)
check("garbage reads as not computed", load_figure_set("{not json") is None)
check(
    "stale version reads as not computed",
    load_figure_set('{"v": 0, "items": [{"key": "x"}]}') is None,
)
check("current version is 2", FIGURE_SET_VERSION == 2)

# --- 8. the crop is a real, upscaled PNG of the drawing ------------------
buffer = io.BytesIO()
page.save(buffer, format="PNG")
data = crop_figure(buffer.getvalue(), FigureBox(120, 120, 70, 80), 320, 260)
crop = Image.open(io.BytesIO(data)).convert("RGB")
check("crop is a real image", len(data) > 200, len(data))
check("crop is upscaled for printing", crop.width > 70 and crop.height > 80, crop.size)
check(
    "crop carries the drawing ink",
    sum(1 for pixel in crop.getdata() if sum(pixel) < 400) > 100,
)

# --- 9. a degenerate box is rejected instead of saving a blank file ------
try:
    crop_figure(buffer.getvalue(), FigureBox(10, 10, 0, 0), 320, 260)
    check("degenerate box rejected", False, "no exception")
except ValueError:
    check("degenerate box rejected", True)

# --- 10. a full-page box with no drawings stays empty --------------------
full = bare_page([], size=(320, 260))
check("blank page yields no figures", detect_figures(full, (0, 0, 320, 260), text_rows()) == [])


# --- 11. a ruled table is not a drawing ---------------------------------
def table_with_text(draw):
    """A grid whose cells hold body text - like 「下列…错误的是」的选项表."""
    left, top, right = 40, 90, 280
    for row in range(4):
        y = top + row * 30
        draw.line([left, y, right, y], fill="black", width=2)
    draw.line([left, top + 120, right, top + 120], fill="black", width=2)
    for column in (left, 140, right):
        draw.line([column, top, column, top + 120], fill="black", width=2)
    # Cell text, printed as solid blocks on the text rows so OCR still "reads" it.
    for row in range(4):
        for cell_left, cell_right in ((left + 6, 134), (146, right - 6)):
            y = top + row * 30 + 6
            draw.rectangle([cell_left, y, cell_right, y + 16], fill="black")


table_rows = [
    Line(10, 20, 300, 42, "下列对宏观事实的微观解释错误的是"),
    Line(46, 96, 134, 112, "选项"),
    Line(146, 96, 274, 112, "解释"),
    Line(46, 126, 134, 142, "A.水变热"),
    Line(146, 126, 274, 142, "分子间隔变大"),
    Line(46, 156, 134, 172, "B.酒挥发"),
    Line(146, 156, 274, 172, "分子不断运动"),
    Line(46, 186, 134, 202, "C.糖溶解"),
    Line(146, 186, 274, 202, "分子体积变小"),
]
table_page = Image.new("RGB", (320, 260), "white")
table_draw = ImageDraw.Draw(table_page)
table_draw.rectangle([10, 20, 300, 42], fill="black")
table_with_text(table_draw)
check(
    "ruled table is not lifted as a drawing",
    detect_figures(table_page, (0, 0, 320, 260), table_rows) == [],
    detect_figures(table_page, (0, 0, 320, 260), table_rows),
)

# --- 12. a labelled diagram is not a table ------------------------------
# Its labels sit over most of its ink, which alone looks exactly like the table
# above; only the absence of long straight rules tells them apart.
labelled_rows = [
    Line(10, 20, 300, 42, "探究浓氨水性质的实验设计如下"),
    Line(120, 150, 176, 168, "浓氨水"),
    Line(120, 172, 190, 190, "酚酞溶液"),
]


def labelled_apparatus(draw):
    draw.rectangle([110, 120, 200, 200], outline="black", width=3)
    draw.ellipse([130, 140, 160, 170], outline="black", width=3)
    draw.rectangle([170, 60, 196, 118], outline="black", width=3)


apparatus_page = Image.new("RGB", (320, 260), "white")
apparatus_draw = ImageDraw.Draw(apparatus_page)
for line in labelled_rows:
    apparatus_draw.rectangle([line.x0, line.y0, line.x1, line.y1], fill="black")
labelled_apparatus(apparatus_draw)
apparatus_boxes = detect_figures(apparatus_page, (0, 0, 320, 260), labelled_rows)
check("labelled diagram is kept", len(apparatus_boxes) == 1, apparatus_boxes)
if apparatus_boxes:
    kept = apparatus_boxes[0]
    check(
        "labelled diagram box covers the apparatus",
        kept.x <= 110 and kept.y <= 60 and kept.right >= 200 and kept.bottom >= 200,
        kept,
    )

# --- 13. ink running off the sheet is letterhead, not a picture ---------
edge_page = Image.new("RGB", (320, 260), "white")
edge_draw = ImageDraw.Draw(edge_page)
for line in text_rows(count=4):
    edge_draw.rectangle([line.x0, line.y0, line.x1, line.y1], fill="black")
# A decorative band touching the top-left corner, bleeding past the edge.
edge_draw.polygon([(0, 0), (150, 0), (0, 110)], fill="black")
check(
    "letterhead bleeding off the edge is not a picture",
    detect_figures(edge_page, (0, 0, 320, 260), text_rows(count=4)) == [],
    detect_figures(edge_page, (0, 0, 320, 260), text_rows(count=4)),
)

# --- 14. a stripped label must not leave a dangling table separator ------
# A table row is joined with " | ". Removing the label at one end used to leave
# the bar behind, which printed as a stray vertical line in the question.
dangling = strip_figure_labels(
    "A.瓶中浓氨水变蓝 | 酚有少量\nB.棉签上酚酞溶液变红 | 酚酞溶液",
    [Line(200, 140, 280, 160, "酚有少量"), Line(200, 180, 280, 200, "酚酞溶液")],
    [FigureBox(x=190, y=130, width=110, height=90)],
)
check("no dangling separator at line end", "变蓝 |" not in dangling, dangling)
check(
    "no row is left as a bare separator",
    not any(line.strip(" |") == "" and "|" in line for line in dangling.splitlines()),
    dangling,
)
check("cell text on the kept side survives", "A.瓶中浓氨水变蓝" in dangling, dangling)
check("second row keeps its text", "B.棉签上酚酞溶液变红" in dangling, dangling)

print()
if failures:
    print(f"{len(failures)} FIGURE CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL FIGURE CHECKS PASSED")
