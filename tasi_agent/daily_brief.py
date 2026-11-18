"""
Daily trading brief generator.

Renders scan results as a complete trading plan:
  - Market verdict
  - Market snapshot
  - Action plan
  - Watch List (setups that fired, ranked cross-sectionally)
  - Analyst-Confirmed Stocks (context with triggers)

Saved as HTML + Markdown + CSV.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd


VERDICT_TRADE = "TRADE"
VERDICT_CAUTION = "CAUTION"
VERDICT_NO_TRADE = "NO_TRADE"


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def verdict_from_regime(regime: str, confidence: float) -> tuple[str, str]:
    """Return (verdict, plain-English summary)."""
    if regime == "STRONG_BULLISH":
        return (VERDICT_TRADE,
                f"Market strong bullish (confidence {confidence:.0%}). "
                f"Full-size entries permitted.")
    if regime == "BULLISH":
        return (VERDICT_TRADE,
                f"Market bullish (confidence {confidence:.0%}). "
                f"Entries permitted, watch position sizing.")
    if regime == "SIDEWAYS":
        return (VERDICT_CAUTION,
                f"Market sideways (confidence {confidence:.0%}). "
                f"New entries only on conviction setups - reduce size by 50%.")
    if regime == "BEARISH":
        return (VERDICT_NO_TRADE,
                f"Market bearish (confidence {confidence:.0%}). "
                f"No new long positions.")
    if regime == "HIGH_VOL_BEARISH":
        return (VERDICT_NO_TRADE,
                f"High-volatility bearish (confidence {confidence:.0%}). "
                f"No new long positions. Reduce existing exposure.")
    return (VERDICT_CAUTION, f"Unclear regime: {regime}")


# ---------------------------------------------------------------------------
# Trigger conditions
# ---------------------------------------------------------------------------
def trigger_conditions_for_setup(row: dict, regime: dict) -> list[str]:
    """Return list of specific conditions that would turn WATCH into BUY_NOW."""
    conditions: list[str] = []
    setup = row.get("setup")
    entry = row.get("plan", {}).get("entry")

    regime_name = regime.get("regime")
    if regime_name in ("SIDEWAYS", "BEARISH", "HIGH_VOL_BEARISH"):
        m = regime.get("metrics", {})
        if regime_name == "SIDEWAYS":
            ma_long = m.get("ma_long") or 0
            conditions.append(
                f"Market must shift from SIDEWAYS to BULLISH: "
                f"TASI close above {ma_long * 1.02:.2f} "
                f"with breadth@20 > 55%"
            )
        else:
            conditions.append(f"Market must shift from {regime_name} to BULLISH")

    if setup == "volume_breakout":
        conditions.append(
            f"Close above {entry:.2f} with volume > 1.5x prior 20-day avg"
        )
    elif setup == "bollinger_squeeze":
        conditions.append(
            f"Close above upper Bollinger band (~{entry:.2f}) with volume expansion"
        )
    elif setup == "macd_crossover":
        conditions.append(
            "MACD histogram remains positive for 3 consecutive sessions "
            "with price above EMA50"
        )
    elif setup == "ema20_pullback":
        conditions.append(
            "Pullback to EMA20 zone, then bullish close above EMA20 "
            "on above-average volume"
        )
    elif setup == "rsi_recovery":
        conditions.append(f"RSI(14) closes above 50 with price above {entry:.2f}")
    elif setup == "20d_high_volume":
        conditions.append(
            "Close makes new 20-day high with volume > prior 20-day avg"
        )
    else:
        conditions.append("Setup-specific confirmation conditions met")

    if row.get("score", 0) < 60:
        conditions.append(
            f"Opportunity score must reach 60+ (currently {row.get('score', 0):.0f})"
        )

    return conditions


# ---------------------------------------------------------------------------
# Setup block renderer
# ---------------------------------------------------------------------------
def format_setup_block(row: dict, regime: dict, index: int,
                       capital: float, risk_pct: float) -> str:
    """Format one setup as a readable text block."""
    p = row["plan"]
    s = row["sizing"]
    score_detail = row.get("score_detail", {})
    action = row.get("action", "WATCH")

    icon = {
        "BUY_NOW": "[BUY]",
        "WATCH": "[WATCH]",
        "BUY_ON_BREAKOUT": "[FORMING]",
        "BUY_ON_PULLBACK": "[FORMING]",
        "AVOID": "[AVOID]",
        "NO_TRADE_MARKET": "[NO TRADE]",
    }.get(action, "[?]")

    lines = [f"{index}. {row['ticker']} {row['company']} - {row['setup']}"]
    lines.append(f"   Score: {row['score']:.0f}/100  |  Action: {icon} {action}")

    # Universe rank (cross-sectional position) - only if data is present
    rank_comp = row.get("rank_composite")
    if rank_comp is not None and pd.notna(rank_comp):
        rank_6m = row.get("rank_6M")
        rank_rs = row.get("rank_rs")
        r6 = (f"{rank_6m:.0f}"
              if (rank_6m is not None and pd.notna(rank_6m)) else "-")
        rrs = (f"{rank_rs:.0f}"
               if (rank_rs is not None and pd.notna(rank_rs)) else "-")
        lines.append(f"   Universe rank: {rank_comp:.0f}/100 "
                     f"(6M mom {r6}, RS {rrs})")

    lines.append("")

    # Strong vs weak components
    strong, weak = [], []
    for comp, d in score_detail.items():
        if d.get("score") is None or d.get("max") is None:
            continue
        if d["score"] >= d["max"] * 0.7:
            strong.append(f"{comp} {d['score']:.0f}/{d['max']}")
        elif d["score"] <= d["max"] * 0.3:
            weak.append(f"{comp} {d['score']:.0f}/{d['max']}")
    if strong:
        lines.append(f"   Strong: {', '.join(strong)}")
    if weak:
        lines.append(f"   Weak:   {', '.join(weak)}")
    lines.append("")

    # Trade plan
    entry = p["entry"]
    stop = p["stop"]
    t1 = p["target_1"]
    t2 = p["target_2"]
    risk = entry - stop
    rr1 = (t1 - entry) / risk if risk > 0 else 0.0

    lines.append("   TRADE PLAN")
    lines.append(f"      Entry    : {entry:.2f}")
    lines.append(f"      Stop     : {stop:.2f}  "
                 f"({(stop / entry - 1) * 100:+.1f}%)")
    lines.append(f"      Target 1 : {t1:.2f}  "
                 f"({(t1 / entry - 1) * 100:+.1f}%)  R:R 1:{rr1:.1f}")
    lines.append(f"      Target 2 : {t2:.2f}  "
                 f"({(t2 / entry - 1) * 100:+.1f}%)")
    lines.append(f"      ATR      : {p['atr']:.2f} SAR")

    # Position size
    if s.get("valid"):
        lines.append("")
        lines.append(f"   POSITION SIZE (capital {capital:,.0f} SAR, "
                     f"risk {risk_pct * 100:.1f}%)")
        lines.append(f"      Shares   : {s['shares']}")
        lines.append(f"      Value    : {s['position_value']:,.0f} SAR")
        lines.append(f"      Max loss : {s['max_loss']:,.0f} SAR  "
                     f"({s['actual_risk_pct'] * 100:.2f}% of capital)")
    else:
        lines.append(f"   Cannot size: {s.get('reason', 'unknown')}")

    # Trigger conditions
    triggers = trigger_conditions_for_setup(row, regime)
    if triggers:
        lines.append("")
        lines.append("   TO TRIGGER (all must be true)")
        for t in triggers:
            lines.append(f"      - {t}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analyst-confirmed technical diagnosis
# ---------------------------------------------------------------------------
def diagnose_setup_gap(last_row) -> tuple[str, str]:
    """Diagnose why an analyst-confirmed stock has no technical setup firing."""
    close = float(last_row["close"])

    def _get(col):
        v = last_row.get(col)
        return float(v) if v is not None and pd.notna(v) else None

    ema20 = _get("ema_20")
    ema50 = _get("ema_50")
    rsi = _get("rsi_14")
    adx = _get("adx")
    plus_di = _get("plus_di")
    minus_di = _get("minus_di")
    vol_ratio = _get("volume_ratio_20")
    bb_upper = _get("bb_upper")

    if vol_ratio is None:
        vol_ratio = 1.0

    below_emas = (ema20 is not None and ema50 is not None
                  and close < ema20 and close < ema50)
    above_emas = (ema20 is not None and ema50 is not None
                  and close > ema20 and close > ema50)
    bearish_di = (plus_di is not None and minus_di is not None
                  and minus_di > plus_di)

    if below_emas and adx is not None and adx > 25 and bearish_di:
        return ("Downtrend - close below EMA20/EMA50, ADX > 25",
                f"Reclaim EMA20 ({ema20:.2f}) and EMA50 ({ema50:.2f}) "
                f"with ADX turning up")

    if below_emas:
        e20 = f"{ema20:.2f}" if ema20 is not None else "EMA20"
        e50 = f"{ema50:.2f}" if ema50 is not None else "EMA50"
        return ("No trend - close below MAs, ADX weak",
                f"Close above {e20} or {e50} with volume")

    if above_emas and vol_ratio < 1.2:
        return (f"Uptrend but volume weak ({vol_ratio:.2f}x)",
                f"Pullback to EMA20 ({ema20:.2f}) with volume surge on reclaim")

    if above_emas:
        if bb_upper is not None and (bb_upper - close) / close < 0.05:
            return (f"Approaching upper BB ({bb_upper:.2f})",
                    f"Close above BB upper {bb_upper:.2f} with volume expansion")
        return ("Uptrend but no specific setup triggered today",
                "Break to new 20d high with above-average volume")

    if rsi is not None and rsi < 40:
        return (f"Weak momentum (RSI {rsi:.0f})",
                "RSI recovers above 50 with bullish close")

    return ("No clear technical signal",
            "Wait for close > EMA20 > EMA50 with volume > 1.2x")


def scan_analyst_stocks_for_technical_status(conn, analyst_stocks: list[dict],
                                              setups: list) -> list[dict]:
    """Enrich each analyst-confirmed stock with technical diagnosis."""
    try:
        from tasi_agent.indicators import compute_all
    except ImportError:
        return [dict(s, close=None, technical_state="indicators unavailable",
                     trigger="-", setups_fired=[]) for s in analyst_stocks]

    out = []
    for sc in analyst_stocks:
        ticker = sc["ticker"]
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? AND open IS NOT NULL AND volume IS NOT NULL "
            "ORDER BY date ASC",
            (ticker,),
        ).fetchall()

        if len(rows) < 60:
            out.append(dict(sc, close=None,
                            technical_state="insufficient data",
                            trigger="Need >= 60 bars of OHLCV",
                            setups_fired=[]))
            continue

        df = pd.DataFrame([dict(r) for r in rows]).set_index("date")
        ind = compute_all(df)
        last = ind.iloc[-1]

        fired = []
        for setup_name, detect_fn in setups:
            try:
                sig = detect_fn(ind)
                if bool(sig.iloc[-1]):
                    fired.append(setup_name)
            except Exception:
                pass

        if fired:
            state = f"Setup fired: {', '.join(fired)}"
            trigger = "Already in scan result"
        else:
            state, trigger = diagnose_setup_gap(last)

        out.append(dict(sc,
                        close=float(last["close"]),
                        technical_state=state,
                        trigger=trigger,
                        setups_fired=fired))

    return out


# ---------------------------------------------------------------------------
# Build the brief data structure
# ---------------------------------------------------------------------------
def build_brief(results: list[dict], regime_info: dict,
                capital: float, risk_pct: float,
                analyst_stocks: list[dict] | None = None,
                as_of: str | None = None) -> dict:
    """Assemble the brief payload for renderers."""
    as_of = as_of or dt.date.today().isoformat()
    regime = regime_info.get("regime", "SIDEWAYS")
    confidence = regime_info.get("confidence", 0.5)
    metrics = regime_info.get("metrics", {})

    verdict, verdict_text = verdict_from_regime(regime, confidence)

    buy_now = [r for r in results if r.get("action") == "BUY_NOW"]
    watch = [r for r in results if r.get("action") == "WATCH"]
    forming = [r for r in results
               if r.get("action") in ("BUY_ON_BREAKOUT", "BUY_ON_PULLBACK")]
    avoid = [r for r in results
             if r.get("action") in ("AVOID", "NO_TRADE_MARKET")]

    total_possible_risk = sum(
        r["sizing"]["max_loss"] for r in watch
        if r.get("sizing", {}).get("valid")
    )
    total_possible_deploy = sum(
        r["sizing"]["position_value"] for r in watch
        if r.get("sizing", {}).get("valid")
    )

    return {
        "as_of": as_of,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "regime": regime,
        "confidence": confidence,
        "metrics": metrics,
        "capital": capital,
        "risk_pct": risk_pct,
        "buy_now": buy_now,
        "watch": watch,
        "forming": forming,
        "avoid": avoid,
        "total_possible_risk": total_possible_risk,
        "total_possible_deploy": total_possible_deploy,
        "all_results": results,
        "analyst_stocks": analyst_stocks or [],
    }


def _verdict_icon(verdict: str) -> str:
    return {
        VERDICT_TRADE: "[TRADE]",
        VERDICT_CAUTION: "[CAUTION]",
        VERDICT_NO_TRADE: "[NO TRADE]",
    }.get(verdict, "[?]")


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def render_markdown(brief: dict) -> str:
    """Render the brief as clean markdown."""
    lines = []
    lines.append(f"# TASI Daily Trading Brief - {brief['as_of']}")
    lines.append("")
    lines.append(f"## {_verdict_icon(brief['verdict'])} VERDICT: {brief['verdict']}")
    lines.append("")
    lines.append(f"**{brief['verdict_text']}**")
    lines.append("")

    # Market snapshot
    m = brief["metrics"]
    lines.append("### Market Snapshot")
    lines.append("")
    if not m.get("insufficient_index_data"):
        lines.append("| Metric | Value | Reading |")
        lines.append("|---|---|---|")
        close_v = m.get('close')
        ma_long_v = m.get('ma_long')
        close_vs = 'above' if (close_v or 0) > (ma_long_v or 0) else 'below'
        lines.append(f"| TASI close | {close_v} | {close_vs} MA50 |")

        ma_short = m.get('ma_short') or 0
        ma_long = m.get('ma_long') or 0
        ma_rel = '>' if ma_short > ma_long else '<'
        lines.append(f"| MA20 / MA50 | {ma_short} / {ma_long} | short {ma_rel} long |")

        b20 = (m.get('breadth_ma20') or 0) * 100
        b50 = (m.get('breadth_ma50') or 0) * 100
        b20_r = 'healthy' if b20 > 55 else ('weak' if b20 < 40 else 'moderate')
        b50_r = 'healthy' if b50 > 50 else ('WEAK' if b50 < 30 else 'moderate')
        lines.append(f"| Breadth @ MA20 | {b20:.0f}% | {b20_r} |")
        lines.append(f"| Breadth @ MA50 | {b50:.0f}% | {b50_r} |")

        vr = m.get('vol_ratio', 1.0)
        vr_r = 'elevated' if vr > 1.2 else ('normal' if vr < 1.0 else 'moderate')
        lines.append(f"| Volatility | {vr:.2f}x long-run | {vr_r} |")
    lines.append("")

    # Action plan
    lines.append("### Action Plan")
    lines.append("")
    lines.append(f"- **Buy now:** {len(brief['buy_now'])} setups")
    lines.append(f"- **Watch (wait for trigger):** {len(brief['watch'])} setups")
    lines.append(f"- **Forming (no trigger yet):** {len(brief['forming'])} setups")
    lines.append(f"- **Avoid:** {len(brief['avoid'])} stocks")
    lines.append("")

    if brief["total_possible_risk"] > 0:
        pct = brief["total_possible_risk"] / brief["capital"] * 100
        deploy_pct = brief["total_possible_deploy"] / brief["capital"] * 100
        lines.append(f"**Portfolio projection if all watches triggered:** "
                     f"{brief['total_possible_risk']:,.0f} SAR risk "
                     f"({pct:.1f}% of capital), "
                     f"{brief['total_possible_deploy']:,.0f} SAR deployed "
                     f"({deploy_pct:.0f}% exposure)")
        lines.append("")

    # Buy now
    if brief["buy_now"]:
        lines.append("## Buy Now")
        lines.append("")
        for i, row in enumerate(brief["buy_now"], 1):
            lines.append("```")
            lines.append(format_setup_block(row, brief, i,
                                             brief["capital"], brief["risk_pct"]))
            lines.append("```")
            lines.append("")

    # Watch
    if brief["watch"]:
        lines.append("## Watch List - Wait For Trigger")
        lines.append("")
        lines.append("The following setups are valid but require confirmation. "
                     "Do not enter until all trigger conditions are met.")
        lines.append("")
        for i, row in enumerate(brief["watch"], 1):
            lines.append("```")
            lines.append(format_setup_block(row, brief, i,
                                             brief["capital"], brief["risk_pct"]))
            lines.append("```")
            lines.append("")

    # Forming
    if brief["forming"]:
        lines.append("## Forming Setups")
        lines.append("")
        for row in brief["forming"]:
            p = row["plan"]
            lines.append(f"- **{row['ticker']} {row['company']}** - "
                         f"{row['setup']} (score {row['score']:.0f}) - "
                         f"entry {p['entry']:.2f}, stop {p['stop']:.2f}")
        lines.append("")

    # Avoid
    if brief["avoid"]:
        lines.append("## Avoid")
        lines.append("")
        for row in brief["avoid"]:
            lines.append(f"- **{row['ticker']} {row['company']}** - "
                         f"{row.get('action_reason', '')}")
        lines.append("")

    # Analyst-confirmed
    if brief["analyst_stocks"]:
        lines.append("## Analyst-Confirmed Stocks - Technical Status")
        lines.append("")
        lines.append("The following stocks have 2+ independent positive analyst "
                     "ratings (Sections A/B of the analyst report). They were "
                     "scanned today. None fires a technical setup, so none appears "
                     "in the Watch List. This section explains why and shows what "
                     "would trigger entry.")
        lines.append("")

        lines.append("| Ticker | Company | Analyst | Close | Technical State |")
        lines.append("|---|---|---|---|---|")
        for s in brief["analyst_stocks"]:
            close_str = (f"{s['close']:.2f}"
                         if s.get("close") is not None else "-")
            state = s.get("technical_state", "-")
            if s.get("setups_fired"):
                state = f"FIRED: {', '.join(s['setups_fired'])}"
            analyst_str = (s.get('status', '').replace('_', ' ')
                           + f" ({s.get('firms_positive', 0)})")
            lines.append(f"| {s['ticker']} | {s.get('company', '')} | "
                         f"{analyst_str} | {close_str} | {state} |")
        lines.append("")

        lines.append("### What each needs to enter the daily brief")
        lines.append("")
        for s in brief["analyst_stocks"]:
            if s.get("setups_fired"):
                continue
            close_str = (f"{s['close']:.2f}"
                         if s.get("close") is not None else "-")
            lines.append(f"**{s['ticker']} {s.get('company', '')}** "
                         f"(currently {close_str})")
            lines.append(f"- State: {s.get('technical_state', '-')}")
            lines.append(f"- To trigger: {s.get('trigger', '-')}")
            lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("**How to use this brief:**")
    lines.append("")
    lines.append("1. Read the VERDICT first. NO_TRADE means do not enter any new "
                 "position.")
    lines.append("2. **Watch List** shows setups that fired but need additional "
                 "confirmation. All trigger conditions must be true before entry.")
    lines.append("3. **Analyst-Confirmed Stocks** shows stocks with 2+ analyst buy "
                 "ratings that are not in the Watch List. They are context: strong "
                 "fundamental support but no technical entry signal. Do not enter "
                 "until trigger fires.")
    lines.append("4. Position sizes assume full deployment per trigger. Do not "
                 "exceed 5 simultaneous positions or 5% total risk.")
    lines.append("5. This is not investment advice. Verify every setup before "
                 "trading.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------
def render_html(brief: dict) -> str:
    """Render the brief as a clean HTML document."""
    md = render_markdown(brief)

    html_lines: list[str] = []
    in_table = False
    in_code = False

    for raw_line in md.split("\n"):
        line = raw_line.rstrip()

        # --- Code fence toggle -----------------------------------
        if line.startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                if in_table:
                    html_lines.append("</table>")
                    in_table = False
                html_lines.append("<pre><code>")
                in_code = True
            continue

        # --- Inside code block: emit raw, no markup ---------------
        if in_code:
            html_lines.append(line)
            continue

        # --- Table separator row ---------------------------------
        stripped = line.replace(" ", "")
        if (stripped.startswith("|")
                and set(stripped) <= {"|", "-"}):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            continue

        # --- Table data row --------------------------------------
        if (line.startswith("|")
                and line.endswith("|")
                and line.count("|") >= 2):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            cells = [c.strip() for c in line.split("|")[1:-1]]
            row_html = ("<tr>"
                        + "".join(f"<td>{c}</td>" for c in cells)
                        + "</tr>")
            html_lines.append(row_html)
            continue

        # --- Close any open table before other elements ----------
        if in_table:
            html_lines.append("</table>")
            in_table = False

        # --- Horizontal rule -------------------------------------
        if line.strip() == "---":
            html_lines.append("<hr>")
            continue

        # --- Headings --------------------------------------------
        if line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.strip() == "":
            html_lines.append("")
        else:
            formatted = line
            while "**" in formatted:
                formatted = formatted.replace("**", "<b>", 1)
                formatted = formatted.replace("**", "</b>", 1)
            html_lines.append(f"<p>{formatted}</p>")

    if in_table:
        html_lines.append("</table>")
    if in_code:
        html_lines.append("</code></pre>")

    body = "\n".join(html_lines)

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>TASI Daily Brief - {brief['as_of']}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        max-width: 960px; margin: 30px auto; padding: 20px;
        background: #fafbfc; color: #1a2733; line-height: 1.5; }}
h1 {{ color: #0d1b2a; border-bottom: 2px solid #dbe3ea; padding-bottom: 8px; }}
h2 {{ color: #1b7f4b; margin-top: 30px; }}
h3 {{ color: #4a5c6b; margin-top: 20px; }}
pre {{ background: #f4f6f8; border-left: 3px solid #4a9d7a;
       padding: 12px 16px; border-radius: 4px; font-size: 13px;
       white-space: pre-wrap; overflow-x: auto; margin: 12px 0; }}
code {{ font-family: "Cascadia Mono", "Consolas", monospace; }}
table {{ border-collapse: collapse; margin: 12px 0; width: 100%; }}
td {{ border: 1px solid #e1e8ee; padding: 6px 10px; font-size: 13px; }}
tr:first-child td {{ background: #eef3f7; font-weight: 600; }}
hr {{ border: none; border-top: 1px solid #dbe3ea; margin: 24px 0; }}
p {{ margin: 8px 0; }}
b {{ font-weight: 600; }}
</style>
</head><body>
{body}
</body></html>"""


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def save_brief(brief: dict, output_dir: Path) -> dict[str, Path]:
    """Save brief to HTML, MD, CSV. Return dict of paths."""
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = f"TASI_Daily_Brief_{stamp}"

    paths: dict[str, Path] = {}

    md_path = output_dir / f"{base}.md"
    md_path.write_text(render_markdown(brief), encoding="utf-8")
    paths["md"] = md_path

    html_path = output_dir / f"{base}.html"
    html_path.write_text(render_html(brief), encoding="utf-8")
    paths["html"] = html_path

    if brief["all_results"]:
        rows = []
        for r in brief["all_results"]:
            p = r["plan"]
            s = r["sizing"]
            rows.append({
                "ticker": r["ticker"],
                "company": r["company"],
                "setup": r["setup"],
                "score": r["score"],
                "action": r["action"],
                "rank_composite": r.get("rank_composite"),
                "rank_6M": r.get("rank_6M"),
                "rank_rs": r.get("rank_rs"),
                "entry": p["entry"],
                "stop": p["stop"],
                "target_1": p["target_1"],
                "target_2": p["target_2"],
                "atr": p["atr"],
                "shares": s["shares"] if s["valid"] else 0,
                "position_value": s["position_value"] if s["valid"] else 0,
                "max_loss": s["max_loss"] if s["valid"] else 0,
                "risk_pct": s["actual_risk_pct"] if s["valid"] else 0,
                "action_reason": r.get("action_reason", ""),
            })
        csv_path = output_dir / f"{base}.csv"
        pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
        paths["csv"] = csv_path

    return paths