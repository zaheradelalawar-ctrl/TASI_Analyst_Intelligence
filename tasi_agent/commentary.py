"""
Deterministic analyst commentary. Every sentence is derived from stored
evidence - nothing here invents a target price, forecast or opinion.
"""
from __future__ import annotations

import datetime as dt

from .consensus import valuation_summary
from .models import StockConsensus


def _age_days(date_iso: str, as_of: str) -> int:
    return (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(date_iso)).days


def consensus_comment(sc: StockConsensus, as_of: str) -> str:
    n = len(sc.positive)
    firms = ", ".join(f"{o.provider_name} ({o.rating_raw})" for o in sc.positive)
    ages = [_age_days(o.publication_date, as_of) for o in sc.positive]
    fresh_txt = f"oldest {max(ages)} days" if ages else "n/a"

    if n >= 3:
        head = f"{n} independent research firms currently hold positive views on {sc.ticker}"
    elif n == 2:
        head = f"Two independent research firms currently hold positive views on {sc.ticker}"
    else:
        head = f"Only one research firm currently holds a positive view on {sc.ticker}"

    upgrades = [o.provider_name for o in sc.positive if o.is_upgrade]
    tail = f" Recent upgrades: {', '.join(upgrades)}." if upgrades else " No recent upgrades among these firms."
    if n < 2:
        tail += " This does not meet the two-firm confirmation rule and is not highlighted."
    return f"{head}: {firms}. Recommendation age within window - {fresh_txt}.{tail}"


def valuation_comment(sc: StockConsensus, conn=None) -> str:
    v = valuation_summary(sc, conn)
    if v["n_targets"] == 0:
        return "No target prices were disclosed in the available reports; valuation agreement cannot be assessed."

    parts = []
    if v["split_adjusted"]:
        parts.append(
            f"A stock split occurred after at least one of these reports was published. "
            f"The raw published targets are shown alongside split-adjusted equivalents: "
            f"median raw {v['median_target']:.2f} SAR vs adjusted {v['median_target_adj']:.2f} SAR."
        )

    if v["n_targets"] == 1:
        up = v["per_firm"][0]["upside_now"]
        base = f"Only one target price is available ({v['median_target_adj']:.2f} SAR adjusted)"
        parts.append(base + (f", implying {up:+.1f}% versus the latest price. "
                             "A single target is not a consensus."
                             if up is not None
                             else "; no current price is available to compute upside."))
    else:
        parts.append(
            f"{v['n_targets']} target prices range from {v['min_target']:.2f} to "
            f"{v['max_target']:.2f} SAR (median {v['median_target_adj']:.2f}) after adjustment."
        )
        if v["upside_now"] is not None:
            parts.append(f"Median adjusted target implies roughly {v['upside_now']:+.1f}% "
                         "versus the latest price.")
        if v["upside_at_publication"] is not None:
            parts.append(f"At publication the same targets implied {v['upside_at_publication']:+.1f}%.")
        if v["dispersion_pct"] is not None:
            if v["dispersion_pct"] > 30:
                parts.append(f"Target dispersion is wide ({v['dispersion_pct']:.0f}% of median) - "
                             "the firms agree on direction but not on valuation.")
            else:
                parts.append(f"Target dispersion is contained ({v['dispersion_pct']:.0f}% of median), "
                             "suggesting reasonable valuation agreement.")
    return " ".join(parts)


def risk_comment(sc: StockConsensus, as_of: str, latest_price: float | None,
                 conn=None) -> str:
    flags: list[str] = []
    v = valuation_summary(sc, conn)

    if sc.negative:
        names = ", ".join(f"{o.provider_name} ({o.rating_raw})" for o in sc.negative)
        flags.append(f"Contrary view present: {names}. The stock is not unanimously confirmed.")

    missing = [o.provider_name for o in sc.positive if not o.target_price]
    if missing:
        flags.append(f"No target price disclosed by {', '.join(missing)}.")

    ages = [_age_days(o.publication_date, as_of) for o in sc.positive]
    if ages and min(ages) > 45:
        flags.append(f"All supporting reports are at least {min(ages)} days old; refresh before acting.")

    low_conf = [o.provider_name for o in sc.positive if o.confidence < 0.7]
    if low_conf:
        flags.append(f"Lower extraction confidence for {', '.join(low_conf)} - verify against the source.")

    assumed = [o.provider_name for o in sc.positive if o.rating_assumed]
    if assumed:
        flags.append(f"Non-standard rating wording mapped by the agent for {', '.join(assumed)}.")

    if v["split_adjusted"]:
        flags.append(
            "Split adjustment applied. If Yahoo missed the split (common for Saudi "
            "stocks), the adjustment here will be wrong - check data/manual_splits.json "
            "and verify against Tadawul corporate actions."
        )

    if v["upside_now"] is not None and abs(v["upside_now"]) > 100 and not v["split_adjusted"]:
        flags.append(
            f"Computed implied upside of {v['upside_now']:+.0f}% is implausible and suggests "
            "an undetected stock split. Verify against Tadawul corporate actions and add "
            "the split to data/manual_splits.json if confirmed."
        )

    if v["upside_now"] is not None and v["upside_now"] < 5 and not v["split_adjusted"]:
        flags.append(f"Average implied upside versus the latest price is only "
                     f"{v['upside_now']:+.1f}% - consensus direction is positive but the "
                     "valuation cushion is thin.")

    if sc.stale:
        flags.append(f"{len(sc.stale)} older opinion(s) fall outside the freshness window.")

    if not flags:
        return "No material red flags detected in the available evidence. Not an investment recommendation."
    return " ".join(flags)


def build_comments(sc: StockConsensus, as_of: str, conn=None) -> dict[str, str]:
    return {
        "consensus": consensus_comment(sc, as_of),
        "valuation": valuation_comment(sc, conn),
        "risk": risk_comment(sc, as_of, sc.latest_price, conn),
    }