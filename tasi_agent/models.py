"""Plain data containers used across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawItem:
    """A candidate recommendation as produced by a collector."""
    provider_name: str
    source_type: str
    ticker: str | None = None
    company_name: str | None = None
    rating_raw: str | None = None
    target_price: float | None = None
    price_at_publication: float | None = None
    publication_date: str | None = None
    source_url: str | None = None
    document_path: str | None = None
    raw_text: str | None = None
    confidence: float = 0.5
    is_withdrawal: bool = False


@dataclass
class FirmOpinion:
    rec_id: int
    provider_id: int
    provider_name: str
    ticker: str
    company_name: str | None
    rating_raw: str
    rating_norm: str
    rating_assumed: bool
    target_price: float | None
    price_at_publication: float | None
    publication_date: str
    source_url: str | None
    source_type: str
    confidence: float
    validation_status: str
    prev_rating_norm: str | None = None

    @property
    def is_upgrade(self) -> bool:
        return _ord(self.prev_rating_norm) is not None and _ord(self.prev_rating_norm) < _ord(self.rating_norm)

    @property
    def is_downgrade(self) -> bool:
        return _ord(self.prev_rating_norm) is not None and _ord(self.prev_rating_norm) > _ord(self.rating_norm)


_ORDER = {"negative": 0, "neutral": 1, "positive": 2}


def _ord(norm: str | None) -> int | None:
    return _ORDER.get(norm) if norm else None


@dataclass
class StockConsensus:
    ticker: str
    company_name: str | None
    status: str                                  # see consensus.STATUS_*
    positive: list[FirmOpinion] = field(default_factory=list)
    neutral: list[FirmOpinion] = field(default_factory=list)
    negative: list[FirmOpinion] = field(default_factory=list)
    stale: list[FirmOpinion] = field(default_factory=list)
    latest_price: float | None = None

    @property
    def firms_reporting(self) -> int:
        return len(self.positive) + len(self.neutral) + len(self.negative)