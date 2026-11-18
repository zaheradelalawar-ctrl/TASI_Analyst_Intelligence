"""
Technical setup detection for TASI daily swing trading.

Six setups, each with unambiguous rules. Every setup returns:
  - boolean Series (True where the setup fires that day)
  - plan dict with entry / stop / targets when fired

No look-ahead: each firing uses only data up to and including that row.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_volume_breakout(df: pd.DataFrame, lookback: int = 20,
                           volume_mult: float = 1.5) -> pd.Series:
    """Close > prior (lookback - 1) day high, AND volume > volume_mult × prior avg."""
    closes = df["close"]
    prior_high = closes.shift(1).rolling(lookback - 1, min_periods=lookback - 1).max()
    prior_avg_vol = (df["volume"].shift(1)
                     .rolling(lookback, min_periods=lookback).mean())
    price_break = closes > prior_high
    vol_confirm = df["volume"] > (prior_avg_vol * volume_mult)
    return (price_break & vol_confirm).fillna(False).astype(bool)


def detect_ema20_pullback(df: pd.DataFrame) -> pd.Series:
    """
    Uptrend: close > EMA50 AND EMA20 > EMA50.
    Pullback: yesterday's low touched EMA20 (within 2%).
    Reclaim: today close > EMA20 AND close > yesterday's close.
    """
    closes = df["close"]
    lows = df["low"]
    ema20 = df["ema_20"]
    ema50 = df["ema_50"]

    uptrend = (closes > ema50) & (ema20 > ema50)
    pullback = lows.shift(1) <= (ema20.shift(1) * 1.02)
    reclaim = (closes > ema20) & (closes > closes.shift(1))

    return (uptrend & pullback & reclaim).fillna(False).astype(bool)


def detect_macd_crossover(df: pd.DataFrame, lookback: int = 3) -> pd.Series:
    """MACD crossed above signal within last N days, price above EMA50, volume ok."""
    macd = df["macd"]
    signal = df["macd_signal"]
    closes = df["close"]
    ema50 = df["ema_50"]
    vol = df["volume"]
    avg_vol = vol.shift(1).rolling(20, min_periods=20).mean()

    crossed_up = (macd > signal) & (macd.shift(1) <= signal.shift(1))
    recent_cross = crossed_up.rolling(lookback, min_periods=1).max().astype(bool)
    above_ema = closes > ema50
    vol_ok = vol > avg_vol

    return (recent_cross & above_ema & vol_ok).fillna(False).astype(bool)


def detect_rsi_recovery(df: pd.DataFrame, oversold: float = 30,
                        lookback: int = 5) -> pd.Series:
    """RSI was below `oversold` within last N days, now above, close up."""
    rsi = df["rsi_14"]
    closes = df["close"]

    was_oversold = (rsi < oversold).rolling(lookback, min_periods=1).max().astype(bool)
    now_recovered = rsi >= oversold
    upward = closes > closes.shift(1)

    return (was_oversold & now_recovered & upward).fillna(False).astype(bool)


def detect_20d_high_volume(df: pd.DataFrame) -> pd.Series:
    """Close is highest of last 20 sessions, volume above prior 20-day avg."""
    closes = df["close"]
    vol = df["volume"]
    rolling_high = closes.rolling(20, min_periods=20).max()
    new_high = closes == rolling_high
    prior_avg_vol = vol.shift(1).rolling(20, min_periods=20).mean()
    vol_ok = vol > prior_avg_vol

    return (new_high & vol_ok).fillna(False).astype(bool)


def detect_bollinger_squeeze_breakout(df: pd.DataFrame,
                                      squeeze_pctile: float = 20,
                                      squeeze_window: int = 60,
                                      recent_days: int = 10) -> pd.Series:
    """
    Within last N days, BB width was in bottom X percentile of prior 60 days.
    Today: close > upper Bollinger band.
    """
    width = df["bb_width"]
    closes = df["close"]
    upper = df["bb_upper"]

    pctile_threshold = width.rolling(squeeze_window,
                                      min_periods=squeeze_window).quantile(
        squeeze_pctile / 100.0)
    in_squeeze = width <= pctile_threshold
    recent_squeeze = in_squeeze.rolling(recent_days, min_periods=1).max().astype(bool)
    breakout = closes > upper

    return (recent_squeeze & breakout).fillna(False).astype(bool)


def setup_details(df: pd.DataFrame, i: int, setup_name: str,
                  lookback: int = 20) -> dict:
    """
    Generate trade plan for the setup that fired at row index i.

    Stop = min(2×ATR below entry, prior-20-day low × 0.99) — the LOWER of the
    two (more conservative).
    Targets at 2R and 3R.
    """
    row = df.iloc[i]
    closes = df["close"]
    lows = df["low"]

    recent_low = float(lows.iloc[max(0, i - lookback + 1):i + 1].min())

    atr_val = row.get("atr_14")
    if pd.isna(atr_val) or atr_val is None or atr_val <= 0:
        atr_val = max(float(row["high"] - row["low"]), float(row["close"]) * 0.02)

    entry = float(row["close"])
    atr_stop = entry - 2.0 * float(atr_val)
    structural_stop = recent_low * 0.99
    stop = min(atr_stop, structural_stop)

    risk = entry - stop
    if risk <= 0:
        risk = entry * 0.02
        stop = entry - risk

    return {
        "setup": setup_name,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target_1": round(entry + 2 * risk, 2),
        "target_2": round(entry + 3 * risk, 2),
        "risk_per_share": round(risk, 2),
        "risk_reward_1": 2.0,
        "risk_reward_2": 3.0,
        "atr": round(float(atr_val), 2),
        "recent_low": round(recent_low, 2),
        "close_date": str(df.index[i]),
    }


SETUPS = [
    ("volume_breakout",    detect_volume_breakout),
    ("ema20_pullback",     detect_ema20_pullback),
    ("macd_crossover",     detect_macd_crossover),
    ("rsi_recovery",       detect_rsi_recovery),
    ("20d_high_volume",    detect_20d_high_volume),
    ("bollinger_squeeze",  detect_bollinger_squeeze_breakout),
]


def scan_all_setups(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Return {setup_name: boolean Series} for all six setups."""
    return {name: fn(df) for name, fn in SETUPS}