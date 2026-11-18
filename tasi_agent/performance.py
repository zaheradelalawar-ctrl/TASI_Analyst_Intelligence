"""Historical analyst performance with explicit benchmark tracking."""
from __future__ import annotations

from .db import price_on_or_after


def trading_calendar(conn) -> list[str]:
    """
    Ordered list of trading dates. Prefer index_prices when it is dense enough;
    otherwise fall back to distinct prices dates (reliable because the price
    table is populated for every stock every trading day).
    """
    idx_rows = conn.execute("SELECT date FROM index_prices ORDER BY date ASC").fetchall()
    if len(idx_rows) >= 30:
        return [r["date"] for r in idx_rows]
    price_rows = conn.execute(
        "SELECT DISTINCT date FROM prices ORDER BY date ASC"
    ).fetchall()
    return [r["date"] for r in price_rows]


def _index_price_on_or_after(conn, date_iso: str):
    row = conn.execute(
        "SELECT date, close FROM index_prices WHERE date>=? ORDER BY date ASC LIMIT 1",
        (date_iso,),
    ).fetchone()
    return (row["date"], float(row["close"])) if row else None


def _horizon_return(calendar, ticker, pub_date, horizon, conn):
    """
    Return (stock_return, index_return, benchmark_available) or None.

    index_return is None when either the entry or exit index price is missing.
    The caller decides how to treat that - the default is to exclude the
    record from hit-rate and excess-return calculations.
    """
    entry = price_on_or_after(conn, ticker, pub_date)
    if not entry:
        return None
    entry_date, entry_price = entry
    try:
        idx = calendar.index(entry_date)
    except ValueError:
        return None
    if idx + horizon >= len(calendar):
        return None
    exit_date = calendar[idx + horizon]
    exit_row = price_on_or_after(conn, ticker, exit_date)
    if not exit_row or exit_row[0] != exit_date:
        return None
    stock_ret = (exit_row[1] / entry_price - 1.0) * 100.0

    idx_entry = _index_price_on_or_after(conn, entry_date)
    idx_exit = _index_price_on_or_after(conn, exit_date)
    if idx_entry and idx_exit:
        index_ret = (idx_exit[1] / idx_entry[1] - 1.0) * 100.0
        benchmark_available = True
    else:
        index_ret = None
        benchmark_available = False

    return stock_ret, index_ret, benchmark_available


def evaluate(conn, cfg) -> list[dict]:
    horizons = cfg["performance"]["horizons"]
    calendar = trading_calendar(conn)
    if not calendar:
        return []

    rows = conn.execute(
        "SELECT r.id, r.ticker, r.publication_date, r.rating_norm, p.name AS provider_name "
        "FROM recommendations r JOIN providers p ON p.id=r.provider_id "
        "WHERE r.rating_norm IN ('positive','negative') AND r.validation_status != 'rejected' "
        "ORDER BY r.publication_date"
    ).fetchall()

    results = []
    for r in rows:
        for h in horizons:
            ret = _horizon_return(calendar, r["ticker"], r["publication_date"], h, conn)
            if not ret:
                continue
            stock_ret, index_ret, bench_ok = ret
            excess = (stock_ret - index_ret) if bench_ok else None
            if bench_ok:
                hit = (excess > 0) if r["rating_norm"] == "positive" else (excess < 0)
            else:
                hit = None
            results.append({
                "provider": r["provider_name"],
                "ticker": r["ticker"],
                "publication_date": r["publication_date"],
                "horizon": h,
                "stock_return": stock_ret,
                "index_return": index_ret,
                "excess": excess,
                "hit": hit,
                "benchmark_available": bench_ok,
            })
    return results


def aggregate(rows: list[dict], min_sample: int = 5) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["provider"], []).append(r)

    out = []
    for provider, items in buckets.items():
        n_total = len(items)
        with_bench = [i for i in items if i["benchmark_available"]]
        n_bench = len(with_bench)
        hits = sum(1 for i in with_bench if i["hit"])
        out.append({
            "provider": provider,
            "evaluated": n_total,
            "evaluated_vs_benchmark": n_bench,
            "hit_rate": (hits / n_bench * 100.0) if n_bench else None,
            "mean_excess": (sum(i["excess"] for i in with_bench) / n_bench) if n_bench else None,
            "publishable": n_bench >= min_sample,
        })
    out.sort(key=lambda x: -(x["mean_excess"] if x["mean_excess"] is not None else -999))
    return out