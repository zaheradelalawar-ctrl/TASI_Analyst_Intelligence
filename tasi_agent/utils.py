"""Small shared helpers: dates, hashes, numbers, logging."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Iterable

DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d",
)


def today_iso() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def parse_date(value) -> str | None:
    """Return an ISO date string, or None if unparseable."""
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text)
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else None


def days_between(iso_a: str, iso_b: str) -> int:
    a = dt.date.fromisoformat(iso_a)
    b = dt.date.fromisoformat(iso_b)
    return (b - a).days


def sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(("" if p is None else str(p)).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def safe_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("\u066b", ".")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {"-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pct(a: float, b: float) -> float | None:
    if not b:
        return None
    return (a / b - 1.0) * 100.0


def median(values: Iterable[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0