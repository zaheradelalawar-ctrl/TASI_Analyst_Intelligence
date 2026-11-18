"""
Technical indicators for TASI stock analysis.

All functions take pandas Series or DataFrames and return Series/DataFrames.
No state. No look-ahead. NaN preserved (never filled with zero).

Indicator conventions match standard charting platforms: Wilder's smoothing
for RSI/ATR/ADX, adjust=False for EMA.

Additions from the BBW+momentum scanner pattern:
  - bollinger_band_width : BBW as percentage series
  - bbw_percentile       : current BBW vs its trailing N-session history
  - lagged_momentum      : standard "N-1 month" momentum excluding last 21 sessions
  - momentum_panel       : all momentum measures for one stock
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_MOMENTUM_LAG = 21
MOMENTUM_WINDOWS = {"3M": 63, "6M": 126, "9M": 189}


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))

    result = result.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    result = result.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    result = result.where(~((avg_gain == 0) & (avg_loss == 0)), np.nan)
    result = result.where(avg_gain.notna(), np.nan)
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line,
                         "histogram": histogram})


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0/period, adjust=False,
                              min_periods=period).mean()


def bollinger_bands(series: pd.Series, period: int = 20,
                    num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width_pct = ((upper - lower) / mid.replace(0, np.nan)) * 100.0
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower,
                         "width_pct": width_pct})


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (df["volume"].fillna(0) * direction).cumsum()


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Today's volume vs PRIOR period average (excludes today from the average)."""
    prior_avg = (df["volume"].shift(1)
                 .rolling(window=period, min_periods=period).mean())
    return df["volume"] / prior_avg.replace(0, np.nan)


def rate_of_change(series: pd.Series, period: int = 10) -> pd.Series:
    return series.pct_change(periods=period) * 100.0


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = true_range(df)
    atr_w = tr.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()

    plus_di = 100.0 * plus_dm.ewm(alpha=1.0/period, adjust=False,
                                   min_periods=period).mean() / atr_w.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0/period, adjust=False,
                                     min_periods=period).mean() / atr_w.replace(0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1.0/period, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di})


def stochastic(df: pd.DataFrame, k_period: int = 14,
               d_period: int = 3) -> pd.DataFrame:
    low_min = df["low"].rolling(window=k_period, min_periods=k_period).min()
    high_max = df["high"].rolling(window=k_period, min_periods=k_period).max()
    k = 100.0 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


# ---------------------------------------------------------------------------
# BBW (Bollinger Band Width) — key addition
# ---------------------------------------------------------------------------
def bollinger_band_width(close: pd.Series, period: int = 20,
                          deviations: float = 2.0) -> pd.Series:
    """Bollinger Band Width as percentage of the middle band."""
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    return (((mid + deviations * std) - (mid - deviations * std))
            / mid.replace(0, np.nan)) * 100.0


def bbw_percentile(bbw_series: pd.Series, lookback: int = 252,
                   min_observations: int = 126) -> pd.Series:
    """
    Current BBW vs the trailing N-session history, as a percentile (0-100).

    Fills a rolling series where each value is the percentile of that day's
    BBW against the trailing `lookback` window. A value of 5 means "today's
    BBW is at or below 5% of the last N observations" — a squeeze.

    The current observation is INCLUDED in the comparison (standard convention).
    """
    def _pct(window: pd.Series) -> float:
        vals = window.dropna()
        if len(vals) < min_observations:
            return np.nan
        current = vals.iloc[-1]
        if not np.isfinite(current):
            return np.nan
        return float((vals <= current).sum()) / len(vals) * 100.0

    if isinstance(bbw_series, pd.Series):
        out = pd.Series(index=bbw_series.index, dtype=float)
        for i in range(len(bbw_series)):
            lo = max(0, i - lookback + 1)
            out.iloc[i] = _pct(bbw_series.iloc[lo:i + 1])
        return out
    return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Lagged momentum — standard "12-1" style, avoids reversal effects
# ---------------------------------------------------------------------------
def lagged_momentum(close: pd.Series, window_days: int,
                     lag_days: int = DEFAULT_MOMENTUM_LAG) -> float:
    """
    Return from (window_days + lag_days) sessions ago to lag_days sessions ago.

    For 6M with lag=21: price 21 sessions ago / price 147 sessions ago - 1.
    This is the standard academic momentum convention that excludes the most
    recent month to avoid short-term reversal contamination.
    """
    prices = close.dropna()
    if window_days < 1 or lag_days < 0:
        return np.nan
    required = window_days + lag_days + 1
    if len(prices) < required:
        return np.nan
    recent = float(prices.iloc[-lag_days - 1])
    old = float(prices.iloc[-window_days - lag_days - 1])
    if not (np.isfinite(recent) and np.isfinite(old)) or old <= 0:
        return np.nan
    return recent / old - 1.0


def momentum_panel(close: pd.Series,
                    lag_days: int = DEFAULT_MOMENTUM_LAG) -> dict:
    """Return dict of all momentum measures for the latest bar."""
    prices = close.dropna()
    result: dict = {}

    for name, sessions in [("1D", 1), ("5D", 5), ("20D", 20)]:
        if len(prices) > sessions:
            old = float(prices.iloc[-sessions - 1])
            new = float(prices.iloc[-1])
            result[name] = (new / old - 1.0) if old > 0 else np.nan
        else:
            result[name] = np.nan

    for name, window in MOMENTUM_WINDOWS.items():
        result[name] = lagged_momentum(prices, window, lag_days)

    return result


# ---------------------------------------------------------------------------
# Combined indicator computation
# ---------------------------------------------------------------------------
def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators on an OHLCV DataFrame.
    Returns copy of df with one column per indicator appended.
    """
    out = df.copy()
    close = df["close"]

    # Trend
    out["ema_9"] = ema(close, 9)
    out["ema_20"] = ema(close, 20)
    out["ema_50"] = ema(close, 50)
    out["sma_50"] = sma(close, 50)
    out["sma_200"] = sma(close, 200)

    # Momentum
    out["rsi_14"] = rsi(close, 14)
    m = macd(close)
    out["macd"] = m["macd"]
    out["macd_signal"] = m["signal"]
    out["macd_hist"] = m["histogram"]
    out["roc_10"] = rate_of_change(close, 10)
    st = stochastic(df)
    out["stoch_k"] = st["k"]
    out["stoch_d"] = st["d"]

    # Volume
    out["volume_ratio_20"] = volume_ratio(df, 20)
    out["obv"] = obv(df)

    # Volatility
    out["atr_14"] = atr(df, 14)
    bb = bollinger_bands(close, 20, 2.0)
    out["bb_upper"] = bb["upper"]
    out["bb_mid"] = bb["mid"]
    out["bb_lower"] = bb["lower"]
    out["bb_width"] = bb["width_pct"]

    # Trend strength
    a = adx(df)
    out["adx"] = a["adx"]
    out["plus_di"] = a["plus_di"]
    out["minus_di"] = a["minus_di"]

    # --- NEW: BBW percentile and lagged momentum ------------------
    bbw = bollinger_band_width(close, 20, 2.0)
    out["bbw_pctile"] = bbw_percentile(bbw, lookback=252, min_observations=126)

    # Lagged momentum stored as columns for cross-sectional ranking
    for name, window in MOMENTUM_WINDOWS.items():
        col = f"mom_{name}_lagged"
        out[col] = np.nan  # will be filled on the last row only
    # Compute panel once and set last row
    panel = momentum_panel(close)
    if len(out) > 0:
        for name in MOMENTUM_WINDOWS:
            out.iloc[-1, out.columns.get_loc(f"mom_{name}_lagged")] = panel[name]

    return out