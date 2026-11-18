"""
Market regime detection for TASI.

Classifies the overall Saudi market before any stock is ranked. In a bearish
regime, no BUY NOW recommendations are issued regardless of individual setups.
"""
from __future__ import annotations

import sqlite3

import pandas as pd


REGIMES = ("STRONG_BULLISH", "BULLISH", "SIDEWAYS", "BEARISH", "HIGH_VOL_BEARISH")


def classify_regime(conn: sqlite3.Connection,
                    short_ma: int = 20,
                    long_ma: int = 50,
                    vol_window: int = 20) -> dict:
    """
    Return {'regime': str, 'confidence': float, 'metrics': dict}.

    Decision rules:
      STRONG_BULLISH  : price > MA20 > MA50 AND breadth_ma20 >= 0.6
                        AND realized_vol < long_vol * 1.2
      BULLISH         : price > MA50 AND breadth_ma20 >= 0.5
      HIGH_VOL_BEARISH: price < MA50 AND realized_vol > long_vol * 1.5
      BEARISH         : price < MA50 AND breadth_ma20 < 0.4
      SIDEWAYS        : everything else
    """
    idx_rows = conn.execute(
        "SELECT date, close FROM index_prices ORDER BY date ASC"
    ).fetchall()
    if len(idx_rows) < long_ma + vol_window:
        return {
            "regime": "SIDEWAYS",
            "confidence": 0.3,
            "metrics": {"insufficient_index_data": True,
                        "index_rows": len(idx_rows)},
        }

    idx = pd.DataFrame([dict(r) for r in idx_rows]).set_index("date")
    close = idx["close"]

    ma_short = close.rolling(short_ma, min_periods=short_ma).mean()
    ma_long = close.rolling(long_ma, min_periods=long_ma).mean()
    returns = close.pct_change()
    realized_vol = returns.rolling(vol_window, min_periods=vol_window).std().iloc[-1]
    long_vol = returns.rolling(vol_window * 6, min_periods=vol_window).std().iloc[-1]

    last_close = float(close.iloc[-1])
    last_short = float(ma_short.iloc[-1])
    last_long = float(ma_long.iloc[-1])

    breadth_ma20 = _compute_breadth(conn, 20)
    breadth_ma50 = _compute_breadth(conn, 50)

    regime = "SIDEWAYS"
    confidence = 0.5
    vol_ratio = (realized_vol / long_vol) if (long_vol and long_vol > 0) else 1.0

    if (last_close > last_short > last_long
            and breadth_ma20 >= 0.6
            and vol_ratio < 1.2):
        regime = "STRONG_BULLISH"
        confidence = min(0.95, 0.6 + (breadth_ma20 - 0.6) * 2.0)
    elif last_close > last_long and breadth_ma20 >= 0.5:
        regime = "BULLISH"
        confidence = min(0.85, 0.5 + (breadth_ma20 - 0.5) * 2.0)
    elif last_close < last_long and vol_ratio > 1.5:
        regime = "HIGH_VOL_BEARISH"
        confidence = min(0.9, 0.5 + (vol_ratio - 1.5))
    elif last_close < last_long and breadth_ma20 < 0.4:
        regime = "BEARISH"
        confidence = min(0.85, 0.5 + (0.4 - breadth_ma20) * 2.0)
    else:
        regime = "SIDEWAYS"
        confidence = 0.4 + abs(last_close - last_long) / last_long if last_long else 0.4

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "metrics": {
            "close": round(last_close, 2),
            "ma_short": round(last_short, 2),
            "ma_long": round(last_long, 2),
            "breadth_ma20": round(breadth_ma20, 3),
            "breadth_ma50": round(breadth_ma50, 3),
            "realized_vol_ann_pct": round(realized_vol * (252 ** 0.5) * 100, 2)
                                     if realized_vol else None,
            "vol_ratio": round(vol_ratio, 2),
        },
    }


def _compute_breadth(conn, ma_period: int) -> float:
    """Fraction of tracked tickers whose close is above their MA(ma_period)."""
    rows = conn.execute(
        "SELECT ticker, close FROM prices WHERE close IS NOT NULL ORDER BY ticker, date"
    ).fetchall()
    if not rows:
        return 0.0
    df = pd.DataFrame([dict(r) for r in rows])
    above = 0
    total = 0
    for ticker, group in df.groupby("ticker"):
        closes = group["close"]
        if len(closes) < ma_period:
            continue
        ma = closes.rolling(ma_period).mean().iloc[-1]
        if pd.isna(ma):
            continue
        if closes.iloc[-1] > ma:
            above += 1
        total += 1
    return above / total if total else 0.0


def regime_allows_new_longs(regime: str) -> bool:
    """True if the regime permits new long positions."""
    return regime in ("STRONG_BULLISH", "BULLISH")