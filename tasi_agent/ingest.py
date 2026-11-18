"""
Ingestion pipeline: validation, normalisation, deduplication, persistence.

Deduplication rules implemented here:
  * identical firm + ticker + date + rating -> same record (fingerprint)
  * same firm + ticker + rating within 5 days -> one record, preferring the
    original source (provider site / PDF / API) over an aggregator page.

Aggregate sources (e.g. SAHMK consensus) are stored but marked so they never
count as an independent firm vote in the consensus engine.
"""
from __future__ import annotations

import datetime as dt

from .db import log
from .models import RawItem
from .normalize import is_withdrawal, normalize_rating
from .registry import get_or_create_provider
from .tickers import name_to_ticker
from .utils import now_iso, parse_date, sha

# Higher number = preferred when the same opinion appears on several sources.
SOURCE_PRIORITY = {
    "argaam_api": 6,      # structured API feed, most reliable
    "provider_site": 5,   # original research house page
    "pdf": 5,             # original research document
    "ir_page": 4,         # company investor-relations coverage page
    "argaam_pages": 3,    # Argaam HTML researchers-opinions tables
    "argaam": 2,          # legacy Argaam discovery adapter
    "manual": 1,          # hand-entered record
    "sahmk_api": 0,       # consensus aggregate - never a firm vote
    "yahoo": 0,           # consensus aggregate - never a firm vote
    "synthetic_demo": 0,  # demo only
}

# Sources that must never enter the independent-firm count.
AGGREGATE_SOURCES = {"sahmk_api", "yahoo", "synthetic_demo"}

NEAR_DUP_DAYS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _known_name_map(conn) -> dict[str, str]:
    """Learn ticker -> company names from records already in the database."""
    rows = conn.execute(
        "SELECT ticker, company_name FROM recommendations WHERE company_name IS NOT NULL"
    ).fetchall()
    return {r["ticker"]: r["company_name"] for r in rows if r["ticker"]}


def resolve_ticker(conn, ticker: str | None, company_name: str | None) -> str | None:
    """Return a 4-digit TASI ticker or None. Never guesses."""
    if ticker:
        cleaned = "".join(ch for ch in str(ticker) if ch.isdigit())
        if len(cleaned) == 4:
            return cleaned
    return name_to_ticker(company_name, _known_name_map(conn))


def _find_near_duplicate(conn, provider_id: int, ticker: str, rating_norm: str | None, pub_date: str):
    lo = (dt.date.fromisoformat(pub_date) - dt.timedelta(days=NEAR_DUP_DAYS)).isoformat()
    hi = (dt.date.fromisoformat(pub_date) + dt.timedelta(days=NEAR_DUP_DAYS)).isoformat()
    return conn.execute(
        "SELECT * FROM recommendations "
        "WHERE provider_id=? AND ticker=? AND ifnull(rating_norm,'')=? "
        "AND publication_date BETWEEN ? AND ? "
        "ORDER BY id ASC LIMIT 1",
        (provider_id, ticker, rating_norm or "", lo, hi),
    ).fetchone()


def _determine_status(item: RawItem, rating_norm: str | None, withdrawal: bool,
                      min_conf: float) -> str:
    """
    Validation status controls whether a record can vote in consensus.

      verified    -> has evidence and a clear rating; may vote
      partial     -> evidence exists but confidence is low or rating is missing;
                     stored but does not vote
      unverified  -> no rating at all; goes to review queue
      aggregate_check -> SAHMK/Yahoo style consensus snapshots; stored only
    """
    if item.source_type in AGGREGATE_SOURCES:
        return "aggregate_check"
    if withdrawal:
        return "verified" if item.source_url else "partial"
    if rating_norm is None:
        return "unverified"
    if float(item.confidence or 0.0) < min_conf:
        return "partial"
    if item.source_url or item.source_type in ("manual", "pdf", "argaam_api"):
        return "verified"
    return "partial"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def ingest_items(conn, items: list[RawItem], cfg: dict) -> dict:
    """
    Validate, normalise, deduplicate and store a batch of RawItem records.

    Returns a stats dict: seen, inserted, duplicate, promoted, rejected, review.
    """
    stats = {
        "seen": 0,
        "inserted": 0,
        "duplicate": 0,
        "promoted": 0,
        "rejected": 0,
        "review": 0,
    }
    min_conf = float(cfg["extraction"]["min_confidence"])

    for item in items:
        stats["seen"] += 1

        # ---- required fields: publication date and resolvable ticker ----
        pub_date = parse_date(item.publication_date)
        ticker = resolve_ticker(conn, item.ticker, item.company_name)

        if not pub_date or not ticker:
            stats["rejected"] += 1
            log(
                conn,
                "ingest",
                f"rejected: ticker={item.ticker!r} company={item.company_name!r} "
                f"date={item.publication_date!r} source={item.source_type}",
                level="warning",
            )
            continue

        # ---- rating normalisation ---------------------------------------
        withdrawal = bool(item.is_withdrawal) or is_withdrawal(item.rating_raw)
        if withdrawal:
            rating_norm, assumed = None, False
        else:
            rating_norm, assumed = normalize_rating(item.rating_raw)

        # ---- provider -----------------------------------------------------
        provider_id = get_or_create_provider(conn, item.provider_name or "Unknown Provider")

        # ---- validation status -------------------------------------------
        status = _determine_status(item, rating_norm, withdrawal, min_conf)

        # ---- fingerprint --------------------------------------------------
        fingerprint = sha(
            provider_id,
            ticker,
            pub_date,
            rating_norm or "WITHDRAWAL",
            item.target_price,
            item.source_type,
        )

        # ---- exact duplicate check ---------------------------------------
        if conn.execute(
            "SELECT id FROM recommendations WHERE fingerprint=?", (fingerprint,)
        ).fetchone():
            stats["duplicate"] += 1
            continue

        # ---- near duplicate across sources -------------------------------
        existing = _find_near_duplicate(conn, provider_id, ticker, rating_norm, pub_date)
        if existing:
            new_pri = SOURCE_PRIORITY.get(item.source_type, 0)
            old_pri = SOURCE_PRIORITY.get(existing["source_type"], 0)
            if new_pri > old_pri:
                # Promote the better-sourced copy.
                conn.execute(
                    "UPDATE recommendations SET "
                    "source_url=?, source_type=?, document_path=?, "
                    "extraction_confidence=?, validation_status=?, "
                    "rating_raw=?, target_price=?, price_at_publication=? "
                    "WHERE id=?",
                    (
                        item.source_url,
                        item.source_type,
                        item.document_path,
                        float(item.confidence or 0.0),
                        status,
                        item.rating_raw,
                        item.target_price if item.target_price is not None else existing["target_price"],
                        item.price_at_publication
                        if item.price_at_publication is not None
                        else existing["price_at_publication"],
                        existing["id"],
                    ),
                )
                conn.commit()
                stats["promoted"] += 1
            else:
                stats["duplicate"] += 1
            continue

        # ---- insert -------------------------------------------------------
        cur = conn.execute(
            """INSERT INTO recommendations
               (provider_id, ticker, company_name, rating_raw, rating_norm, rating_assumed,
                target_price, currency, price_at_publication, publication_date, source_url,
                source_type, document_path, raw_text, extraction_confidence, validation_status,
                is_withdrawal, fingerprint, collected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                provider_id,
                ticker,
                item.company_name,
                item.rating_raw,
                rating_norm,
                int(assumed),
                item.target_price,
                "SAR",
                item.price_at_publication,
                pub_date,
                item.source_url,
                item.source_type,
                item.document_path,
                (item.raw_text or "")[:20000] or None,
                float(item.confidence or 0.0),
                status,
                int(withdrawal),
                fingerprint,
                now_iso(),
            ),
        )
        rec_id = cur.lastrowid
        stats["inserted"] += 1

        # ---- review queue -------------------------------------------------
        if rating_norm is None and not withdrawal:
            conn.execute(
                "INSERT INTO review_queue (recommendation_id, reason, created_at) VALUES (?,?,?)",
                (rec_id, f"Unmapped rating: {item.rating_raw!r}", now_iso()),
            )
            stats["review"] += 1

    conn.commit()
    log(conn, "ingest", f"ingest complete: {stats}")
    return stats