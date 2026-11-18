"""
Actionable trade classification per spec §9.

Action labels:
  BUY_NOW          - setup fired TODAY and market allows longs
  BUY_ON_BREAKOUT  - setup formed but entry trigger not yet met
  BUY_ON_PULLBACK  - setup formed but price is above entry zone
  WATCH            - valid setup but weak market / low confidence
  AVOID            - setup fired but regime forbids or eligibility failed
  NO_TRADE_MARKET  - regime is bearish; no new longs anywhere
"""
from __future__ import annotations


def classify_action(
    setup_fired_today: bool,
    setup_name: str | None,
    market_regime: str,
    opportunity_score: float,
    eligible: bool,
) -> dict:
    """Return {'action': str, 'reason': str}."""
    if not eligible:
        return {"action": "AVOID",
                "reason": "failed eligibility gate (liquidity, data, or price)"}

    if market_regime in ("BEARISH", "HIGH_VOL_BEARISH"):
        return {"action": "NO_TRADE_MARKET",
                "reason": f"market regime {market_regime} - no new longs"}

    if setup_fired_today and setup_name:
        if market_regime in ("STRONG_BULLISH", "BULLISH"):
            if opportunity_score >= 60:
                return {"action": "BUY_NOW",
                        "reason": f"{setup_name} fired, score {opportunity_score:.0f}, market ok"}
            return {"action": "WATCH",
                    "reason": f"{setup_name} fired but score {opportunity_score:.0f} < 60"}
        return {"action": "WATCH",
                "reason": f"{setup_name} fired but market is {market_regime}"}

    if setup_name:
        if setup_name == "ema20_pullback":
            return {"action": "BUY_ON_PULLBACK",
                    "reason": "uptrend intact, waiting for pullback to EMA20"}
        return {"action": "BUY_ON_BREAKOUT",
                "reason": f"{setup_name} forming, waiting for confirmation"}

    return {"action": "WATCH",
            "reason": "no qualifying setup today"}