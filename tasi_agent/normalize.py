"""
Rating normalisation.

The original rating is ALWAYS preserved on the record. This module only
derives the agent classification and flags non-standard mappings so they are
visible in the report instead of being silently forced.
"""
from __future__ import annotations

import re

POSITIVE = "positive"
NEUTRAL = "neutral"
NEGATIVE = "negative"

# Canonical mappings taken directly from the project specification.
PRIMARY = {
    "buy": POSITIVE,
    "strong buy": POSITIVE,
    "strongbuy": POSITIVE,
    "outperform": POSITIVE,
    "overweight": POSITIVE,
    "add": POSITIVE,
    "accumulate": POSITIVE,
    "hold": NEUTRAL,
    "neutral": NEUTRAL,
    "equal weight": NEUTRAL,
    "equalweight": NEUTRAL,
    "reduce": NEGATIVE,
    "underweight": NEGATIVE,
    "sell": NEGATIVE,
    "underperform": NEGATIVE,
}

# Common synonyms found in the region. These are marked "assumed" in the report.
SECONDARY = {
    "strong outperform": POSITIVE,
    "market outperform": POSITIVE,
    "trading buy": POSITIVE,
    "speculative buy": POSITIVE,
    "long term buy": POSITIVE,
    "market perform": NEUTRAL,
    "sector perform": NEUTRAL,
    "in line": NEUTRAL,
    "market weight": NEUTRAL,
    "fair value": NEUTRAL,
    "not rated": None,
    "under review": None,
    "no rating": None,
    "restricted": None,
}

ARABIC = {
    "شراء": POSITIVE,
    "شراء قوي": POSITIVE,
    "تجميع": POSITIVE,
    "زيادة الوزن": POSITIVE,
    "أداء أفضل من السوق": POSITIVE,
    "اداء افضل من السوق": POSITIVE,
    "محايد": NEUTRAL,
    "احتفاظ": NEUTRAL,
    "وزن مساو": NEUTRAL,
    "أداء مماثل للسوق": NEUTRAL,
    "تخفيض الوزن": NEGATIVE,
    "بيع": NEGATIVE,
    "بيع قوي": NEGATIVE,
    "أداء أقل من السوق": NEGATIVE,
}

_WITHDRAWAL_TOKENS = ("withdrawn", "suspended", "coverage terminated", "إلغاء", "سحب")


def _key(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^\w\u0600-\u06FF\s\-]", " ", text)
    text = re.sub(r"[\-_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_rating(raw: str | None) -> tuple[str | None, bool]:
    """
    Return (normalised_classification, assumed_flag).

    classification is one of 'positive' | 'neutral' | 'negative' | None.
    None means the rating could not be mapped and must go to the review queue.
    """
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False

    for token in _WITHDRAWAL_TOKENS:
        if token in text.lower():
            return None, False

    # Exact Arabic match first (no lowercasing of Arabic needed).
    if text in ARABIC:
        return ARABIC[text], False

    k = _key(text)
    if k in PRIMARY:
        return PRIMARY[k], False
    if k in SECONDARY:
        val = SECONDARY[k]
        return (val, True) if val else (None, False)
    if k in ARABIC:
        return ARABIC[k], False

    # Longest substring match (e.g. "Buy (High Risk)" -> "buy").
    for table, assumed in ((PRIMARY, False), (SECONDARY, True)):
        for phrase in sorted(table, key=len, reverse=True):
            if phrase in k:
                val = table[phrase]
                return (val, assumed) if val else (None, False)
    for phrase in sorted(ARABIC, key=len, reverse=True):
        if phrase in text:
            return ARABIC[phrase], False

    return None, False


def is_withdrawal(raw: str | None) -> bool:
    if not raw:
        return False
    low = str(raw).lower()
    return any(t in low for t in _WITHDRAWAL_TOKENS)