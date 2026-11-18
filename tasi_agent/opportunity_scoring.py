"""
Transparent 100-point opportunity scoring per spec §8.

Components and weights:
  Trend strength           : 20
  Momentum                 : 15
  Volume confirmation      : 20
  Setup quality            : 15
  Relative strength vs TASI: 10
  Risk/reward              : 10
  Analyst support          :  5
  Data quality/liquidity   :  5
Total                      100

No points awarded for missing data.
"""
from __future__ import annotations

import pandas as pd


MIN_LIQUIDITY_SAR = 500_000
MIN_HISTORY_BARS = 60
MIN_BARS_FOR_SMA200 = 200


def eligibility_gates(df: pd.DataFrame, ticker: str,
                      latest_market_price: float | None) -> tuple[bool, list[str]]:
    """Return (eligible, list_of_failure_reasons)."""
    reasons: list[str] = []

    if df is None or len(df) < MIN_HISTORY_BARS:
        reasons.append(f"insufficient history ({len(df) if df is not None else 0} bars)")

    if df is not None and "volume" in df.columns:
        recent_vol_sar = (df["close"] * df["volume"]).tail(20).mean()
        if pd.isna(recent_vol_sar) or recent_vol_sar < MIN_LIQUIDITY_SAR:
            reasons.append(f"liquidity below {MIN_LIQUIDITY_SAR:,} SAR avg 20d")

    if latest_market_price is None or latest_market_price <= 0:
        reasons.append("no current market price")

    return (len(reasons) == 0, reasons)


def _score_trend(df: pd.DataFrame) -> tuple[float, str]:
    if df.empty:
        return 0.0, "no data"
    last = df.iloc[-1]
    pts = 0.0
    notes = []

    close = last["close"]
    ema20 = last.get("ema_20")
    ema50 = last.get("ema_50")
    sma200 = last.get("sma_200")

    if pd.notna(ema20) and close > ema20:
        pts += 5
        notes.append(">EMA20")
    if pd.notna(ema50) and close > ema50:
        pts += 5
        notes.append(">EMA50")
    if pd.notna(ema20) and pd.notna(ema50) and ema20 > ema50:
        pts += 3
        notes.append("EMA20>EMA50")
    if pd.notna(sma200) and close > sma200:
        pts += 2
        notes.append(">SMA200")

    adx_val = last.get("adx")
    if pd.notna(adx_val) and adx_val >= 25:
        pts += 5
        notes.append(f"ADX{adx_val:.0f}")
    elif pd.notna(adx_val) and adx_val >= 20:
        pts += 2
        notes.append(f"ADX{adx_val:.0f}")

    return min(pts, 20.0), ", ".join(notes) if notes else "weak trend"


def _score_momentum(df: pd.DataFrame) -> tuple[float, str]:
    if df.empty:
        return 0.0, "no data"
    last = df.iloc[-1]
    pts = 0.0
    notes = []

    rsi = last.get("rsi_14")
    if pd.notna(rsi):
        if 50 <= rsi <= 70:
            pts += 6
            notes.append(f"RSI{rsi:.0f}")
        elif 40 <= rsi < 50:
            pts += 3
            notes.append(f"RSI{rsi:.0f}")
        elif rsi > 70:
            pts += 2
            notes.append(f"RSI{rsi:.0f}(overbought)")

    macd_hist = last.get("macd_hist")
    if pd.notna(macd_hist) and macd_hist > 0:
        pts += 5
        notes.append("MACD+")

    roc = last.get("roc_10")
    if pd.notna(roc) and roc > 0:
        pts += min(4, roc / 5.0 * 4)
        notes.append(f"ROC{roc:+.1f}%")

    return min(pts, 15.0), ", ".join(notes) if notes else "neutral momentum"


def _score_volume(df: pd.DataFrame) -> tuple[float, str]:
    if df.empty:
        return 0.0, "no data"
    last = df.iloc[-1]
    pts = 0.0
    notes = []

    vr = last.get("volume_ratio_20")
    if pd.notna(vr):
        if vr >= 2.0:
            pts += 12
            notes.append(f"Vol{vr:.1f}x")
        elif vr >= 1.5:
            pts += 9
            notes.append(f"Vol{vr:.1f}x")
        elif vr >= 1.2:
            pts += 5
            notes.append(f"Vol{vr:.1f}x")
        elif vr >= 1.0:
            pts += 2

    obv = df["obv"] if "obv" in df.columns else None
    if obv is not None and len(obv) >= 20:
        recent = obv.tail(20)
        if recent.iloc[-1] > recent.iloc[0]:
            pts += 8
            notes.append("OBV rising")

    return min(pts, 20.0), ", ".join(notes) if notes else "no volume edge"


def _score_setup(setup_name: str | None) -> tuple[float, str]:
    if not setup_name:
        return 0.0, "no setup"
    scores = {
        "volume_breakout":   15.0,
        "bollinger_squeeze": 13.0,
        "macd_crossover":    11.0,
        "ema20_pullback":    12.0,
        "rsi_recovery":      10.0,
        "20d_high_volume":   10.0,
    }
    return scores.get(setup_name, 8.0), setup_name


def _score_relative_strength(df: pd.DataFrame,
                              tasi_returns: pd.Series | None) -> tuple[float, str]:
    if df.empty or tasi_returns is None or len(tasi_returns) < 20:
        return 0.0, "no benchmark"
    stock_roc = df["close"].pct_change(20).iloc[-1]
    tasi_roc = tasi_returns.iloc[-1] if len(tasi_returns) >= 1 else 0
    if pd.isna(stock_roc) or pd.isna(tasi_roc):
        return 0.0, "insufficient data"
    excess = (stock_roc - tasi_roc) * 100
    if excess >= 10:
        return 10.0, f"RS +{excess:.1f}%"
    if excess >= 5:
        return 7.0, f"RS +{excess:.1f}%"
    if excess >= 0:
        return 4.0, f"RS +{excess:.1f}%"
    return 0.0, f"RS {excess:+.1f}%"


def _score_risk_reward(plan: dict | None) -> tuple[float, str]:
    if not plan:
        return 0.0, "no plan"
    rr = plan.get("risk_reward_1", 0)
    if rr >= 3.0:
        return 10.0, f"R:R 1:{rr:.1f}"
    if rr >= 2.5:
        return 8.0, f"R:R 1:{rr:.1f}"
    if rr >= 2.0:
        return 6.0, f"R:R 1:{rr:.1f}"
    return 3.0, f"R:R 1:{rr:.1f}"


def _score_analyst_support(analyst_status: str | None) -> tuple[float, str]:
    if analyst_status == "STRONG_CONSENSUS":
        return 5.0, "strong analyst support"
    if analyst_status == "CONFIRMED":
        return 4.0, "confirmed analyst support"
    if analyst_status == "CONFIRMED_WITH_DISSENT":
        return 3.0, "analyst support with dissent"
    if analyst_status == "SINGLE_OPINION":
        return 1.0, "single analyst"
    return 0.0, "no analyst edge"


def _score_data_quality(n_bars: int) -> tuple[float, str]:
    if n_bars >= MIN_BARS_FOR_SMA200:
        return 5.0, "full history"
    if n_bars >= 120:
        return 3.0, f"{n_bars} bars"
    if n_bars >= MIN_HISTORY_BARS:
        return 1.5, f"{n_bars} bars"
    return 0.0, f"only {n_bars} bars"


def compute_opportunity_score(
    df: pd.DataFrame,
    setup_name: str | None,
    plan: dict | None,
    analyst_status: str | None,
    tasi_returns: pd.Series | None,
) -> dict:
    """Return full score breakdown."""
    trend, trend_note = _score_trend(df)
    momentum, momentum_note = _score_momentum(df)
    volume, volume_note = _score_volume(df)
    setup, setup_note = _score_setup(setup_name)
    rs, rs_note = _score_relative_strength(df, tasi_returns)
    rr, rr_note = _score_risk_reward(plan)
    analyst, analyst_note = _score_analyst_support(analyst_status)
    quality, quality_note = _score_data_quality(len(df) if df is not None else 0)

    total = trend + momentum + volume + setup + rs + rr + analyst + quality

    return {
        "total": round(total, 2),
        "components": {
            "trend": {"score": round(trend, 1), "max": 20, "note": trend_note},
            "momentum": {"score": round(momentum, 1), "max": 15, "note": momentum_note},
            "volume": {"score": round(volume, 1), "max": 20, "note": volume_note},
            "setup": {"score": round(setup, 1), "max": 15, "note": setup_note},
            "relative_strength": {"score": round(rs, 1), "max": 10, "note": rs_note},
            "risk_reward": {"score": round(rr, 1), "max": 10, "note": rr_note},
            "analyst": {"score": round(analyst, 1), "max": 5, "note": analyst_note},
            "data_quality": {"score": round(quality, 1), "max": 5, "note": quality_note},
        },
    }