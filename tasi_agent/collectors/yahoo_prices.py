"""
Yahoo Finance price collector with full OHLCV.

Uses auto_adjust=True so historical prices are adjusted for splits and
dividends. This is required for correct technical indicators - a 4-for-1
split would otherwise produce a fake -75% "drop" in moving averages.

Populates: prices (OHLCV per day), index_prices (TASI/benchmark),
splits (from Yahoo events + manual overrides).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .base import Collector

YAHOO_TICKER_TEMPLATE = "{ticker}.SR"

BENCHMARK_TICKERS = [
    ("^TASI.SR", "Tadawul All Share Index (Yahoo)"),
    ("TASI.SR",  "Tadawul (alternate Yahoo symbol)"),
    ("KSA",      "iShares MSCI Saudi Arabia ETF (US proxy)"),
]

HTTP_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")

MIN_SPLIT_RATIO = 0.5
MAX_SPLIT_RATIO = 10.0


def _is_plausible_split(ratio: float) -> bool:
    return MIN_SPLIT_RATIO <= ratio <= MAX_SPLIT_RATIO


def _safe_float(v):
    """Return float or None; treat NaN as None."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


class YahooPriceCollector(Collector):
    name = "yahoo_prices"
    source_type = "yahoo"

    def fetch(self):
        return []

    # ==================================================================
    # yfinance path (primary)
    # ==================================================================
    def _fetch_via_yfinance(self, yahoo_ticker: str, days: int = 400):
        """Return (bars, splits) where bars is a list of OHLCV dicts."""
        try:
            import yfinance as yf  # type: ignore
        except ImportError:
            return None, None

        try:
            t = yf.Ticker(yahoo_ticker)
            # auto_adjust=True -> OHLCV adjusted for splits and dividends
            hist = t.history(period=f"{days}d", auto_adjust=True, actions=True)
            if hist is None or hist.empty:
                return [], []

            bars: list[dict] = []
            for idx, row in hist.iterrows():
                d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                close = _safe_float(row.get("Close"))
                if close is None:
                    continue
                bars.append({
                    "date": d,
                    "open": _safe_float(row.get("Open")),
                    "high": _safe_float(row.get("High")),
                    "low": _safe_float(row.get("Low")),
                    "close": close,
                    "volume": int(row["Volume"]) if _safe_float(row.get("Volume")) else None,
                    "adj_close": close,  # auto_adjust=True means close IS adjusted
                })

            splits: list[tuple[str, float]] = []
            if "Stock Splits" in hist.columns:
                for idx, ratio in hist["Stock Splits"].items():
                    r = _safe_float(ratio)
                    if r and r > 0 and r != 1.0:
                        d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                        splits.append((d, r))

            return bars, splits
        except Exception as exc:                         # noqa: BLE001
            print(f"    [yf] {yahoo_ticker} error: {exc}")
            return None, None

    # ==================================================================
    # HTTP fallback
    # ==================================================================
    def _fetch_via_http(self, yahoo_ticker: str, days: int = 400):
        """Return (bars, splits). NOTE: raw prices, not adjusted."""
        params = {
            "period1": int(time.time()) - days * 86400,
            "period2": int(time.time()),
            "interval": "1d",
            "events": "split",
        }
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept": "application/json,text/plain,*/*",
        }
        for host in HTTP_HOSTS:
            url = f"https://{host}/v8/finance/chart/{yahoo_ticker}"
            try:
                r = requests.get(url, params=params, headers=headers, timeout=20)
            except requests.RequestException as exc:
                print(f"    [http] {host} {yahoo_ticker}: {exc}")
                continue
            if r.status_code != 200:
                continue
            if "json" not in (r.headers.get("content-type") or "").lower():
                continue
            try:
                payload = r.json()
            except ValueError:
                continue
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                continue

            ts = result[0].get("timestamp") or []
            quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            bars: list[dict] = []
            for i, stamp in enumerate(ts):
                close = _safe_float(closes[i]) if i < len(closes) else None
                if close is None:
                    continue
                d = datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
                bars.append({
                    "date": d,
                    "open": _safe_float(opens[i]) if i < len(opens) else None,
                    "high": _safe_float(highs[i]) if i < len(highs) else None,
                    "low": _safe_float(lows[i]) if i < len(lows) else None,
                    "close": close,
                    "volume": int(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
                    "adj_close": None,  # HTTP fallback is unadjusted
                })

            splits: list[tuple[str, float]] = []
            for s in (((result[0].get("events") or {}).get("splits")) or {}).values():
                try:
                    ratio = float(s["numerator"]) / float(s["denominator"])
                    d = dt.datetime.fromtimestamp(s["date"]).date().isoformat()
                    splits.append((d, ratio))
                except Exception:
                    continue

            return bars, splits
        return [], []

    # ==================================================================
    def _fetch_series(self, yahoo_ticker: str, days: int = 400):
        bars, splits = self._fetch_via_yfinance(yahoo_ticker, days)
        if bars is None:
            return self._fetch_via_http(yahoo_ticker, days)
        return bars, splits

    # ==================================================================
    def _store_splits(self, conn, ticker: str, splits: list[tuple[str, float]],
                      now: str) -> tuple[int, int]:
        kept, rejected = 0, 0
        for ex_date, ratio in splits:
            if not _is_plausible_split(ratio):
                rejected += 1
                continue
            conn.execute(
                "INSERT OR REPLACE INTO splits (ticker, ex_date, ratio, source, fetched_at) "
                "VALUES (?,?,?,?,?)",
                (ticker, ex_date, ratio, "yahoo", now),
            )
            kept += 1
        return kept, rejected

    def _load_manual_splits(self, conn, now: str) -> int:
        path = Path(self.cfg["_root"]) / "data" / "manual_splits.json"
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:                         # noqa: BLE001
            print(f"  [manual_splits] parse error: {exc}")
            return 0
        count = 0
        for ticker, events in data.items():
            if ticker.startswith("_") or not isinstance(events, list):
                continue
            for ev in events:
                try:
                    ex = str(ev["ex_date"]).strip()
                    ratio = float(ev["ratio"])
                    if not ex or ratio <= 0:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO splits "
                        "(ticker, ex_date, ratio, source, fetched_at) VALUES (?,?,?,?,?)",
                        (ticker, ex, ratio, "manual", now),
                    )
                    count += 1
                except Exception:
                    continue
        conn.commit()
        if count:
            print(f"  [manual_splits] loaded {count} override(s) from {path.name}")
        return count

    # ==================================================================
    def store(self, conn) -> dict:
        stats = {"index_days": 0, "index_source": None,
                 "tickers": 0, "bars_stored": 0,
                 "splits_kept": 0, "splits_rejected": 0,
                 "manual_splits": 0, "failed": 0,
                 "ohlcv_complete": 0}
        now = dt.datetime.now().isoformat(sep=" ", timespec="seconds")

        # ---- benchmark index -----------------------------------
        print(f"  fetching benchmark (trying {len(BENCHMARK_TICKERS)} tickers)")
        idx_bars: list[dict] = []
        chosen = None
        for bticker, blabel in BENCHMARK_TICKERS:
            print(f"    trying {bticker} ({blabel})")
            try:
                bars, _ = self._fetch_series(bticker, days=1200)
            except Exception as exc:                     # noqa: BLE001
                print(f"    {bticker}: error {exc}")
                continue
            n = len(bars or [])
            if n >= 30:
                print(f"    got {n} days from {bticker}")
                idx_bars = bars
                chosen = bticker
                break
            print(f"    {bticker}: only {n} days, trying next")

        if chosen:
            for b in idx_bars:
                conn.execute(
                    "INSERT OR REPLACE INTO index_prices (date, close) VALUES (?,?)",
                    (b["date"], b["close"]),
                )
                stats["index_days"] += 1
            conn.commit()
            stats["index_source"] = chosen
            print(f"    {stats['index_days']} index days written from {chosen}")

        # ---- per-ticker OHLCV ----------------------------------
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM recommendations ORDER BY ticker"
        ).fetchall()]
        print(f"  fetching OHLCV for {len(tickers)} tickers")

        for ticker in tickers:
            yahoo_ticker = YAHOO_TICKER_TEMPLATE.format(ticker=ticker)
            bars, splits = self._fetch_series(yahoo_ticker)
            if not bars:
                stats["failed"] += 1
                print(f"    {ticker}: NO DATA")
                continue

            complete_bars = 0
            for b in bars:
                conn.execute(
                    "INSERT OR REPLACE INTO prices "
                    "(ticker, date, open, high, low, close, volume, adj_close) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (ticker, b["date"], b["open"], b["high"], b["low"],
                     b["close"], b["volume"], b["adj_close"]),
                )
                stats["bars_stored"] += 1
                if all(b[k] is not None for k in ("open", "high", "low", "close", "volume")):
                    complete_bars += 1
            stats["ohlcv_complete"] += complete_bars

            kept, rejected = self._store_splits(conn, ticker, splits, now)
            stats["splits_kept"] += kept
            stats["splits_rejected"] += rejected
            conn.commit()
            stats["tickers"] += 1

            note = ""
            if kept:
                note += f", {kept} split(s)"
            if rejected:
                note += f", {rejected} split rejected"
            print(f"    {ticker}: {len(bars)} bars, {complete_bars} full OHLCV{note}")
            time.sleep(0.4)

        stats["manual_splits"] = self._load_manual_splits(conn, now)

        print(f"  done: {stats}")
        return stats