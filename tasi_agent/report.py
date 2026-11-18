"""HTML / Markdown / Excel report generation (Reading 3 layout)."""
from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path

from .commentary import build_comments
from .consensus import (STATUS_CONFIRMED, STATUS_CONFIRMED_DISSENT, STATUS_CONFLICTING,
                        STATUS_LABEL, STATUS_NONE, STATUS_SINGLE, STATUS_STRONG,
                        confirmed_with_dissent, conflicting, highlighted,
                        material_negatives, unconfirmed, valuation_summary)
from .models import StockConsensus
from .performance import aggregate as perf_aggregate, evaluate as perf_evaluate

CSS = """
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       background:#f4f6f8; color:#14202b; line-height:1.5; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 28px 20px 60px; }
header { background:#0d1b2a; color:#fff; padding:26px 32px; border-radius:10px; }
header h1 { margin:0 0 6px; font-size:24px; letter-spacing:.2px; }
header .sub { color:#9fb3c8; font-size:13px; text-transform:uppercase; letter-spacing:1.2px; }
.header-meta { display:flex; gap:22px; margin-top:16px; flex-wrap:wrap; font-size:13px; color:#c9d6e2; }
.header-meta b { color:#fff; }
.banner { background:#fff4e5; border:1px solid #ffb74d; color:#8a4b00; padding:12px 16px;
          border-radius:8px; margin:18px 0; font-size:13px; }
.banner.danger { background:#fdecea; border-color:#e57373; color:#8e1b14; }
.banner.warn { background:#fff4e5; border-color:#ffb74d; color:#8a4b00; }
h2 { font-size:17px; margin:34px 0 12px; padding-bottom:8px; border-bottom:2px solid #dbe3ea; }
h2 .count { font-size:13px; font-weight:600; color:#7b8fa2; margin-left:8px; }
.card { background:#fff; border:1px solid #e1e8ee; border-radius:10px; padding:20px 22px;
        margin-bottom:16px; box-shadow:0 1px 2px rgba(16,36,56,.04); }
.card.highlight { border-left:5px solid #1b7f4b; }
.card.dissent   { border-left:5px solid #6b8e23; }
.card.conflict { border-left:5px solid #c0392b; }
.card-head { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
.card-head h3 { margin:0; font-size:18px; }
.ticker { color:#5b7186; font-size:13px; font-weight:600; letter-spacing:.5px; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; font-size:11px;
         font-weight:700; letter-spacing:.6px; text-transform:uppercase; }
.badge.strong { background:#0b5d33; color:#fff; }
.badge.confirmed { background:#1b7f4b; color:#fff; }
.badge.dissent { background:#6b8e23; color:#fff; }
.badge.conflict { background:#c0392b; color:#fff; }
.badge.single { background:#f0e2b6; color:#6b5300; }
.badge.none { background:#e3e9ee; color:#4a5c6b; }
.badge.warn { background:#fdecea; color:#8e1b14; border:1px solid #e57373; }
.count-big { font-size:30px; font-weight:800; color:#0d1b2a; }
.count-big small { font-size:14px; color:#7b8fa2; font-weight:600; }
table { width:100%; border-collapse:collapse; background:#fff; font-size:13.5px; }
th, td { padding:9px 11px; text-align:left; border-bottom:1px solid #e8eef3; vertical-align:top; }
th { background:#eef3f7; font-size:11.5px; text-transform:uppercase; letter-spacing:.6px; color:#4a5c6b; }
tr:last-child td { border-bottom:none; }
.comment { background:#f7fafc; border:1px solid #e5edf3; border-radius:8px; padding:12px 14px;
           margin-top:12px; font-size:13.5px; }
.comment b { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.8px;
             color:#6b8093; margin-bottom:4px; }
.material-alert { background:#fdecea; border:1px solid #e57373; border-radius:8px;
                  padding:12px 16px; margin-top:12px; font-size:13.5px; color:#8e1b14; }
.material-alert b { display:block; font-size:11px; text-transform:uppercase;
                    letter-spacing:.8px; margin-bottom:6px; }
.material-alert ul { margin:6px 0 0 18px; padding:0; }
.material-alert li { margin:3px 0; }
a { color:#1565a8; text-decoration:none; }
a:hover { text-decoration:underline; }
.muted { color:#7b8fa2; font-size:12px; }
.pos { color:#1b7f4b; font-weight:600; }
.neg { color:#c0392b; font-weight:600; }
.neu { color:#8a7500; font-weight:600; }
footer { margin-top:40px; padding-top:18px; border-top:1px solid #dbe3ea;
         font-size:12px; color:#7b8fa2; }
"""

BADGE_CLASS = {
    STATUS_STRONG: ("strong", "Strong consensus"),
    STATUS_CONFIRMED: ("confirmed", "Confirmed"),
    STATUS_CONFIRMED_DISSENT: ("dissent", "Confirmed · 1 dissent"),
    STATUS_CONFLICTING: ("conflict", "Conflicting"),
    STATUS_SINGLE: ("single", "Single opinion"),
    STATUS_NONE: ("none", "No confirmation"),
}

SUSPICIOUS_UPSIDE_PCT = 100.0


def _e(text) -> str:
    return html.escape("" if text is None else str(text))


def _fmt(value, suffix="") -> str:
    return "—" if value is None else f"{value:,.2f}{suffix}"


def _rating_cell(o) -> str:
    star = '<span class="muted">*</span>' if o.rating_assumed else ""
    return f"{_e(o.rating_raw)}{star}"


def _quality_flag(v: dict) -> tuple[str, str]:
    upside = v.get("upside_now")
    adjusted = v.get("split_adjusted", False)
    if upside is None:
        return ("no target", "muted")
    if adjusted:
        return ("split-adjusted", "pos")
    if abs(upside) > SUSPICIOUS_UPSIDE_PCT:
        return ("SUSPICIOUS", "warn")
    return ("ok", "")


def _material_disagreement_html(sc: StockConsensus, conn=None) -> str:
    """Render the alert callout for stocks with a material downside dissent."""
    items = material_negatives(conn, sc)
    if not items:
        return ""
    lis = []
    for m in items:
        adj_note = (f" (adj from {m['target_raw']:.2f} ÷ {m['split_ratio']:.2f})"
                    if m["split_ratio"] > 1.0 else "")
        lis.append(
            f"<li><b>{_e(m['firm'])}</b> — target SAR {_fmt(m['target_adj'])}{adj_note} "
            f"vs current SAR {_fmt(m['current_price'])} → "
            f"<span class='neg'>{m['downside_pct']:+.1f}%</span> "
            f"<span class='muted'>({_e(m['publication_date'])})</span></li>"
        )
    return (
        '<div class="material-alert">'
        '<b>Material downside disagreement</b>'
        'The following research firm published a target below the current share price:'
        '<ul>' + "".join(lis) + '</ul>'
        '</div>'
    )


def _firm_table(opinions, latest_price, sc: StockConsensus, conn=None) -> str:
    if not opinions:
        return '<p class="muted">None.</p>'
    v = valuation_summary(sc, conn)
    per_firm = {pf["firm"]: pf for pf in v.get("per_firm", [])}
    rows = []
    for o in opinions:
        info = per_firm.get(o.provider_name, {})
        adj_target = info.get("target_adj")
        split_ratio = info.get("split_ratio", 1.0)
        up_pub = info.get("upside_at_publication")
        up_now = info.get("upside_now")
        move = ""
        if o.is_upgrade:
            move = '<span class="pos">▲ upgrade</span>'
        elif o.is_downgrade:
            move = '<span class="neg">▼ downgrade</span>'
        link = (f'<a href="{_e(o.source_url)}" target="_blank" rel="noopener">source</a>'
                if o.source_url else '<span class="muted">no link</span>')
        adj_note = ""
        if split_ratio and split_ratio > 1.0 and adj_target is not None:
            adj_note = f'<br><span class="muted">adj {_fmt(adj_target)} (÷{split_ratio:.2f})</span>'
        rows.append(
            "<tr>"
            f"<td><b>{_e(o.provider_name)}</b><br>{move}</td>"
            f"<td>{_rating_cell(o)}</td>"
            f"<td>{_fmt(o.target_price)}{adj_note}</td>"
            f"<td>{_fmt(up_pub, '%')}</td>"
            f"<td>{_fmt(up_now, '%')}</td>"
            f"<td>{_e(o.publication_date)}</td>"
            f"<td>{o.confidence:.2f}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Research firm</th><th>Rating (as published)</th><th>Target (SAR)</th>"
        "<th>Upside at publication</th><th>Upside vs latest (adj)</th><th>Date</th><th>Conf.</th><th>Evidence</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _stock_card(sc: StockConsensus, as_of: str, conn=None) -> str:
    cls, label = BADGE_CLASS[sc.status]
    comments = build_comments(sc, as_of, conn)
    v = valuation_summary(sc, conn)

    if sc.status == STATUS_CONFIRMED_DISSENT:
        card_cls = "card dissent"
    elif sc.status in (STATUS_STRONG, STATUS_CONFIRMED):
        card_cls = "card highlight"
    else:
        card_cls = "card conflict"

    q_label, q_cls = _quality_flag(v)
    quality_html = (f' <span class="badge {q_cls}">{q_label}</span>'
                    if q_label not in ("ok", "no target") else "")

    material_html = _material_disagreement_html(sc, conn)

    denom = len(sc.positive) + len(sc.neutral) + len(sc.negative)
    if v["split_adjusted"] and v["median_target_adj"] is not None:
        target_line = (f"Latest price: {_fmt(sc.latest_price)} SAR · "
                       f"Raw median target: {_fmt(v['median_target'])} SAR · "
                       f"Adjusted median: {_fmt(v['median_target_adj'])} SAR")
    else:
        target_line = (f"Latest price: {_fmt(sc.latest_price)} SAR · "
                       f"Median target: {_fmt(v['median_target'])} SAR")

    return f"""
<div class="{card_cls}">
  <div class="card-head">
    <div>
      <h3>{_e(sc.company_name or sc.ticker)} <span class="ticker">{_e(sc.ticker)}</span>{quality_html}</h3>
      <div class="muted">{target_line}</div>
    </div>
    <div style="text-align:right">
      <span class="badge {cls}">{label}</span>
      <div class="count-big" style="margin-top:8px">{len(sc.positive)}<small> / {denom} firms positive</small></div>
    </div>
  </div>
  <div style="margin-top:14px">{_firm_table(sc.positive + sc.neutral + sc.negative, sc.latest_price, sc, conn)}</div>
  {material_html}
  <div class="comment"><b>Consensus</b>{_e(comments['consensus'])}</div>
  <div class="comment"><b>Valuation</b>{_e(comments['valuation'])}</div>
  <div class="comment"><b>Risk &amp; disagreement</b>{_e(comments['risk'])}</div>
</div>"""


def _simple_table(results: list[StockConsensus], conn=None) -> str:
    if not results:
        return '<p class="muted">None in this category.</p>'
    rows = []
    for sc in results:
        cls, label = BADGE_CLASS[sc.status]
        v = valuation_summary(sc, conn)
        q_label, q_cls = _quality_flag(v)
        q_html = (f' <span class="badge {q_cls}">{q_label}</span>'
                  if q_label not in ("ok", "no target") else "")
        rows.append(
            "<tr>"
            f"<td><b>{_e(sc.company_name or sc.ticker)}</b><br><span class='ticker'>{_e(sc.ticker)}</span></td>"
            f"<td class='pos'>{len(sc.positive)}</td>"
            f"<td class='neu'>{len(sc.neutral)}</td>"
            f"<td class='neg'>{len(sc.negative)}</td>"
            f"<td><span class='badge {cls}'>{label}</span>{q_html}</td>"
            "</tr>"
        )
    return ("<table><thead><tr><th>Stock</th><th>Positive</th><th>Neutral</th><th>Negative</th>"
            "<th>Status</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _activity_section(conn, as_of: str, window_days: int) -> str:
    start = (dt.date.fromisoformat(as_of) - dt.timedelta(days=window_days)).isoformat()
    new_rows = conn.execute(
        "SELECT r.*, p.name AS provider_name FROM recommendations r JOIN providers p ON p.id=r.provider_id "
        "WHERE date(r.collected_at) >= ? ORDER BY r.publication_date DESC LIMIT 30", (start,)
    ).fetchall()
    if not new_rows:
        return '<p class="muted">No new recommendations detected in this window.</p>'
    rows = []
    for r in new_rows:
        link = (f'<a href="{_e(r["source_url"])}" target="_blank" rel="noopener">report</a>'
                if r["source_url"] else "—")
        rows.append(
            "<tr>"
            f"<td>{_e(r['publication_date'])}</td>"
            f"<td>{_e(r['company_name'] or r['ticker'])}</td>"
            f"<td>{_e(r['provider_name'])}</td>"
            f"<td>{_e(r['rating_raw'])}</td>"
            f"<td>{_fmt(r['target_price'])}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    return ("<table><thead><tr><th>Date</th><th>Stock</th><th>Firm</th><th>Rating</th>"
            "<th>Target</th><th>Evidence</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _performance_section(conn, cfg) -> str:
    rows = perf_evaluate(conn, cfg)
    if not rows:
        return (
            '<p class="muted">No analyst performance can be computed yet. '
            'Run <code>python run.py collect --source prices</code> and confirm the '
            'benchmark feed works, then regenerate.</p>'
        )

    any_bench = any(r["benchmark_available"] for r in rows)
    min_n = int(cfg["performance"]["min_sample_for_publication"])
    agg = perf_aggregate(rows, min_n)

    body = []
    for a in agg:
        hit = f"{a['hit_rate']:.1f}%" if a["hit_rate"] is not None else "—"
        exc = f"{a['mean_excess']:+.2f}%" if a["mean_excess"] is not None else "—"
        status = ('<span class="pos">publishable</span>' if a["publishable"]
                  else '<span class="muted">sample too small</span>')
        body.append(
            f"<tr><td><b>{_e(a['provider'])}</b></td>"
            f"<td>{a['evaluated']} ({a['evaluated_vs_benchmark']} vs bench)</td>"
            f"<td>{hit}</td><td>{exc}</td><td>{status}</td></tr>"
        )

    banner = ""
    if not any_bench:
        banner = (
            '<div class="banner warn"><b>Benchmark unavailable.</b> The TASI index feed '
            'returned no usable history, so "positive return rate" and "outperformance" '
            'cannot be computed. Section E will populate once the benchmark ticker '
            'starts returning data.</div>'
        )

    return (
        banner
        + "<table><thead><tr>"
        + "<th>Research firm</th><th>Evaluated</th>"
        + "<th>Positive return rate</th>"
        + "<th>Outperformance vs Saudi benchmark (KSA ETF proxy)</th>"
        + "<th>Status</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
        + f'<p class="muted">Returns measured at 20, 60 and 120 trading days after publication. '
        + 'Benchmark is the iShares MSCI Saudi Arabia ETF (KSA), a US-listed proxy for Saudi '
        + 'large-caps — not the exact TASI index. A firm only shows publishable figures once it '
        + f'has at least {min_n} evaluated recommendations with a working benchmark. Past '
        + 'performance is not a forecast.</p>'
    )


def build_html(conn, results: list[StockConsensus], cfg: dict, as_of: str) -> str:
    section_a = highlighted(results)
    section_b = confirmed_with_dissent(results)
    section_c = conflicting(results)
    section_d = unconfirmed(results)

    has_synthetic = conn.execute(
        "SELECT COUNT(*) c FROM recommendations WHERE source_type='synthetic_demo'"
    ).fetchone()["c"] > 0

    banner = ""
    if has_synthetic:
        banner = ('<div class="banner danger"><b>DEMO DATA.</b> The database contains '
                  'synthetic records seeded for testing.</div>')

    cards_a = "".join(_stock_card(s, as_of, conn) for s in section_a) or \
        '<p class="muted">No clean confirmations this run.</p>'
    cards_b = "".join(_stock_card(s, as_of, conn) for s in section_b) or \
        '<p class="muted">No confirmed-with-dissent stocks this run.</p>'
    cards_c = "".join(_stock_card(s, as_of, conn) for s in section_c) or \
        '<p class="muted">No conflicting opinions this run.</p>'
    table_d = _simple_table(section_d, conn)

    providers = conn.execute("SELECT COUNT(*) c FROM providers").fetchone()["c"]
    recs = conn.execute("SELECT COUNT(*) c FROM recommendations").fetchone()["c"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(cfg['project']['name'])} — {as_of}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <div class="sub">Daily research consensus report</div>
  <h1>TASI Analyst Intelligence</h1>
  <div class="header-meta">
    <span>Report date <b>{_e(as_of)}</b></span>
    <span>Rule <b>2 independent firms</b></span>
    <span>Freshness <b>{cfg['project']['freshness_days']} days</b></span>
    <span>Strong + clean <b>{len(section_a)}</b></span>
    <span>With dissent <b>{len(section_b)}</b></span>
    <span>Conflicting <b>{len(section_c)}</b></span>
    <span>Providers <b>{providers}</b></span>
    <span>Records <b>{recs}</b></span>
  </div>
</header>
{banner}
<h2>A · Strong and clean confirmations <span class="count">{len(section_a)}</span></h2>
{cards_a}
<h2>B · Confirmed with dissent <span class="count">{len(section_b)}</span></h2>
{cards_b}
<h2>C · Conflicting opinions <span class="count">{len(section_c)}</span></h2>
{cards_c}
<h2>D · Single opinions and unconfirmed stocks <span class="count">{len(section_d)}</span></h2>
{table_d}
<h2>E · Historical analyst performance</h2>
{_performance_section(conn, cfg)}
<footer>
  <p><b>Method.</b> Only current recommendations inside the freshness window count. Two analysts
  at the same research house count as one firm. The same recommendation republished on an
  aggregator and on the firm's own website counts once. A newer downgrade or withdrawal replaces
  the earlier view. Section A holds stocks with two or more positive firms and no negative
  opinions. Section B holds stocks with two or more positive firms where negatives exist but
  are not material (no negative target falls below the current price, and negatives are fewer
  than positives). Section C holds stocks with material disagreement — negative targets below
  the current price, or negatives matching or exceeding positives. Section D holds single
  opinions and stocks with no qualifying positive consensus. Target prices are split-adjusted
  when a corporate action occurred after publication; the raw published target is shown
  alongside the adjusted figure.</p>
  <p><b>Disclaimer.</b> Analyst consensus is not an investment recommendation and does not
  imply that a stock will rise. Ratings marked with <b>*</b> were mapped from non-standard
  wording and should be verified against the original report. Rows marked <b>SUSPICIOUS</b>
  have an implied upside above 100% and probably reflect an undetected split — verify against
  Tadawul corporate actions. Generated automatically by {_e(cfg['project']['name'])}.</p>
</footer>
</div></body></html>"""


def _card_markdown(sc: StockConsensus, as_of: str, conn=None) -> list[str]:
    comments = build_comments(sc, as_of, conn)
    v = valuation_summary(sc, conn)
    lines = [f"### {sc.company_name or sc.ticker} ({sc.ticker}) — {STATUS_LABEL[sc.status]}"]
    lines.append(f"{len(sc.positive)} positive · {len(sc.neutral)} neutral · {len(sc.negative)} negative · "
                 f"median target {v['median_target'] or '—'} SAR")
    for o in sc.positive + sc.neutral + sc.negative:
        lines.append(f"- {o.provider_name}: {o.rating_raw} · target {o.target_price or '—'} · "
                     f"{o.publication_date} · {o.source_url or 'no link'}")
    mat = material_negatives(conn, sc)
    if mat:
        lines.append("")
        lines.append("**Material downside disagreement:**")
        for m in mat:
            lines.append(f"- {m['firm']}: target SAR {m['target_adj']:.2f} vs current "
                         f"SAR {m['current_price']:.2f} ({m['downside_pct']:+.1f}%)")
    lines.append(f"\n**Consensus:** {comments['consensus']}")
    lines.append(f"**Valuation:** {comments['valuation']}")
    lines.append(f"**Risk:** {comments['risk']}\n")
    return lines


def build_markdown(results: list[StockConsensus], as_of: str, conn=None) -> str:
    section_a = highlighted(results)
    section_b = confirmed_with_dissent(results)
    section_c = conflicting(results)
    section_d = unconfirmed(results)

    lines = [f"# TASI Analyst Intelligence — {as_of}", "",
             f"**Strong + clean:** {len(section_a)} · **With dissent:** {len(section_b)} · "
             f"**Conflicting:** {len(section_c)} · **Unconfirmed:** {len(section_d)}", ""]

    lines.append(f"## A · Strong and clean confirmations ({len(section_a)})")
    if not section_a:
        lines.append("_None._")
    for sc in section_a:
        lines.extend(_card_markdown(sc, as_of, conn))

    lines.append(f"## B · Confirmed with dissent ({len(section_b)})")
    if not section_b:
        lines.append("_None._")
    for sc in section_b:
        lines.extend(_card_markdown(sc, as_of, conn))

    lines.append(f"## C · Conflicting opinions ({len(section_c)})")
    if not section_c:
        lines.append("_None._")
    for sc in section_c:
        lines.extend(_card_markdown(sc, as_of, conn))

    lines.append(f"## D · Single opinions and unconfirmed ({len(section_d)})\n")
    lines.append("| Stock | Positive | Neutral | Negative | Status |")
    lines.append("|---|---|---|---|---|")
    for sc in section_d:
        lines.append(f"| {sc.company_name or sc.ticker} ({sc.ticker}) | "
                     f"{len(sc.positive)} | {len(sc.neutral)} | {len(sc.negative)} | "
                     f"{STATUS_LABEL[sc.status]} |")

    if conn is not None:
        lines.append("\n## E · Historical analyst performance\n")
        perf_rows = perf_evaluate(conn, {
            "performance": {"horizons": [20, 60, 120], "min_sample_for_publication": 5}
        })
        if not perf_rows:
            lines.append("_No analyst performance can be computed yet._")
        else:
            any_bench = any(r["benchmark_available"] for r in perf_rows)
            if not any_bench:
                lines.append("> **Benchmark unavailable.** Hit-rate and excess-return columns "
                             "are blank because the TASI index feed returned no usable history.\n")
            agg = perf_aggregate(perf_rows, 5)
            lines.append("| Firm | Evaluated | Hit rate | Excess vs Saudi benchmark (KSA ETF proxy) | Status |")
            lines.append("|---|---|---|---|---|")
            for a in agg:
                hit = f"{a['hit_rate']:.1f}%" if a["hit_rate"] is not None else "—"
                exc = f"{a['mean_excess']:+.2f}%" if a["mean_excess"] is not None else "—"
                status = "publishable" if a["publishable"] else "sample too small"
                lines.append(f"| {a['provider']} | {a['evaluated']} "
                             f"({a['evaluated_vs_benchmark']} vs bench) | {hit} | {exc} | {status} |")

    return "\n".join(lines)


def build_excel(results: list[StockConsensus], path: Path, as_of: str, conn=None) -> None:
    import pandas as pd

    consensus_rows, opinion_rows, valuation_rows = [], [], []
    for sc in results:
        v = valuation_summary(sc, conn)
        q_label, _ = _quality_flag(v)
        mat = material_negatives(conn, sc)
        mat_detail = "; ".join(
            f"{m['firm']} {m['target_adj']:.2f} vs {m['current_price']:.2f} "
            f"({m['downside_pct']:+.1f}%)" for m in mat
        )

        consensus_rows.append({
            "Ticker": sc.ticker,
            "Company": sc.company_name,
            "Status": STATUS_LABEL[sc.status],
            "Positive firms": len(sc.positive),
            "Neutral firms": len(sc.neutral),
            "Negative firms": len(sc.negative),
            "Material negatives": len(mat),
            "Latest price": sc.latest_price,
            "Median target (raw)": v["median_target"],
            "Median target (adjusted)": v["median_target_adj"],
            "Implied upside now (%)": v["upside_now"],
            "Target dispersion (%)": v["dispersion_pct"],
            "Conflicting": sc.status == STATUS_CONFLICTING,
            "Split adjusted": v["split_adjusted"],
            "Data quality": q_label,
            "Material downside detail": mat_detail,
        })

        for o in sc.positive + sc.neutral + sc.negative:
            opinion_rows.append({
                "Ticker": sc.ticker,
                "Company": sc.company_name or sc.ticker,
                "Provider": o.provider_name,
                "Rating (raw)": o.rating_raw,
                "Classification": o.rating_norm,
                "Assumed mapping": o.rating_assumed,
                "Target": o.target_price,
                "Date": o.publication_date,
                "Source type": o.source_type,
                "Source URL": o.source_url,
                "Confidence": o.confidence,
                "Validation": o.validation_status,
            })

        for pf in v.get("per_firm", []):
            valuation_rows.append({
                "Ticker": sc.ticker,
                "Firm": pf["firm"],
                "Target (raw)": pf["target"],
                "Target (adjusted)": pf["target_adj"],
                "Split ratio applied": pf["split_ratio"],
                "Upside at publication (%)": pf["upside_at_publication"],
                "Upside vs latest (%)": pf["upside_now"],
            })

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(consensus_rows).to_excel(writer, sheet_name="Consensus", index=False)
        pd.DataFrame(opinion_rows).to_excel(writer, sheet_name="Opinions", index=False)
        pd.DataFrame(valuation_rows).to_excel(writer, sheet_name="Valuation", index=False)


def _force_foreground(window_class: str = "XLMAIN",
                     attempts: int = 10, interval: float = 0.5) -> bool:
    """
    Bring a window of the given class to the foreground. Retries for up to
    ~5 seconds so slow Excel startup is tolerated. No-ops on non-Windows.
    """
    import time
    for _ in range(attempts):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hwnd = user32.FindWindowW(window_class, None)
            if hwnd:
                user32.ShowWindow(hwnd, 9)               # SW_RESTORE
                if user32.GetForegroundWindow() == hwnd:
                    return True
                fg = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg, None)
                current_thread = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(current_thread, fg_thread, True)
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(current_thread, fg_thread, False)
                if user32.GetForegroundWindow() == hwnd:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _auto_open_files(written: dict[str, Path], which: list[str]) -> None:
    """Open the requested formats. TASI_NO_OPEN=1 suppresses opening."""
    if not which:
        return
    if os.environ.get("TASI_NO_OPEN", "").strip() in ("1", "true", "yes"):
        for fmt in which:
            path = written.get(fmt)
            if path and path.exists():
                print(f"  wrote {fmt} (auto-open suppressed): {path.name}")
        return

    for fmt in which:
        path = written.get(fmt)
        if not path or not path.exists():
            continue
        try:
            os.startfile(str(path))
            print(f"  opened {fmt}: {path.name}")
            if fmt == "xlsx":
                if _force_foreground("XLMAIN"):
                    print(f"  focused Excel window")
                else:
                    print(f"  Excel is open — click the taskbar icon if it is behind this window")
        except AttributeError:
            import subprocess
            import sys
            opener = {"darwin": "open"}.get(
                sys.platform,
                "xdg-open" if sys.platform.startswith("linux") else None,
            )
            if opener:
                try:
                    subprocess.Popen([opener, str(path)])
                except Exception:
                    pass
        except Exception as exc:                         # noqa: BLE001
            print(f"  could not auto-open {fmt}: {exc}")


def write_reports(conn, results, cfg, as_of=None) -> dict[str, Path]:
    as_of = as_of or dt.date.today().isoformat()
    out_dir = Path(cfg["_root"]) / cfg["project"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = f"TASI_Analyst_Report_{stamp}"

    formats = cfg["reporting"]["formats"]
    if "html" in formats:
        p = out_dir / f"{base}.html"
        p.write_text(build_html(conn, results, cfg, as_of), encoding="utf-8")
        written["html"] = p
    if "md" in formats:
        p = out_dir / f"{base}.md"
        p.write_text(build_markdown(results, as_of, conn), encoding="utf-8")
        written["md"] = p
    if "xlsx" in formats:
        p = out_dir / f"{base}.xlsx"
        build_excel(results, p, as_of, conn)
        written["xlsx"] = p

    auto_open = cfg.get("reporting", {}).get("auto_open", ["xlsx"])
    _auto_open_files(written, auto_open)

    return written