"""
Independent-firm consensus engine (split-aware, material-dissent-aware).

Rules:
  1. Independence is at the FIRM level, not analyst level.
  2. Only the most recent recommendation per (firm, stock) counts.
  3. Only recommendations inside the freshness window qualify.
  4. Dissent is material when:
       - negatives >= positives, OR
       - positives < strong threshold AND negatives >= 2, OR
       - any negative's split-adjusted target is BELOW the current price.
     Material dissent => CONFLICTING. Non-material negatives with a passing
     positive threshold => CONFIRMED_WITH_DISSENT. Clean => STRONG or CONFIRMED.
  5. Split adjustment: any split with ex_date AFTER the publication date is
     applied to the target before comparing with the current price.
"""
from __future__ import annotations

import datetime as dt

from .db import latest_price
from .models import FirmOpinion, StockConsensus

STATUS_STRONG = "STRONG_CONSENSUS"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CONFIRMED_DISSENT = "CONFIRMED_WITH_DISSENT"
STATUS_SINGLE = "SINGLE_OPINION"
STATUS_CONFLICTING = "CONFLICTING_OPINIONS"
STATUS_NONE = "NO_CONFIRMATION"

STATUS_LABEL = {
    STATUS_STRONG: "Strong consensus (4+ firms)",
    STATUS_CONFIRMED: "Confirmed (2+ firms)",
    STATUS_CONFIRMED_DISSENT: "Confirmed with dissent",
    STATUS_SINGLE: "Single opinion",
    STATUS_CONFLICTING: "Conflicting opinions",
    STATUS_NONE: "No confirmation",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_stances(conn, as_of: str, allowed_statuses: list[str]):
    placeholders = ",".join("?" for _ in allowed_statuses)
    sql = f"""
        SELECT r.*, p.name AS provider_name
        FROM recommendations r
        JOIN providers p ON p.id = r.provider_id
        WHERE r.validation_status IN ({placeholders})
          AND r.publication_date <= ?
        ORDER BY r.provider_id, r.ticker, r.publication_date DESC,
                 r.collected_at DESC, r.id DESC
    """
    rows = conn.execute(sql, (*allowed_statuses, as_of)).fetchall()
    current: dict[tuple[int, str], dict] = {}
    history: dict[tuple[int, str], list[dict]] = {}
    for r in rows:
        key = (r["provider_id"], r["ticker"])
        history.setdefault(key, []).append(dict(r))
        if key not in current:
            current[key] = dict(r)
    return current, history


# ---------------------------------------------------------------------------
# Split adjustment
# ---------------------------------------------------------------------------
def _cumulative_split_ratio(conn, ticker: str, pub_date: str) -> float:
    """Product of split ratios with ex_date > pub_date. 1.0 if none."""
    if conn is None:
        return 1.0
    try:
        rows = conn.execute(
            "SELECT ratio FROM splits WHERE ticker=? AND ex_date > ? ORDER BY ex_date",
            (ticker, pub_date),
        ).fetchall()
    except Exception:
        return 1.0
    ratio = 1.0
    for r in rows:
        try:
            v = float(r["ratio"])
            if v > 0:
                ratio *= v
        except Exception:
            continue
    return ratio


def split_adjusted_target(conn, ticker: str, pub_date: str,
                          raw_target: float | None) -> tuple[float | None, float]:
    """Return (adjusted_target, cumulative_ratio)."""
    if raw_target is None:
        return None, 1.0
    r = _cumulative_split_ratio(conn, ticker, pub_date)
    if r <= 0:
        return raw_target, 1.0
    return raw_target / r, r


# ---------------------------------------------------------------------------
# Material negative detection (shared by classification and report)
# ---------------------------------------------------------------------------
def material_negatives(conn, sc: StockConsensus) -> list[dict]:
    """
    Return the list of negative opinions whose split-adjusted target is below
    the current price. Empty list means no material downside dissent.

    Each entry contains the raw target, the adjusted target, the split ratio,
    the current price, and the implied downside.
    """
    if not sc.latest_price or not sc.negative:
        return []
    out: list[dict] = []
    for o in sc.negative:
        if not o.target_price:
            continue
        adjusted, ratio = split_adjusted_target(conn, sc.ticker, o.publication_date, o.target_price)
        if adjusted is None or adjusted >= sc.latest_price:
            continue
        downside = (adjusted / sc.latest_price - 1.0) * 100.0
        out.append({
            "firm": o.provider_name,
            "rating": o.rating_raw,
            "target_raw": o.target_price,
            "target_adj": adjusted,
            "split_ratio": ratio,
            "current_price": sc.latest_price,
            "downside_pct": downside,
            "publication_date": o.publication_date,
            "source_url": o.source_url,
        })
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def _classify(positive: int, negative: int, material_negs: int, cfg: dict) -> str:
    """
    Rules, in order:

      1. No positives and no negatives                -> NONE
      2. No positives, negatives present              -> CONFLICTING (bearish only)
      3. Material dissent:
         - negatives >= positives                     -> CONFLICTING
         - positives < strong AND negatives >= 2      -> CONFLICTING
         - any material negative (target < price)     -> CONFLICTING
      4. Positives >= threshold, negatives present    -> CONFIRMED_WITH_DISSENT
      5. Positives >= threshold, no negatives:
         - >= strong                                  -> STRONG
         - >= confirm                                 -> CONFIRMED
      6. Positives < threshold                        -> SINGLE
    """
    confirm = int(cfg["consensus"]["confirm_threshold"])
    strong = int(cfg["consensus"]["strong_threshold"])

    if positive == 0:
        return STATUS_CONFLICTING if negative > 0 else STATUS_NONE

    dissent_material = (
        negative >= positive
        or (positive < strong and negative >= 2)
        or material_negs >= 1
    )
    if dissent_material:
        return STATUS_CONFLICTING

    if positive >= confirm:
        if negative > 0:
            return STATUS_CONFIRMED_DISSENT
        return STATUS_STRONG if positive >= strong else STATUS_CONFIRMED

    return STATUS_SINGLE


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def build_consensus(conn, cfg: dict, as_of: str | None = None) -> list[StockConsensus]:
    as_of = as_of or dt.date.today().isoformat()
    freshness = int(cfg["project"]["freshness_days"])
    cutoff = (dt.date.fromisoformat(as_of) - dt.timedelta(days=freshness)).isoformat()
    allowed = cfg["consensus"]["allowed_statuses"] if cfg["consensus"]["require_verified_only"] \
        else ["verified", "partial", "unverified", "rejected"]

    current, history = _load_stances(conn, as_of, allowed)
    by_ticker: dict[str, StockConsensus] = {}

    for (provider_id, ticker), rec in current.items():
        sc = by_ticker.setdefault(
            ticker, StockConsensus(ticker=ticker, company_name=rec["company_name"], status=STATUS_NONE)
        )
        if rec["company_name"] and not sc.company_name:
            sc.company_name = rec["company_name"]

        prior = [h for h in history.get((provider_id, ticker), []) if h["id"] != rec["id"]]
        prev_norm = prior[0]["rating_norm"] if prior else None

        opinion = FirmOpinion(
            rec_id=rec["id"], provider_id=provider_id, provider_name=rec["provider_name"],
            ticker=ticker, company_name=rec["company_name"],
            rating_raw=rec["rating_raw"] or "", rating_norm=rec["rating_norm"] or "unknown",
            rating_assumed=bool(rec["rating_assumed"]),
            target_price=rec["target_price"], price_at_publication=rec["price_at_publication"],
            publication_date=rec["publication_date"], source_url=rec["source_url"],
            source_type=rec["source_type"], confidence=rec["extraction_confidence"] or 0.0,
            validation_status=rec["validation_status"], prev_rating_norm=prev_norm,
        )

        if rec["is_withdrawal"]:
            continue
        if rec["publication_date"] < cutoff:
            sc.stale.append(opinion)
            continue

        if opinion.rating_norm == "positive":
            sc.positive.append(opinion)
        elif opinion.rating_norm == "negative":
            sc.negative.append(opinion)
        elif opinion.rating_norm == "neutral":
            sc.neutral.append(opinion)

    results: list[StockConsensus] = []
    for sc in by_ticker.values():
        sc.positive.sort(key=lambda o: o.publication_date, reverse=True)
        sc.neutral.sort(key=lambda o: o.publication_date, reverse=True)
        sc.negative.sort(key=lambda o: o.publication_date, reverse=True)
        sc.latest_price = latest_price(conn, sc.ticker)
        mat = material_negatives(conn, sc)
        sc.status = _classify(len(sc.positive), len(sc.negative), len(mat), cfg)
        results.append(sc)

    order = {
        STATUS_STRONG: 0,
        STATUS_CONFIRMED: 1,
        STATUS_CONFIRMED_DISSENT: 2,
        STATUS_CONFLICTING: 3,
        STATUS_SINGLE: 4,
        STATUS_NONE: 5,
    }
    results.sort(key=lambda s: (order[s.status], -len(s.positive), s.ticker))
    return results


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------
def highlighted(results: list[StockConsensus]) -> list[StockConsensus]:
    """Section A: Strong and clean confirmations (no negative firms)."""
    return [s for s in results if s.status in (STATUS_STRONG, STATUS_CONFIRMED)]


def confirmed_with_dissent(results: list[StockConsensus]) -> list[StockConsensus]:
    """Section B: Confirmed with a non-material dissenting view."""
    return [s for s in results if s.status == STATUS_CONFIRMED_DISSENT]


def conflicting(results: list[StockConsensus]) -> list[StockConsensus]:
    """Section C: Material disagreement or negative-dominated."""
    return [s for s in results if s.status == STATUS_CONFLICTING]


def unconfirmed(results: list[StockConsensus]) -> list[StockConsensus]:
    """Section D: Single opinions and no-confirmation stocks."""
    return [s for s in results if s.status in (STATUS_SINGLE, STATUS_NONE)]


# ---------------------------------------------------------------------------
# Valuation summary (unchanged interface, split-aware)
# ---------------------------------------------------------------------------
def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def valuation_summary(sc: StockConsensus, conn=None) -> dict:
    entries = []
    any_adjusted = False
    for o in sc.positive:
        adj, ratio = split_adjusted_target(conn, sc.ticker, o.publication_date, o.target_price)
        if ratio > 1.0:
            any_adjusted = True
        entries.append({
            "firm": o.provider_name,
            "raw_target": o.target_price,
            "adj_target": adj,
            "price_pub": o.price_at_publication,
            "pub_date": o.publication_date,
            "split_ratio": ratio,
        })

    out = {
        "n_targets": sum(1 for e in entries if e["raw_target"] is not None),
        "median_target": None,
        "median_target_adj": None,
        "min_target": None,
        "max_target": None,
        "dispersion_pct": None,
        "upside_at_publication": None,
        "upside_now": None,
        "split_adjusted": any_adjusted,
        "per_firm": [],
    }
    if not entries:
        return out

    raw_vals = [e["raw_target"] for e in entries if e["raw_target"] is not None]
    adj_vals = [e["adj_target"] for e in entries if e["adj_target"] is not None]
    out["median_target"] = _median(raw_vals)
    out["median_target_adj"] = _median(adj_vals)
    if adj_vals:
        out["min_target"] = min(adj_vals)
        out["max_target"] = max(adj_vals)
        if out["median_target_adj"]:
            out["dispersion_pct"] = ((out["max_target"] - out["min_target"])
                                     / out["median_target_adj"] * 100.0)

    pub_up = []
    now_up = []
    for e in entries:
        up_pub = None
        up_now = None
        if e["raw_target"] and e["price_pub"]:
            up_pub = (e["raw_target"] / e["price_pub"] - 1.0) * 100.0
            pub_up.append(up_pub)
        if e["adj_target"] and sc.latest_price:
            up_now = (e["adj_target"] / sc.latest_price - 1.0) * 100.0
            now_up.append(up_now)
        out["per_firm"].append({
            "firm": e["firm"],
            "target": e["raw_target"],
            "target_adj": e["adj_target"],
            "split_ratio": e["split_ratio"],
            "upside_at_publication": up_pub,
            "upside_now": up_now,
        })
    if pub_up:
        out["upside_at_publication"] = sum(pub_up) / len(pub_up)
    if now_up:
        out["upside_now"] = sum(now_up) / len(now_up)
    return out