"""
Cross-sectional ranking across the scan universe.

Given per-stock measurements, compute percentile ranks so a "score 65" becomes
meaningful: top 20% of universe today? Bottom 40%? The BBW scanner pattern
shows this is essential — absolute thresholds fail in mixed markets.

Every rank is 0-100. Higher = stronger signal for that dimension.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_ranks(df: pd.DataFrame, column_map: dict[str, str],
              invert: dict[str, bool] | None = None) -> pd.DataFrame:
    """
    Add rank columns for each (source_col -> rank_col) in column_map.

    Ranks are percentiles (0-100). NaN values in source remain NaN in rank.
    Set invert[src] = True if LOWER source is better (e.g. BBW percentile:
    lower squeeze is stronger setup).
    """
    invert = invert or {}
    out = df.copy()

    for src, rank_col in column_map.items():
        if src not in out.columns:
            out[rank_col] = np.nan
            continue

        series = pd.to_numeric(out[src], errors="coerce")

        if invert.get(src, False):
            series = -series

        out[rank_col] = series.rank(pct=True, method="average") * 100.0

    return out


def composite_rank(df: pd.DataFrame,
                    weights: dict[str, float]) -> pd.Series:
    """
    Weighted combination of rank columns. Weights need not sum to 1.
    NaN components are treated as 0 contribution; the result is rescaled
    by the total weight of AVAILABLE components so no stock is penalized
    for missing data (matches BBW scanner convention).
    """
    total = pd.Series(0.0, index=df.index)
    weight_used = pd.Series(0.0, index=df.index)

    for col, w in weights.items():
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        mask = vals.notna()
        total[mask] += vals[mask] * w
        weight_used[mask] += w

    return (total / weight_used.replace(0, np.nan)).fillna(0)


def rank_universe(rows: list[dict]) -> pd.DataFrame:
    """
    Take a list of per-stock dicts (with ticker + raw measurements) and
    return a DataFrame with rank columns added.

    Expected input keys (missing ones produce NaN ranks, no error):
      ticker
      mom_6M_lagged, mom_20D, mom_5D
      relative_strength_20D
      bbw_pctile
      volume_ratio
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    column_map = {
        "mom_6M_lagged": "rank_6M",
        "mom_20D":       "rank_20D",
        "mom_5D":        "rank_5D",
        "relative_strength_20D": "rank_rs",
        "bbw_pctile":    "rank_squeeze",
        "volume_ratio":  "rank_volume",
    }
    # Lower BBW percentile = tighter squeeze = better for squeeze setups
    invert = {"bbw_pctile": True}

    df = add_ranks(df, column_map, invert=invert)

    # Composite "setup quality" score
    df["rank_composite"] = composite_rank(df, {
        "rank_6M":      0.35,
        "rank_rs":      0.25,
        "rank_20D":     0.15,
        "rank_squeeze": 0.15,
        "rank_volume":  0.10,
    })

    return df