"""
Streamlit dashboard for TASI Analyst Consensus Intelligence.

Absolute imports + sys.path injection because Streamlit runs this file as a
top-level script (not as a package module).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import datetime as dt

import pandas as pd
import streamlit as st

from tasi_agent.config import db_path, load
from tasi_agent.consensus import (
    STATUS_CONFLICTING,
    STATUS_CONFIRMED,
    STATUS_CONFIRMED_DISSENT,
    STATUS_LABEL,
    STATUS_NONE,
    STATUS_SINGLE,
    STATUS_STRONG,
    build_consensus,
    confirmed_with_dissent,
    conflicting,
    highlighted,
    material_negatives,
    unconfirmed,
    valuation_summary,
)
from tasi_agent.db import connect
from tasi_agent.performance import aggregate as perf_aggregate, evaluate as perf_evaluate


st.set_page_config(page_title="TASI Analyst Intelligence", layout="wide")

cfg = load(str(_ROOT / "config.yaml"))
conn = connect(db_path(cfg))

as_of = st.sidebar.date_input("As of", dt.date.today()).isoformat()
st.sidebar.markdown("---")
st.sidebar.markdown(
    f"**Highlight rule:** {cfg['consensus']['confirm_threshold']} independent firms\n\n"
    f"**Freshness window:** {cfg['project']['freshness_days']} days\n\n"
    f"**Database:** `{db_path(cfg)}`"
)


results = build_consensus(conn, cfg, as_of)
section_a = highlighted(results)
section_b = confirmed_with_dissent(results)
section_c = conflicting(results)
section_d = unconfirmed(results)


st.title("TASI Analyst Consensus Intelligence")
st.caption(f"Five-section report · as of {as_of} · {len(results)} stocks analysed")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("A · Clean confirmations", len(section_a))
c2.metric("B · With dissent", len(section_b))
c3.metric("C · Conflicting", len(section_c))
c4.metric("D · Single / unconfirmed", len(section_d))
c5.metric("Providers tracked",
          conn.execute("SELECT COUNT(*) c FROM providers").fetchone()["c"])


def _stock_card(sc, show_material: bool = False, expanded: bool = False) -> None:
    v = valuation_summary(sc, conn)
    label = STATUS_LABEL[sc.status]
    header = (
        f"{sc.company_name or sc.ticker} ({sc.ticker}) — {label} — "
        f"{len(sc.positive)} pos · {len(sc.neutral)} neu · {len(sc.negative)} neg"
    )
    with st.expander(header, expanded=expanded):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Latest price (SAR)", f"{sc.latest_price:,.2f}" if sc.latest_price else "—")
        m2.metric(
            "Median target (SAR)",
            f"{v['median_target_adj']:,.2f}" if v["split_adjusted"] and v["median_target_adj"]
            else (f"{v['median_target']:,.2f}" if v["median_target"] else "—"),
        )
        m3.metric("Implied upside now",
                  f"{v['upside_now']:+.1f}%" if v["upside_now"] is not None else "—")
        m4.metric("Dispersion",
                  f"{v['dispersion_pct']:.0f}%" if v["dispersion_pct"] is not None else "—")

        if show_material:
            mat = material_negatives(conn, sc)
            if mat:
                st.error("**Material downside disagreement**")
                for m in mat:
                    st.markdown(
                        f"- **{m['firm']}** — target SAR {m['target_adj']:.2f} "
                        f"vs current SAR {m['current_price']:.2f} "
                        f"({m['downside_pct']:+.1f}%) · {m['publication_date']}"
                    )

        rows = []
        for o in sc.positive + sc.neutral + sc.negative:
            rows.append({
                "Firm": o.provider_name,
                "Rating": o.rating_raw,
                "Class": o.rating_norm,
                "Target": o.target_price,
                "Date": o.publication_date,
                "Confidence": round(o.confidence, 2),
                "Source": o.source_url or "",
                "Validation": o.validation_status,
            })
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "Source": st.column_config.LinkColumn("Source", display_text="open"),
                },
            )


st.subheader(f"A · Strong and clean confirmations ({len(section_a)})")
if not section_a:
    st.info("No stock currently meets the clean two-firm rule.")
for sc in section_a:
    _stock_card(sc, show_material=False, expanded=(len(section_a) <= 3))


st.subheader(f"B · Confirmed with dissent ({len(section_b)})")
if not section_b:
    st.info("No stocks currently have a passing positive threshold with only "
            "non-material negatives. Material negatives move stocks to Section C.")
for sc in section_b:
    _stock_card(sc, show_material=True)


st.subheader(f"C · Conflicting opinions ({len(section_c)})")
if not section_c:
    st.info("No stocks with material disagreement this run.")
for sc in section_c:
    _stock_card(sc, show_material=True)


st.subheader(f"D · Single opinions and unconfirmed stocks ({len(section_d)})")
if not section_d:
    st.info("None.")
else:
    table = pd.DataFrame([{
        "Ticker": s.ticker,
        "Company": s.company_name,
        "Status": STATUS_LABEL[s.status],
        "Positive": len(s.positive),
        "Neutral": len(s.neutral),
        "Negative": len(s.negative),
        "Latest price": s.latest_price,
    } for s in section_d])
    st.dataframe(table, width="stretch", hide_index=True)


st.subheader("E · Historical analyst performance")
perf_rows = perf_evaluate(conn, cfg)
if not perf_rows:
    st.info("No evaluable recommendations yet. "
            "Run `python run.py collect --source prices` to populate the price history.")
else:
    any_bench = any(r["benchmark_available"] for r in perf_rows)
    if not any_bench:
        st.warning("**Benchmark unavailable.** Hit rate and excess-return columns are "
                   "blank because the TASI index feed returned no usable history.")
    min_n = cfg["performance"]["min_sample_for_publication"]
    agg = perf_aggregate(perf_rows, min_n)
    perf_df = pd.DataFrame([{
        "Firm": a["provider"],
        "Evaluated": a["evaluated"],
        "vs Benchmark": a["evaluated_vs_benchmark"],
        "Hit rate (%)": None if a["hit_rate"] is None else round(a["hit_rate"], 1),
        "Excess vs benchmark (%)": None if a["mean_excess"] is None else round(a["mean_excess"], 2),
        "Publishable": a["publishable"],
    } for a in agg])
    st.dataframe(perf_df, width="stretch", hide_index=True)
    st.caption(
        "Returns measured at 20, 60 and 120 trading days after publication. "
        "Benchmark is the iShares MSCI Saudi Arabia ETF (KSA), a US-listed proxy — "
        f"not the exact TASI index. A firm becomes publishable at N ≥ {min_n}."
    )


st.markdown("---")
st.caption(
    "Analyst consensus is not an investment recommendation. Two independent "
    "firms publishing positive ratings does not imply the stock will rise. "
    "Verify every source report before acting."
)