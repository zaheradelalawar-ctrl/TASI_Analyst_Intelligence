"""
Watchlist monitor: detects material changes on specific stocks and produces
deterministic analyst-style alerts. No LLM, no invention.

Event types:
  new_rec            - first-ever recommendation from a firm on this stock
  rating_change      - a firm changed rating vs its own prior view
  target_change      - a firm moved its target by >= 5% vs its own prior
  consensus_change   - overall consensus status moved (A->C, B->C, etc.)
  price_move         - latest close vs 5-day average beyond threshold
  material_dissent   - a negative target newly fell below the current price

Dedup rule: an event with the same (ticker, event_type, title, occurred_at)
is never persisted twice. Consensus and material-dissent events use the
current DATE as occurred_at, not a live timestamp, so a stable day cannot
re-emit the same event on every intra-day run.

IMPORTANT: this file must remain ASCII-clean. Do NOT use Unicode arrows in
any generated title - PowerShell's default Set-Content reads/writes cp1252
and permanently corrupts UTF-8 multi-byte characters. Use "->" instead.
"""
from __future__ import annotations

import datetime as dt
import json

from .consensus import (STATUS_CONFIRMED, STATUS_CONFIRMED_DISSENT,
                        STATUS_LABEL, STATUS_STRONG,
                        build_consensus, material_negatives)
from .db import log
from .models import _ord
from .utils import now_iso


SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
def add_to_watchlist(conn, ticker: str, notes: str = "",
                     alert_level: str = "medium") -> bool:
    ticker = str(ticker).strip().zfill(4)
    row = conn.execute(
        "SELECT 1 FROM recommendations WHERE ticker=? LIMIT 1", (ticker,)
    ).fetchone()
    if not row:
        return False
    company_row = conn.execute(
        "SELECT company_name FROM recommendations "
        "WHERE ticker=? AND company_name IS NOT NULL "
        "ORDER BY publication_date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    company_name = company_row["company_name"] if company_row else None
    conn.execute(
        "INSERT OR REPLACE INTO watchlist "
        "(ticker, company_name, added_at, notes, alert_level) "
        "VALUES (?,?,?,?,?)",
        (ticker, company_name, now_iso(), notes, alert_level),
    )
    conn.commit()
    return True


def remove_from_watchlist(conn, ticker: str) -> bool:
    cur = conn.execute("DELETE FROM watchlist WHERE ticker=?",
                       (str(ticker).strip().zfill(4),))
    conn.commit()
    return cur.rowcount > 0


def list_watchlist(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM watchlist ORDER BY ticker"
    ).fetchall()]


# ---------------------------------------------------------------------------
# Event persistence (with dedup)
# ---------------------------------------------------------------------------
def _persist_event(conn, ticker: str, event_type: str, severity: str,
                   title: str, detail: str, payload: dict | None = None,
                   source_url: str | None = None,
                   occurred_at: str | None = None) -> int | None:
    occurred_at = occurred_at or now_iso()

    existing = conn.execute(
        "SELECT id FROM stock_events "
        "WHERE ticker=? AND event_type=? AND title=? AND occurred_at=? "
        "LIMIT 1",
        (ticker, event_type, title, occurred_at),
    ).fetchone()
    if existing:
        return None

    cur = conn.execute(
        "INSERT INTO stock_events "
        "(ticker, event_type, severity, title, detail, payload_json, source_url, "
        " occurred_at, detected_at, notified) "
        "VALUES (?,?,?,?,?,?,?,?,?,0)",
        (ticker, event_type, severity, title, detail,
         json.dumps(payload) if payload else None,
         source_url, occurred_at, now_iso()),
    )
    return cur.lastrowid


def _last_run(conn) -> str:
    row = conn.execute(
        "SELECT run_at FROM monitor_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["run_at"]
    return (dt.datetime.now() - dt.timedelta(days=7)).isoformat(sep=" ", timespec="seconds")


def _baseline_date(baseline_iso: str) -> str:
    base = baseline_iso[:10]
    try:
        return (dt.date.fromisoformat(base) - dt.timedelta(days=1)).isoformat()
    except ValueError:
        return base


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------
def _detect_recommendation_events(conn, ticker: str,
                                  baseline_iso: str) -> list[int]:
    ids: list[int] = []
    new_rows = conn.execute(
        "SELECT r.*, p.name AS provider_name "
        "FROM recommendations r JOIN providers p ON p.id=r.provider_id "
        "WHERE r.ticker=? AND r.collected_at > ? AND r.is_withdrawal=0 "
        "ORDER BY r.publication_date ASC, r.id ASC",
        (ticker, baseline_iso),
    ).fetchall()

    for r in new_rows:
        prior = conn.execute(
            "SELECT * FROM recommendations "
            "WHERE provider_id=? AND ticker=? AND id<? "
            "ORDER BY publication_date DESC, id DESC LIMIT 1",
            (r["provider_id"], ticker, r["id"]),
        ).fetchone()

        if not prior:
            if r["rating_norm"] == "negative":
                sev = "high"
            elif r["rating_norm"] == "positive":
                sev = "medium"
            else:
                sev = "low"
            target = (f", target SAR {r['target_price']:.2f}"
                      if r["target_price"] else "")
            title = f"New {r['rating_raw']} from {r['provider_name']}"
            detail = (f"{r['provider_name']} initiated coverage on {ticker} "
                      f"with a {r['rating_raw']} rating{target} "
                      f"on {r['publication_date']}.")
            eid = _persist_event(
                conn, ticker, "new_rec", sev, title, detail,
                payload={"rating": r["rating_raw"], "target": r["target_price"],
                         "provider": r["provider_name"]},
                source_url=r["source_url"], occurred_at=r["publication_date"],
            )
            if eid is not None:
                ids.append(eid)
            continue

        if prior["rating_norm"] != r["rating_norm"]:
            from_ord = _ord(prior["rating_norm"]) or 1
            to_ord = _ord(r["rating_norm"]) or 1
            notches = abs(to_ord - from_ord)
            if r["rating_norm"] == "negative":
                sev = "high"
            elif prior["rating_norm"] == "negative" and r["rating_norm"] != "negative":
                sev = "high"
            elif notches >= 2:
                sev = "high"
            else:
                sev = "medium"
            direction = "upgraded" if to_ord > from_ord else "downgraded"
            target = (f", target SAR {r['target_price']:.2f}"
                      if r["target_price"] else "")
            title = f"{direction.title()} to {r['rating_raw']} by {r['provider_name']}"
            detail = (f"{r['provider_name']} {direction} {ticker} from "
                      f"{prior['rating_raw']} to {r['rating_raw']} "
                      f"on {r['publication_date']}{target}.")
            eid = _persist_event(
                conn, ticker, "rating_change", sev, title, detail,
                payload={"from": prior["rating_raw"], "to": r["rating_raw"],
                         "provider": r["provider_name"],
                         "target": r["target_price"]},
                source_url=r["source_url"], occurred_at=r["publication_date"],
            )
            if eid is not None:
                ids.append(eid)

        if (prior["target_price"] and r["target_price"]
                and prior["target_price"] > 0):
            pct = (r["target_price"] / prior["target_price"] - 1.0) * 100.0
            if abs(pct) >= 5.0:
                if abs(pct) >= 20:
                    sev = "high"
                elif abs(pct) >= 10:
                    sev = "medium"
                else:
                    sev = "low"
                direction = "raised" if pct > 0 else "lowered"
                title = f"Target {direction} {pct:+.1f}% by {r['provider_name']}"
                detail = (f"{r['provider_name']} {direction} the {ticker} target "
                          f"from SAR {prior['target_price']:.2f} "
                          f"to SAR {r['target_price']:.2f} ({pct:+.1f}%) "
                          f"on {r['publication_date']}.")
                eid = _persist_event(
                    conn, ticker, "target_change", sev, title, detail,
                    payload={"from": prior["target_price"],
                             "to": r["target_price"], "pct": pct},
                    source_url=r["source_url"], occurred_at=r["publication_date"],
                )
                if eid is not None:
                    ids.append(eid)

    return ids


def _detect_consensus_change(conn, cfg: dict, ticker: str,
                             baseline_iso: str) -> list[int]:
    before_date = _baseline_date(baseline_iso)
    today = dt.date.today().isoformat()

    before = next((s for s in build_consensus(conn, cfg, before_date)
                   if s.ticker == ticker), None)
    after = next((s for s in build_consensus(conn, cfg, today)
                  if s.ticker == ticker), None)
    if not before or not after or before.status == after.status:
        return []

    clean_before = before.status in (STATUS_STRONG, STATUS_CONFIRMED)
    clean_after = after.status in (STATUS_STRONG, STATUS_CONFIRMED)

    if clean_before and not clean_after:
        sev = "high"
    elif not clean_before and clean_after:
        sev = "medium"
    else:
        sev = "medium"

    title = (f"Consensus: {STATUS_LABEL[before.status]} "
             f"-> {STATUS_LABEL[after.status]}")
    detail = (f"{ticker} consensus moved from '{STATUS_LABEL[before.status]}' "
              f"to '{STATUS_LABEL[after.status]}'. Counts: before "
              f"{len(before.positive)}P/{len(before.neutral)}N/{len(before.negative)}X -> "
              f"after {len(after.positive)}P/{len(after.neutral)}N/{len(after.negative)}X.")
    eid = _persist_event(
        conn, ticker, "consensus_change", sev, title, detail,
        payload={"before": before.status, "after": after.status,
                 "before_counts": [len(before.positive), len(before.neutral),
                                   len(before.negative)],
                 "after_counts": [len(after.positive), len(after.neutral),
                                  len(after.negative)]},
        occurred_at=today,
    )
    return [eid] if eid is not None else []


def _detect_material_dissent(conn, cfg: dict, ticker: str,
                             baseline_iso: str) -> list[int]:
    before_date = _baseline_date(baseline_iso)
    today = dt.date.today().isoformat()

    before_sc = next((s for s in build_consensus(conn, cfg, before_date)
                      if s.ticker == ticker), None)
    after_sc = next((s for s in build_consensus(conn, cfg, today)
                     if s.ticker == ticker), None)
    if not after_sc:
        return []

    before_set = {(m["firm"], round(m["target_adj"], 2))
                  for m in material_negatives(conn, before_sc)
                  } if before_sc else set()
    after_list = material_negatives(conn, after_sc)
    new_dissent = [m for m in after_list
                   if (m["firm"], round(m["target_adj"], 2)) not in before_set]

    if not new_dissent:
        return []

    lines = [f"- {m['firm']}: target SAR {m['target_adj']:.2f} "
             f"vs current SAR {m['current_price']:.2f} "
             f"({m['downside_pct']:+.1f}%)"
             for m in new_dissent]
    title = (f"Material dissent: {len(new_dissent)} new negative "
             f"target{'s' if len(new_dissent) != 1 else ''} below price")
    detail = "Negative targets now below the current price:\n" + "\n".join(lines)
    eid = _persist_event(
        conn, ticker, "material_dissent", "high", title, detail,
        payload={"targets": new_dissent},
        source_url=new_dissent[0].get("source_url"),
        occurred_at=today,
    )
    return [eid] if eid is not None else []


def _detect_price_moves(conn, ticker: str, threshold_pct: float = 3.0) -> list[int]:
    rows = conn.execute(
        "SELECT date, close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 6",
        (ticker,),
    ).fetchall()
    if len(rows) < 6:
        return []
    latest = rows[0]
    prev5 = rows[1:6]
    avg5 = sum(r["close"] for r in prev5) / 5.0
    if avg5 <= 0:
        return []
    pct = (latest["close"] / avg5 - 1.0) * 100.0
    if abs(pct) < threshold_pct:
        return []

    if abs(pct) >= 10:
        sev = "high"
    elif abs(pct) >= 5:
        sev = "medium"
    else:
        sev = "low"

    direction = "surged" if pct > 0 else "dropped"
    title = f"Price {direction} {pct:+.1f}% vs 5-day average"
    detail = (f"{ticker} closed at SAR {latest['close']:.2f} on {latest['date']} "
              f"versus 5-day average of SAR {avg5:.2f} ({pct:+.1f}%).")
    eid = _persist_event(
        conn, ticker, "price_move", sev, title, detail,
        payload={"close": latest["close"], "avg5": avg5, "pct": pct,
                 "date": latest["date"]},
        occurred_at=latest["date"],
    )
    return [eid] if eid is not None else []


# ---------------------------------------------------------------------------
# Monitor run
# ---------------------------------------------------------------------------
def run_monitor(conn, cfg: dict, dry_run: bool = False) -> dict:
    watchlist = list_watchlist(conn)
    if not watchlist:
        print("Watchlist is empty. Add stocks with: run.py watch add TICKER")
        return {"tickers": 0, "events": 0,
                "alerts": {"high": 0, "medium": 0, "low": 0}, "errors": []}

    global_baseline = _last_run(conn)
    print(f"Monitor baseline: {global_baseline}")
    print(f"Watching {len(watchlist)} stock(s)")

    all_event_ids: list[int] = []
    errors: list[str] = []

    for w in watchlist:
        ticker = w["ticker"]
        baseline = max(global_baseline, w["added_at"])
        try:
            ids: list[int] = []
            ids.extend(_detect_recommendation_events(conn, ticker, baseline))
            ids.extend(_detect_consensus_change(conn, cfg, ticker, baseline))
            ids.extend(_detect_material_dissent(conn, cfg, ticker, baseline))
            ids.extend(_detect_price_moves(conn, ticker))
            all_event_ids.extend(ids)
            if ids:
                print(f"  {ticker}: {len(ids)} new event(s)")
        except Exception as exc:                         # noqa: BLE001
            errors.append(f"{ticker}: {exc}")
            print(f"  {ticker}: ERROR {exc}")

    conn.commit()

    unnotified = conn.execute(
        "SELECT severity, COUNT(*) AS c FROM stock_events WHERE notified=0 "
        "GROUP BY severity"
    ).fetchall()
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in unnotified:
        counts[row["severity"]] = row["c"]

    if not dry_run and all_event_ids:
        conn.execute(
            "INSERT INTO monitor_runs "
            "(run_at, tickers_checked, events_created, "
            " alerts_high, alerts_medium, alerts_low, errors) "
            "VALUES (?,?,?,?,?,?,?)",
            (now_iso(), len(watchlist), len(all_event_ids),
             counts["high"], counts["medium"], counts["low"],
             "\n".join(errors) if errors else None),
        )
        conn.commit()

    summary = {"tickers": len(watchlist), "events": len(all_event_ids),
               "alerts": counts, "errors": errors}
    log(conn, "monitor", f"monitor complete: {summary}")
    return summary


def pending_alerts(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM stock_events WHERE notified=0 "
        "ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
        "occurred_at DESC"
    ).fetchall()]


def mark_notified(conn, event_id: int) -> None:
    conn.execute("UPDATE stock_events SET notified=1 WHERE id=?", (event_id,))
    conn.commit()


def dossier(conn, ticker: str, limit: int = 100) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM stock_events WHERE ticker=? "
        "ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (str(ticker).strip().zfill(4), limit),
    ).fetchall()]