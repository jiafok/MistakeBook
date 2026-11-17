from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from app.services.ocr import _red_mask, _red_signal, _segment_rows, get_region_engine


def make_paper_image() -> bytes:
    img = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(img)
    for top in (120, 520, 920):
        draw.rectangle([60, top, 900, top + 8], fill="black")
        draw.rectangle([60, top + 40, 820, top + 48], fill="black")
        draw.rectangle([60, top + 80, 760, top + 88], fill="black")
        draw.rectangle([60, top + 140, 600, top + 148], fill=(60, 60, 60))
    draw.line([880, 640, 940, 700], fill=(220, 30, 30), width=9)
    draw.line([940, 640, 880, 700], fill=(220, 30, 30), width=9)
    draw.line([880, 1040, 905, 1065], fill=(210, 40, 40), width=9)
    draw.line([905, 1065, 945, 1010], fill=(210, 40, 40), width=9)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


data = make_paper_image()
image = Image.open(io.BytesIO(data)).convert("RGB")
gray = image.convert("L")

bands = _segment_rows(gray)
print("bands:", bands)
print("band count:", len(bands))

mask = _red_mask(image)
print("red signal X:", [round(v, 4) for v in _red_signal(mask, (860, 620, 960, 720))])
print("red signal clean:", [round(v, 4) for v in _red_signal(mask, (60, 120, 600, 260))])

analysis = get_region_engine().analyze(data)
print("regions:", len(analysis.regions))
for r in analysis.regions:
    print(f"  order={r.region_order} y={r.y} h={r.height} prob={r.wrong_probability} band={r.band}")
