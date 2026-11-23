"""Regression tests for text assembly (line breaks, table cells, banners)."""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.textlayout import build_question_text, cluster_rows

QUESTION_START_RE = re.compile(r"^\s*(?:第\s*)?(\d{1,3})\s*[.、．]|^\s*第\s*(\d{1,3})\s*题")


class Box:
    def __init__(self, x0, y0, x1, y1, text):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.text = text

    def __repr__(self):
        return f"Box({self.text!r})"


failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"ok  {name}")
    else:
        failures.append(name)
        print(f"FAIL {name}  {detail}")


# --- 1. a tall banner heading must not be glued onto question 1 ------------
banner = Box(93, 143, 497, 200, "一、选择题（本大题共8个小题，每小题1分，")
banner2 = Box(90, 174, 453, 227, "共8分。每小题只有一个选项符合题意）")
q1 = Box(89, 203, 349, 249, "1.构成氯化钠的微观粒子是")
opt = Box(114, 236, 503, 291, "A.原子B.分子C.电子D.离子")

text = build_question_text([banner, banner2, q1, opt], QUESTION_START_RE)
check("banner heading dropped", "一、选择题" not in text, text)
check("banner continuation dropped", "共8分" not in text, text)
check("question 1 kept", "构成氯化钠" in text, text)
check("option line kept", "A.原子B.分子" in text, text)
check("no extra blank lines", "\n\n" not in text, repr(text))

# --- 2. a question number offset a few px below its text stays together ----
num = Box(521, 502, 537, 515, "7.")
body = Box(549, 498, 908, 523, "（2025山西中考改编）探究浓氨水性质的实")
rows = cluster_rows([body, num])
check("offset number shares one row", len(rows) == 1, [r.lines for r in rows])

text = build_question_text([num, body], QUESTION_START_RE)
check("number precedes its text", text.startswith("7.") or "7.（2025" in text, text)

# --- 3. a table row keeps its cells in column order ------------------------
header = Box(120, 740, 165, 764, "选项")
header_b = Box(250, 740, 400, 764, "宏观事实")
header_c = Box(480, 740, 640, 764, "微观解释")
row_a = Box(125, 796, 142, 820, "A")
row_a_b = Box(250, 796, 320, 820, "钢瓶中")
row_a_c = Box(480, 796, 548, 820, "子变小")

text = build_question_text([header, header_b, header_c, row_a, row_a_b, row_a_c], QUESTION_START_RE)
check("table cells joined with separator", "|" in text, text)
check("cell order preserved", text.index("子变小") > text.index("钢瓶中"), text)

# --- 4. a wrapped prose line is reflowed into one paragraph ---------------
frag_a = Box(89, 203, 349, 249, "1.构成氯化钠的微观粒子是")
frag_b = Box(89, 236, 349, 282, "以离子形式存在")

text = build_question_text([frag_a, frag_b], QUESTION_START_RE)
check("wrapped prose reflowed", text.count("\n") == 0, repr(text))
check("reflow keeps every character", "以离子形式存在" in text, repr(text))
check("reflow puts fragments in order", text.index("微观粒子") < text.index("以离子"), text)

# --- 4b. option lines are never swallowed by the prose line above --------
opt_a = Box(89, 203, 349, 249, "1.构成氯化钠的微观粒子是")
opt_b = Box(89, 236, 420, 282, "A.原子B.分子C.电子D.离子")

text = build_question_text([opt_a, opt_b], QUESTION_START_RE)
check("option line keeps its own line", text.count("\n") == 1, repr(text))
check("option text intact", "A.原子B.分子" in text, text)

# --- 4c. a wrapped table cell is not appended to the row above -----------
row_one = Box(120, 740, 165, 764, "选项")
row_one_b = Box(250, 740, 400, 764, "宏观事实")
row_one_c = Box(480, 740, 640, 764, "微观解释")
row_two = Box(250, 796, 320, 820, "钢瓶中")
row_two_c = Box(480, 796, 548, 820, "子变小")

text = build_question_text([row_one, row_one_b, row_one_c, row_two, row_two_c], QUESTION_START_RE)
check("table rows stay on separate lines", text.count("\n") == 1, repr(text))
check("table cell not glued to header", "微观解释钢瓶中" not in text, text)

# --- 5. duplicated punctuation from a split box is collapsed --------------
d1 = Box(521, 173, 537, 192, "6.")
d2 = Box(540, 173, 908, 204, ".（跨学科实践活动）下列对原子结构模型演")

text = build_question_text([d1, d2], QUESTION_START_RE)
check("doubled punctuation collapsed", "6.." not in text and "6." in text, text)

# --- 6. a bare folio row is not part of the last question ----------------
q9 = Box(517, 1000, 933, 1029, "9.（2分）（黑白卷）农历五月初五过端午节时")
folio = Box(913, 1308, 939, 1334, "13")

text = build_question_text([q9, folio], QUESTION_START_RE)
check("page folio dropped", not text.endswith("13"), repr(text))

# --- 7. numbering anchored: a later question starts a new block ----------
q1a = Box(89, 203, 349, 249, "1.构成氯化钠的微观粒子是")
q2a = Box(86, 266, 503, 321, "2.（教材素材改编）氩氨刀是一种低温冷冻")

text = build_question_text([q1a, q2a], QUESTION_START_RE)
check("two questions split into blocks", len(text.splitlines()) == 2, repr(text))
check("question 2 text intact", "氩氨刀" in text, text)

# --- 8. a stacked fraction is rebuilt instead of split across lines -------
# Geometry copied from the real paper: '12m' at y=869-892, 'n' at y=905-919.
q8 = Box(522, 743, 929, 769, "8.已知某种碳原子的实际质量为mg")
num = Box(580, 869, 619, 892, "12m")
den = Box(593, 905, 607, 919, "n")

text = build_question_text([q8, num, den], QUESTION_START_RE)
check("stacked fraction rebuilt", "12m/n" in text, text)
check("fraction parts not left dangling", "\nn\n" not in text, repr(text))

# --- 8b. two lines of prose are never merged into a fraction --------------
prose_upper = Box(522, 743, 929, 769, "8.已知某种碳原子的实际质量为mg，其相对")
prose_lower = Box(551, 775, 929, 800, "原子质量为12（以这种碳原子的质量为标")

text = build_question_text([prose_upper, prose_lower], QUESTION_START_RE)
check("prose lines not merged as a fraction", "/" not in text, text)
check("prose upper kept verbatim", "其相对" in text, text)
check("prose lower kept verbatim", "为标" in text, text)
check("prose not duplicated", text.count("已知某种碳原子的实际质量") == 1, text)

# --- 8c. a superscript glyph is raised onto its base ----------------------
base = Box(100, 200, 130, 224, "m")
script = Box(131, 194, 143, 208, "2")

text = build_question_text([base, script], QUESTION_START_RE)
check("superscript raised", "m²" in text, text)

# --- 8d. options laid out side by side keep their own formula -------------
opt_a = Box(554, 886, 572, 902, "A.")
frac_a = Box(580, 869, 619, 892, "12m")
den_a = Box(593, 905, 607, 919, "n")
opt_b = Box(659, 884, 681, 907, "B")
frac_b = Box(694, 869, 730, 896, "12n")
den_b = Box(701, 899, 724, 922, "m")
noise = Box(723, 894, 740, 914, "H")

text = build_question_text([opt_a, frac_a, den_a, opt_b, frac_b, den_b, noise], QUESTION_START_RE)
check("option A keeps its own fraction", "A.12m/n" in text, text)
check("option B keeps its own fraction", "B 12n/m" in text, text)
check("stray glyph dropped from the stack", "mH" not in text, text)
check("options stay on one line", text.count("\n") == 0, repr(text))

# --- 8e. prose ending in a CJK glyph never gains a fake superscript -------
prose = Box(517, 1000, 933, 1029, "5.下列关于原子、分子、离子的说法正确的是")
bracket = Box(900, 1016, 918, 1030, "（C")

text = build_question_text([prose, bracket], QUESTION_START_RE)
check("no fake superscript on prose", "⁽" not in text, text)
check("prose kept verbatim", "说法正确的是" in text, text)

print()
if failures:
    print(f"{len(failures)} LAYOUT CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL LAYOUT CHECKS PASSED")
