"""OCR + Question Region Engine.

Geometry/probability detection is implemented with pure Pillow so the system works
out of the box. Text extraction is delegated to a pluggable provider (PaddleOCR,
Tesseract, MinerU, OCRmyPDF). If no provider is available the engine still returns
valid question geometry plus a red-pen based probability estimate.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image, ImageChops, ImageOps, ImageStat

from ..config import settings

SUSPECT_SYMBOLS = ("△", "?", "？", "○")
CORRECT_SYMBOLS = ("√", "✓", "✔")

QUESTION_LABEL_RE = re.compile(
    r"^\s*(?:第\s*)?(\d{1,3})\s*[\.、\)．]|^\s*[（(](\d{1,3})[）)]|^\s*([一二三四五六七八九十]+)\s*[、\.]"
)


@dataclass
class RegionBox:
    x: int
    y: int
    width: int
    height: int
    region_order: int
    label: str = ""
    text: str = ""
    wrong_probability: int | None = None
    band: str = "unknown"
    detection_status: str = "ok"


@dataclass
class PageAnalysis:
    width: int
    height: int
    text: str = ""
    regions: list[RegionBox] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class OcrLine:
    x0: int
    y0: int
    x1: int
    y1: int
    text: str
    confidence: float


class TextOcrProvider(Protocol):
    name: str
    available: bool
    reason: str

    def extract(self, image: Image.Image) -> str: ...


class NullOcr:
    name = "heuristic"
    available = True
    reason = "内置启发式引擎：仅做版面切分与红笔分析，不含文字识别"

    def extract(self, image: Image.Image) -> str:
        return ""


class RapidOcr:
    name = "rapidocr"
    available = False
    reason = "rapidocr-onnxruntime 未安装或模型不可用"

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
            self.available = True
            self.reason = "ok"
        except Exception as exc:  # pragma: no cover - depends on optional runtime
            self.reason = f"RapidOCR 不可用: {exc}"

    def extract_lines(self, image: Image.Image) -> list[OcrLine]:
        if not self.available:
            return []
        try:
            result, _elapsed = self._engine(image.convert("RGB"))
        except Exception:
            return []

        lines: list[OcrLine] = []
        for item in result or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                points = [(float(point[0]), float(point[1])) for point in item[0]]
                text = str(item[1]).strip()
                confidence = float(item[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not points or not text:
                continue
            lines.append(
                OcrLine(
                    x0=max(0, int(min(x for x, _ in points))),
                    y0=max(0, int(min(y for _, y in points))),
                    x1=int(max(x for x, _ in points)),
                    y1=int(max(y for _, y in points)),
                    text=text,
                    confidence=confidence,
                )
            )
        return sorted(lines, key=lambda line: (line.y0, line.x0))

    def extract(self, image: Image.Image) -> str:
        return "\n".join(line.text for line in self.extract_lines(image))


class TesseractOcr:
    name = "tesseract"
    available = False
    reason = "pytesseract 或 tesseract 未安装"

    def __init__(self) -> None:
        try:
            import pytesseract  # type: ignore

            pytesseract.get_tesseract_version()
            self._tess = pytesseract
            self.available = True
            self.reason = "ok"
        except Exception as exc:  # pragma: no cover
            self.reason = f"pytesseract 不可用: {exc}"

    def extract(self, image: Image.Image) -> str:
        if not self.available:
            return ""
        try:
            return str(self._tess.image_to_string(image, lang="chi_sim+eng") or "")
        except Exception:
            return ""


class PaddleOcr:
    name = "paddle"
    available = False
    reason = "paddleocr 未安装"

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore

            self._engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            self.available = True
            self.reason = "ok"
        except Exception as exc:  # pragma: no cover
            self.reason = f"paddleocr 不可用: {exc}"

    def extract(self, image: Image.Image) -> str:
        if not self.available:
            return ""
        try:
            import numpy as np  # type: ignore

            result = self._engine.ocr(np.array(image), cls=True)
            lines: list[str] = []
            for page in result or []:
                for item in page or []:
                    if len(item) >= 2 and isinstance(item[1], (list, tuple)):
                        lines.append(str(item[1][0]))
            return "\n".join(lines)
        except Exception:
            return ""


class ExternalCommandOcr:
    """MinerU / OCRmyPDF adapters. Enabled only when explicitly configured."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.available = False
        self.reason = f"{name} 适配器需要外部命令，未在本实例启用"

    def extract(self, image: Image.Image) -> str:
        return ""


def get_ocr_provider() -> TextOcrProvider:
    name = (settings.ocr_provider or "rapidocr").lower()
    if name == "rapidocr":
        provider = RapidOcr()
        return provider if provider.available else NullOcr()
    if name == "paddle":
        provider = PaddleOcr()
        return provider if provider.available else _rapidocr_or_null()
    if name in ("tesseract", "ocr"):
        provider = TesseractOcr()
        return provider if provider.available else _rapidocr_or_null()
    if name in ("mineru", "ocrmypdf"):
        return ExternalCommandOcr(name)
    return NullOcr()


def _rapidocr_or_null() -> TextOcrProvider:
    provider = RapidOcr()
    return provider if provider.available else NullOcr()


def band_for(probability: int | None) -> str:
    if probability is None:
        return "unknown"
    if probability >= settings.probability_recommend:
        return "recommended"
    if probability >= settings.probability_suspect:
        return "suspected"
    return "normal"


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _text_signal(text: str) -> tuple[float, str]:
    """Return (delta, evidence). Positive delta means 'more likely wrong'."""
    if not text:
        return 0.0, ""
    stripped = text.strip()
    has_wrong_mark = any(sym in stripped for sym in ("×", "✗")) or bool(
        re.search(r"(?<![\w])X(?![\w])", stripped)
    )
    if has_wrong_mark:
        return 50.0, "检测到叉号/错误符号"
    if any(sym in stripped for sym in SUSPECT_SYMBOLS):
        return 25.0, "检测到待定符号"
    if any(sym in stripped for sym in CORRECT_SYMBOLS):
        return -30.0, "检测到对勾符号"
    if re.search(r"[-−]\s*\d+(?:\.\d+)?\s*分?", stripped):
        return 30.0, "检测到扣分标注"
    if re.search(r"\d+\s*/\s*\d+", stripped):
        return 15.0, "检测到分数标注"
    return 0.0, ""


def _red_mask(image: Image.Image) -> Image.Image:
    """L-mode mask: 255 where a pixel is strongly red (red-pen grading marks)."""
    rgb = image.convert("RGB")
    r, g, b = rgb.split()
    # strongly red: r high, and clearly dominant over g and b
    mask_rg = ImageChops.subtract(r, g)
    mask_rb = ImageChops.subtract(r, b)
    mask = ImageChops.darker(mask_rg, mask_rb)
    bright = r.point(lambda p: 255 if p > 105 else 0)
    return ImageChops.darker(mask, bright)


def _red_signal(mask: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float]:
    """Return (coverage, peak_density) of red pixels inside box.

    peak_density uses a pixel-scale grid (~12px tiles) instead of a fixed 8x8
    grid, so a small but saturated mark such as a red X is not diluted by the
    surrounding paper area. A minimum stroke count suppresses single-pixel noise
    from JPEG artifacts or coloured paper.
    """
    crop = mask.crop(box)
    if crop.width < 12 or crop.height < 12:
        return 0.0, 0.0

    pixels = list(crop.resize((crop.width // 4, crop.height // 4), Image.Resampling.BOX).getdata())
    strong = sum(1 for v in pixels if v > 90)
    if strong < 12:  # not enough red ink to be a grading mark
        return 0.0, 0.0

    tw = max(1, crop.width // 12)
    th = max(1, crop.height // 12)
    grid = list(crop.resize((tw, th), Image.Resampling.BOX).getdata())
    peak = (max(grid) / 255.0) if grid else 0.0

    total = sum(pixels) / 255.0
    coverage = total / max(1, len(pixels))
    return coverage, peak


def _mark_shape(mask: Image.Image, box: tuple[int, int, int, int]) -> tuple[str, float]:
    """Classify a red grading mark as cross / check / unknown.

    Both glyphs use two strokes, so diagonal-energy alone cannot separate them.
    They differ at the extremes:

    * cross (x): ink reaches BOTH left and right edges at the top and at the bottom
    * check (v): the top edge carries ink only near the right tip; the bottom edge
      carries ink near the left/centre elbow

    We therefore compare horizontal ink span in the top and bottom bands.
    """
    crop = mask.crop(box)
    red_bbox = crop.getbbox()
    if red_bbox is None:
        return "unknown", 0.0
    mark = crop.crop(red_bbox)
    if mark.width < 12 or mark.height < 12:
        return "unknown", 0.0

    size = 40
    grid = mark.resize((size, size), Image.Resampling.BOX)
    px = [v / 255.0 for v in grid.getdata()]

    def band_profile(rows: range) -> tuple[float, float, float]:
        """Return (ink, leftmost_x, rightmost_x) normalised in [0,1]."""
        total = 0.0
        lo = size
        hi = -1
        for r in rows:
            for c in range(size):
                if px[r * size + c] > 0.28:
                    total += px[r * size + c]
                    lo = min(lo, c)
                    hi = max(hi, c)
        if hi < 0:
            return 0.0, 0.5, 0.5
        return total, lo / (size - 1), hi / (size - 1)

    band = max(2, size // 5)
    top_ink, top_lo, top_hi = band_profile(range(0, band))
    bot_ink, bot_lo, bot_hi = band_profile(range(size - band, size))

    if top_ink + bot_ink < 0.6:
        return "unknown", 0.0

    top_wide = (top_hi - top_lo) >= 0.55
    bot_wide = (bot_hi - bot_lo) >= 0.55
    top_right_only = top_lo >= 0.45
    bot_left_only = bot_hi <= 0.75

    # cross: wide ink at BOTH extremes
    if top_wide and bot_wide:
        return "cross", min(1.0, ((top_hi - top_lo) + (bot_hi - bot_lo)) / 2)

    # check: narrow at top (right tip) and narrow at bottom (left elbow)
    if top_right_only and bot_left_only and not bot_wide:
        confidence = 0.6 + 0.4 * min(1.0, (top_lo + (1 - bot_hi)) / 2)
        return "check", min(1.0, confidence)

    # check with a slightly wider bottom elbow
    if top_right_only and not top_wide:
        return "check", 0.62

    return "unknown", 0.0


def _segment_rows(gray: Image.Image) -> list[tuple[int, int]]:
    """Row-projection segmentation of text blocks (not individual lines)."""
    width, height = gray.size
    if height == 0 or width == 0:
        return []

    # binary ink mask: white paper -> 0, ink -> 255
    binary = gray.point(lambda p: 255 if p < 180 else 0)
    rows = list(binary.resize((1, height), Image.Resampling.BOX).getdata())
    ink = [v / 255.0 for v in rows]

    line_threshold = 0.004          # a row with any meaningful ink
    line_gap = max(10, int(height * 0.045))   # blank run that still belongs to the same question
    min_block = max(10, int(height * 0.012))

    raw: list[tuple[int, int]] = []
    start: int | None = None
    gap_run = 0
    for y, value in enumerate(ink):
        if value > line_threshold:
            if start is None:
                start = y
            gap_run = 0
        else:
            if start is not None:
                gap_run += 1
                if gap_run >= line_gap:
                    end = y - gap_run
                    if end > start:
                        raw.append((start, end))
                    start = None
                    gap_run = 0
    if start is not None and height > start:
        raw.append((start, height))

    # merge line runs into question blocks
    merged: list[list[int]] = []
    for top, bottom in raw:
        if merged and top - merged[-1][1] <= line_gap:
            merged[-1][1] = bottom
        else:
            merged.append([top, bottom])

    bands = [(a, b) for a, b in merged if (b - a) >= min_block]
    return bands


def _find_column_split(gray: Image.Image) -> int | None:
    """Find a strong vertical gutter between two printed page columns."""
    width, height = gray.size
    if width < 640 or height < 400:
        return None

    # Ignore paper texture and faint shadows; retain printed text and rules.
    sample = gray.resize((width, min(height, 700)), Image.Resampling.BOX)
    binary = sample.point(lambda pixel: 255 if pixel < 125 else 0)
    projection = list(binary.resize((width, 1), Image.Resampling.BOX).getdata())
    window = max(8, int(width * 0.015))
    margin = max(window, int(width * 0.08))
    low = int(width * 0.28)
    high = int(width * 0.72)
    if high <= low:
        return None

    best_x = low
    best_density = 256.0
    half = window // 2
    for x in range(low, high):
        start = max(margin, x - half)
        end = min(width - margin, x + half)
        if end <= start:
            continue
        density = sum(projection[start:end]) / (end - start)
        if density < best_density:
            best_x = x
            best_density = density

    left_end = max(margin, best_x - window)
    right_start = min(width - margin, best_x + window)
    left_density = sum(projection[margin:left_end]) / max(1, left_end - margin)
    right_density = sum(projection[right_start : width - margin]) / max(
        1, width - margin - right_start
    )
    side_density = min(left_density, right_density)
    if side_density < 1.5 or best_density > min(4, side_density * 0.35):
        return None
    return best_x


def _merge_close_bands(bands: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not bands:
        return []
    merged: list[list[int]] = [[bands[0][0], bands[0][1]]]
    for top, bottom in bands[1:]:
        if top - merged[-1][1] <= max_gap:
            merged[-1][1] = bottom
        else:
            merged.append([top, bottom])
    return [(a, b) for a, b in merged]


def _split_tall(top: int, bottom: int, max_height: int) -> list[tuple[int, int]]:
    height = bottom - top
    if height <= max_height or max_height <= 0:
        return [(top, bottom)]
    parts = max(1, round(height / max_height))
    step = height / parts
    out: list[tuple[int, int]] = []
    for i in range(parts):
        out.append((int(top + i * step), int(top + (i + 1) * step)))
    return out


class RegionEngine:
    """Detects question regions and estimates wrong probability."""

    def __init__(self) -> None:
        self.provider = get_ocr_provider()

    @property
    def status(self) -> dict[str, object]:
        return {
            "engine": "question-region-engine",
            "ocr_provider": self.provider.name,
            "ocr_available": self.provider.available,
            "ocr_reason": self.provider.reason,
            "text_extraction": self.provider.available and self.provider.name != "heuristic",
        }

    def analyze(self, image_bytes: bytes, *, _allow_column_split: bool = True) -> PageAnalysis:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
        image.load()
        rgb = image.convert("RGB")
        gray = rgb.convert("L")
        width, height = rgb.size

        if _allow_column_split:
            split_x = _find_column_split(gray)
            if split_x is not None:
                analyses: list[tuple[int, PageAnalysis]] = []
                for left, right, offset in ((0, split_x, 0), (split_x, width, split_x)):
                    column = rgb.crop((left, 0, right, height))
                    buffer = io.BytesIO()
                    column.save(buffer, format="PNG")
                    analyses.append(
                        (offset, self.analyze(buffer.getvalue(), _allow_column_split=False))
                    )

                regions: list[RegionBox] = []
                warnings: list[str] = []
                text_parts: list[str] = []
                for offset, analysis in analyses:
                    if analysis.text:
                        text_parts.append(analysis.text)
                    warnings.extend(analysis.warnings)
                    for region in analysis.regions:
                        region.x += offset
                        region.region_order = len(regions)
                        regions.append(region)
                return PageAnalysis(
                    width=width,
                    height=height,
                    text="\n\n".join(text_parts),
                    regions=regions,
                    warnings=warnings,
                )

        warnings: list[str] = []
        bands = _segment_rows(gray)
        if not bands:
            warnings.append("未检测到题目区域：页面可能是空白或对比度过低")
            return PageAnalysis(width=width, height=height, warnings=warnings)

        red_mask = _red_mask(rgb)

        # Grading marks (red X / check) are usually written in the margin or
        # between answer lines, so they form their own thin ink band. Attach such
        # thin bands to the nearest question block instead of treating them as
        # separate questions.
        typical_block = max((b - a) for a, b in bands) if bands else 0
        thin_limit = max(24, int(typical_block * 0.45))

        question_bands: list[list[int]] = []
        mark_bands: list[tuple[int, int]] = []
        for top, bottom in bands:
            thin = (bottom - top) < thin_limit
            # A thin band is only a stray grading mark if it actually contains red
            # ink. Otherwise it is a short question with very little text.
            red_ink = 0.0
            if thin and question_bands:
                probe = red_mask.crop((0, top, width, bottom))
                small = probe.resize((max(1, probe.width // 6), max(1, probe.height // 6)))
                vals = list(small.getdata())
                red_ink = sum(1 for v in vals if v > 90) / max(1, len(vals))
            if thin and question_bands and red_ink > 0.01:
                mark_bands.append((top, bottom))
            else:
                question_bands.append([top, bottom])

        for top, bottom in mark_bands:
            best_index = 0
            best_distance: int | None = None
            for index, (q_top, q_bottom) in enumerate(question_bands):
                if q_top <= top <= q_bottom:
                    distance = 0
                else:
                    distance = min(abs(top - q_bottom), abs(q_top - bottom), abs(top - q_top))
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_index = index
            q_top, q_bottom = question_bands[best_index]
            question_bands[best_index] = [min(q_top, top), max(q_bottom, bottom)]

        bands = [(a, b) for a, b in question_bands]

        max_height = int(height * 0.42)
        expanded: list[tuple[int, int]] = []
        for top, bottom in bands:
            expanded.extend(_split_tall(top, bottom, max_height))
        expanded = _merge_close_bands(expanded, max_gap=int(height * 0.008))
        ocr_lines = self.provider.extract_lines(rgb) if isinstance(self.provider, RapidOcr) else []
        full_text = (
            "\n".join(line.text for line in ocr_lines)
            if ocr_lines
            else self.provider.extract(rgb)
        )
        if not self.provider.available:
            warnings.append(self.provider.reason)

        text_lines = [line for line in full_text.splitlines() if line.strip()]
        regions: list[RegionBox] = []

        for index, (top, bottom) in enumerate(expanded):
            pad = max(2, int(height * 0.004))
            y0 = max(0, top - pad)
            y1 = min(height, bottom + pad)
            x0 = 0
            x1 = width

            # narrow horizontally to actual ink extent for tighter boxes
            crop = gray.crop((0, y0, width, y1))
            bbox = Image.eval(crop, lambda p: 255 if p < 200 else 0).getbbox()
            if bbox:
                x0 = max(0, min(bbox[0], width - 1) - 4)
                x1 = min(width, bbox[2] + 4)

            if ocr_lines:
                region_text = "\n".join(
                    line.text
                    for line in ocr_lines
                    if y0 <= (line.y0 + line.y1) // 2 < y1 and line.x0 < x1 and line.x1 > x0
                )
            else:
                region_text = text_lines[index] if index < len(text_lines) else ""
            coverage, peak = _red_signal(red_mask, (x0, y0, x1, y1))
            text_delta, evidence = _text_signal(region_text)

            # A saturated mark (red X / check) produces a high local peak; faint
            # shading produces low, broad coverage. Shape separates a cross
            # (wrong) from a check (correct).
            red_component = 0.0
            if peak > 0.25:
                red_component = _clamp((peak - 0.25) / 0.45 * 70.0, 0.0, 70.0)
            if coverage > 0.02:
                red_component = _clamp(max(red_component, coverage / 0.10 * 38.0), 0.0, 70.0)

            shape, shape_conf = _mark_shape(red_mask, (x0, y0, x1, y1))
            shape_delta = 0.0
            if shape == "cross" and shape_conf >= 0.55:
                shape_delta = 24.0
                red_component = max(red_component, 58.0)
            elif shape == "check" and shape_conf >= 0.60:
                # A red check means the teacher marked it correct: strongly negative.
                shape_delta = -70.0
                red_component = 0.0

            probability = int(round(_clamp(10.0 + red_component + shape_delta + text_delta)))

            label = ""
            match = QUESTION_LABEL_RE.match(region_text) if region_text else None
            if match:
                token = next((g for g in match.groups() if g), "")
                if token:
                    label = f"第 {token} 题"

            status = "ok"
            if region_text == "" and not self.provider.available:
                status = "ok"
            if evidence:
                region_text = f"{region_text} [{evidence}]" if region_text else f"[{evidence}]"

            regions.append(
                RegionBox(
                    x=int(x0),
                    y=int(y0),
                    width=int(max(1, x1 - x0)),
                    height=int(max(1, y1 - y0)),
                    region_order=index,
                    label=label or f"第 {index + 1} 题",
                    text=region_text,
                    wrong_probability=probability,
                    band=band_for(probability),
                    detection_status=status,
                )
            )

        # Overlap check (should not happen with row bands, but keep the invariant explicit)
        for i in range(1, len(regions)):
            prev, cur = regions[i - 1], regions[i]
            if cur.y < prev.y + prev.height:
                cur.detection_status = "overlap"
                warnings.append(f"区域 {cur.label} 与上一区域重叠，建议人工复核")

        return PageAnalysis(width=width, height=height, text=full_text, regions=regions, warnings=warnings)


_engine: RegionEngine | None = None


def get_region_engine() -> RegionEngine:
    global _engine
    if _engine is None:
        _engine = RegionEngine()
    return _engine
