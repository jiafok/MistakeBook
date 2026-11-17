"""Similarity / deduplication helpers."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

from ..config import settings

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s\.,;:!?，。；：！？、“”‘’\"'()（）\[\]【】<>《》]")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return _PUNCT_RE.sub("", _WS_RE.sub(" ", text)).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def is_duplicate(a: str, b: str) -> bool:
    return similarity(a, b) >= settings.dedup_similarity_threshold
