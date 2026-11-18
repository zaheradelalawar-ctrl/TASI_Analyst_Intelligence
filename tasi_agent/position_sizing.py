"""
Position sizing based on fixed fractional risk.

Pure functions. No database access. Fully unit-testable.
"""
from __future__ import annotations


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    fees_pct: float = 0.001,
    min_ticket: float = 100.0,
) -> dict:
    """
    Return {'shares', 'position_value', 'risk_per_share', 'max_loss',
            'actual_risk_pct', 'fees_total', 'valid', 'reason'}.

    fees_pct applies per side (buy + sell = 2x).

    Rules:
      - risk_per_share must be > 0
      - position_value <= capital (cannot spend more than you have)
      - shares rounded DOWN to whole shares
      - if position_value < min_ticket, returns valid=False
    """
    risk_per_share = float(entry) - float(stop)
    if risk_per_share <= 0:
        return {
            "shares": 0, "position_value": 0.0, "risk_per_share": risk_per_share,
            "max_loss": 0.0, "actual_risk_pct": 0.0, "fees_total": 0.0,
            "valid": False, "reason": "stop >= entry (would lose on stop-out)",
        }

    max_loss = float(capital) * float(risk_pct)
    round_trip_fee_per_share = float(entry) * fees_pct * 2
    loss_per_share_effective = risk_per_share + round_trip_fee_per_share

    raw_shares = max_loss / loss_per_share_effective
    shares = int(raw_shares)

    max_affordable = int(float(capital) / float(entry))
    if shares > max_affordable:
        shares = max_affordable

    if shares <= 0:
        return {
            "shares": 0, "position_value": 0.0, "risk_per_share": risk_per_share,
            "max_loss": 0.0, "actual_risk_pct": 0.0, "fees_total": 0.0,
            "valid": False, "reason": "insufficient capital for even 1 share",
        }

    position_value = shares * float(entry)
    if position_value < min_ticket:
        return {
            "shares": 0, "position_value": 0.0, "risk_per_share": risk_per_share,
            "max_loss": 0.0, "actual_risk_pct": 0.0, "fees_total": 0.0,
            "valid": False,
            "reason": f"position {position_value:.0f} < min ticket {min_ticket:.0f}",
        }

    actual_loss = shares * loss_per_share_effective
    fees = position_value * fees_pct * 2

    return {
        "shares": shares,
        "position_value": round(position_value, 2),
        "risk_per_share": round(risk_per_share, 2),
        "max_loss": round(actual_loss, 2),
        "actual_risk_pct": round(actual_loss / capital, 4),
        "fees_total": round(fees, 2),
        "valid": True,
        "reason": "",
    }


def portfolio_risk_check(positions: list[dict], capital: float,
                          max_total_risk_pct: float = 0.05,
                          max_positions: int = 5) -> dict:
    """
    Check whether adding the proposed positions stays within limits.
    Each position dict must have 'max_loss' and 'position_value'.
    """
    current_risk = sum(p.get("max_loss", 0) for p in positions)
    current_exposure = sum(p.get("position_value", 0) for p in positions)
    limit_risk = capital * max_total_risk_pct

    return {
        "positions_count": len(positions),
        "positions_limit": max_positions,
        "current_risk": round(current_risk, 2),
        "risk_limit": round(limit_risk, 2),
        "risk_ok": current_risk <= limit_risk and len(positions) <= max_positions,
        "current_exposure": round(current_exposure, 2),
        "exposure_limit": round(capital, 2),
        "exposure_ok": current_exposure <= capital,
    }