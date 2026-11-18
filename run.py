#!/usr/bin/env python3
"""
TASI Analyst Consensus Intelligence Agent - command line entry point.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from tasi_agent import __version__
from tasi_agent.collectors import (aljazira, argaam_pages, demo, euroland_ir,
                                   pdf_inbox, yahoo_prices)
from tasi_agent.config import db_path, load
from tasi_agent.consensus import (STATUS_LABEL, build_consensus,
                                  confirmed_with_dissent, conflicting,
                                  highlighted)
from tasi_agent.db import connect, init_db, log
from tasi_agent.ingest import ingest_items
from tasi_agent.performance import aggregate, evaluate
from tasi_agent.registry import seed_providers
from tasi_agent.report import write_reports


COLLECTOR_CHOICES = ("argaam", "argaam_pages", "euroland", "aljazira",
                     "pdf", "prices", "demo")


def _safe_float(v):
    """Return float or None, treating NaN as None."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _collector(source: str, cfg: dict, debug: bool = False):
    if source in ("argaam", "argaam_pages"):
        return argaam_pages.ArgaamPagesCollector(cfg, debug=debug)
    if source == "euroland":
        return euroland_ir.EurolandIrCollector(cfg)
    if source == "aljazira":
        return aljazira.AlJaziraCollector(cfg)
    if source == "pdf":
        return pdf_inbox.PdfInboxCollector(cfg)
    if source == "prices":
        return yahoo_prices.YahooPriceCollector(cfg)
    if source == "demo":
        return demo.DemoCollector(cfg)
    raise SystemExit(f"unknown source: {source!r}; choose from {COLLECTOR_CHOICES}")


def _enabled_default_targets(cfg: dict) -> list[str]:
    targets: list[str] = []
    coll = cfg.get("collectors", {})
    if coll.get("argaam", {}).get("enabled"):
        targets.append("argaam")
    if coll.get("euroland", {}).get("enabled"):
        targets.append("euroland")
    if coll.get("aljazira", {}).get("enabled"):
        targets.append("aljazira")
    if coll.get("pdf_inbox", {}).get("enabled", True):
        targets.append("pdf")
    return targets


def cmd_init(cfg: dict) -> int:
    conn = init_db(db_path(cfg))
    added = seed_providers(conn)
    print(f"Database ready at {db_path(cfg)}")
    print(f"Providers seeded: {added}")
    return 0


def cmd_demo(cfg: dict) -> int:
    conn = init_db(db_path(cfg))
    seed_providers(conn)
    stats = ingest_items(conn, demo.DemoCollector(cfg).fetch(), cfg)
    demo.seed_prices(conn)
    print("Demo seed complete:", stats)
    return 0


def cmd_collect(cfg: dict, source: str | None, debug: bool = False) -> int:
    conn = init_db(db_path(cfg))
    seed_providers(conn)

    targets = [source] if source else _enabled_default_targets(cfg)
    if not targets:
        print("No collectors enabled. Set collectors.<name>.enabled: true in config.yaml, "
              "or pass --source <name>.")
        return 1

    total = {"seen": 0, "inserted": 0, "duplicate": 0,
             "promoted": 0, "rejected": 0, "review": 0}

    for name in targets:
        print(f"\n=== {name} ===")
        try:
            collector = _collector(name, cfg, debug=debug)
            store_fn = getattr(collector, "store", None)
            if callable(store_fn):
                store_fn(conn)
                continue
            items = collector.fetch()
        except Exception as exc:
            log(conn, f"collect:{name}", f"collector failed: {exc}", level="error")
            print(f"  FAILED: {exc}")
            continue

        print(f"  fetched raw items: {len(items)}")
        if not items:
            continue

        stats = ingest_items(conn, items, cfg)
        for k in total:
            total[k] += stats[k]
        print(f"  ingest stats: {stats}")

    print(f"\nTOTAL: {total}")
    return 0


def cmd_report(cfg: dict, as_of: str | None) -> int:
    conn = init_db(db_path(cfg))
    as_of = as_of or dt.date.today().isoformat()

    results = build_consensus(conn, cfg, as_of)
    section_a = highlighted(results)
    section_b = confirmed_with_dissent(results)
    section_c = conflicting(results)

    written = write_reports(conn, results, cfg, as_of)

    print(f"\nReport date: {as_of}")
    print(f"Stocks analysed: {len(results)}   "
          f"Clean: {len(section_a)}   "
          f"With dissent: {len(section_b)}   "
          f"Conflicting: {len(section_c)}")

    if section_a:
        print("\nSection A - Strong and clean confirmations")
        for sc in section_a:
            print(f"  [A] {sc.ticker:>4} {(sc.company_name or ''):<28} "
                  f"{STATUS_LABEL[sc.status]:<32} ({len(sc.positive)} firms)")

    if section_b:
        print("\nSection B - Confirmed with dissent")
        for sc in section_b:
            print(f"  [B] {sc.ticker:>4} {(sc.company_name or ''):<28} "
                  f"({len(sc.positive)} pos, {len(sc.negative)} neg)")

    if section_c:
        print("\nSection C - Conflicting opinions")
        for sc in section_c:
            print(f"  [C] {sc.ticker:>4} {(sc.company_name or ''):<28} "
                  f"({len(sc.positive)} pos, {len(sc.neutral)} neu, "
                  f"{len(sc.negative)} neg)")

    for label, path in written.items():
        print(f"  {label.upper():<4} {path}")
    return 0


def cmd_perf(cfg: dict) -> int:
    conn = init_db(db_path(cfg))
    rows = evaluate(conn, cfg)
    if not rows:
        print("No evaluable recommendations yet.")
        print("Run: python run.py collect --source prices")
        return 0
    agg = aggregate(rows, cfg["performance"]["min_sample_for_publication"])
    print(f"{'Firm':<28}{'N':>5}{'vsBench':>8}{'Hit %':>9}{'Excess %':>11}  Status")
    for a in agg:
        hit = f"{a['hit_rate']:.1f}" if a["hit_rate"] is not None else "-"
        exc = f"{a['mean_excess']:+.2f}" if a["mean_excess"] is not None else "-"
        flag = "publishable" if a["publishable"] else "sample too small"
        print(f"{a['provider']:<28}{a['evaluated']:>5}"
              f"{a['evaluated_vs_benchmark']:>8}{hit:>9}{exc:>11}  {flag}")
    return 0


def cmd_dashboard(cfg: dict) -> int:
    import subprocess
    project_root = str(Path(__file__).parent)
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run",
         str(Path(project_root) / "tasi_agent" / "dashboard.py")],
        cwd=project_root,
    )


def cmd_doctor(cfg: dict) -> int:
    print(f"Version            : {__version__}")
    print(f"Project root       : {cfg['_root']}")
    print(f"Database           : {db_path(cfg)} (exists={db_path(cfg).exists()})")

    print("\nDependencies:")
    for mod, label in [
        ("yaml", "PyYAML"), ("bs4", "BeautifulSoup"), ("lxml", "lxml"),
        ("requests", "requests"), ("pandas", "pandas"), ("openpyxl", "openpyxl"),
        ("fitz", "PyMuPDF"), ("streamlit", "streamlit"),
        ("playwright", "playwright"), ("yfinance", "yfinance"),
        ("numpy", "numpy"),
    ]:
        try:
            __import__(mod)
            print(f"  {label:<20} ok")
        except ImportError:
            print(f"  {label:<20} MISSING")

    print("\nCollectors:")
    for mod in ("argaam_pages", "euroland_ir", "aljazira", "pdf_inbox",
                "yahoo_prices", "demo"):
        try:
            __import__(f"tasi_agent.collectors.{mod}")
            print(f"  {mod:<20} ok")
        except ImportError as e:
            print(f"  {mod:<20} MISSING ({e})")

    print("\nData inventory:")
    try:
        conn = connect(db_path(cfg))
        for tbl in ("recommendations", "providers", "prices",
                    "index_prices", "splits", "watchlist",
                    "stock_events", "monitor_runs"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"  {tbl:<16} {n:>6} rows")
            except Exception:
                print(f"  {tbl:<16} (table missing)")

        try:
            total = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM prices "
                "WHERE open IS NOT NULL AND high IS NOT NULL "
                "AND low IS NOT NULL AND volume IS NOT NULL"
            ).fetchone()[0]
            if total:
                pct = complete / total * 100
                print(f"  OHLCV complete   {complete:>6}/{total} ({pct:.1f}%)")
        except Exception:
            pass
    except Exception as exc:
        print(f"  could not open DB: {exc}")

    print("\nConfig summary:")
    print(f"  freshness_days       : {cfg['project']['freshness_days']}")
    print(f"  confirm_threshold    : {cfg['consensus']['confirm_threshold']}")
    print(f"  strong_threshold     : {cfg['consensus']['strong_threshold']}")
    for name in ("argaam", "euroland", "aljazira", "pdf_inbox"):
        c = cfg.get("collectors", {}).get(name, {})
        print(f"  collectors.{name:<12} enabled={c.get('enabled')}")
    print(f"  reporting.auto_open  : {cfg['reporting'].get('auto_open', ['xlsx'])}")
    return 0


def cmd_run_all(cfg: dict) -> int:
    cmd_collect(cfg, None)
    cmd_report(cfg, None)
    return 0


def cmd_watch_add(cfg: dict, ticker: str, notes: str, level: str) -> int:
    from tasi_agent.monitor import add_to_watchlist
    conn = init_db(db_path(cfg))
    if add_to_watchlist(conn, ticker, notes, level):
        print(f"Added {ticker} to watchlist (alert level: {level})")
        return 0
    print(f"Cannot add {ticker}: ticker not found in recommendations table. "
          f"Run a collection first or verify the ticker.")
    return 1


def cmd_watch_remove(cfg: dict, ticker: str) -> int:
    from tasi_agent.monitor import remove_from_watchlist
    conn = init_db(db_path(cfg))
    if remove_from_watchlist(conn, ticker):
        print(f"Removed {ticker} from watchlist")
        return 0
    print(f"{ticker} was not on the watchlist")
    return 1


def cmd_watch_list(cfg: dict) -> int:
    from tasi_agent.monitor import list_watchlist
    conn = init_db(db_path(cfg))
    rows = list_watchlist(conn)
    if not rows:
        print("Watchlist is empty. Add stocks with: run.py watch add TICKER")
        return 0
    print(f"{'Ticker':<8}{'Company':<32}{'Alert':<10}{'Added':<22}Notes")
    print("-" * 100)
    for r in rows:
        print(f"{r['ticker']:<8}{(r['company_name'] or '')[:30]:<32}"
              f"{r['alert_level']:<10}{(r['added_at'] or '')[:19]:<22}"
              f"{r['notes'] or ''}")
    return 0


def cmd_monitor(cfg: dict, dry_run: bool = False) -> int:
    from tasi_agent.monitor import (mark_notified, pending_alerts,
                                    run_monitor)
    from tasi_agent.notifier import dispatch

    conn = init_db(db_path(cfg))
    summary = run_monitor(conn, cfg, dry_run=dry_run)

    print(f"\nMonitor summary: {summary['tickers']} stocks, "
          f"{summary['events']} new event(s)")
    print(f"  Pending alerts: {summary['alerts']}")
    if summary["errors"]:
        print(f"  Errors: {len(summary['errors'])}")
        for err in summary["errors"][:5]:
            print(f"    {err}")

    alerts = pending_alerts(conn)
    if not alerts:
        print("  (No pending alerts - staying quiet.)")
        return 0

    print(f"\n=== Delivering {len(alerts)} alert(s) ===\n")
    delivered = dispatch(
        alerts,
        project_root=Path(cfg["_root"]),
        mark_cb=(lambda eid: mark_notified(conn, eid)) if not dry_run else None,
        console=True,
    )
    print(f"\nDelivered {delivered} of {len(alerts)}")
    return 0


def cmd_dossier(cfg: dict, ticker: str) -> int:
    from tasi_agent.monitor import dossier
    conn = init_db(db_path(cfg))
    rows = dossier(conn, ticker)
    if not rows:
        print(f"No events recorded for {ticker}. Either no material changes, "
              f"or the stock has never been on the watchlist.")
        return 0
    print(f"=== {ticker} event history ({len(rows)} events) ===\n")
    for r in rows:
        print(f"[{r['severity'].upper():<6}] {r['occurred_at'][:10]}  {r['title']}")
        if r["detail"]:
            for line in r["detail"].splitlines():
                print(f"         {line}")
        print()
    return 0


def cmd_indicators(cfg: dict, ticker: str | None, watchlist: bool) -> int:
    import pandas as pd
    from tasi_agent.indicators import compute_all

    conn = init_db(db_path(cfg))

    if watchlist:
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM watchlist ORDER BY ticker"
        ).fetchall()]
        if not tickers:
            print("Watchlist is empty. Add stocks with: run.py watch add TICKER")
            return 0
    elif ticker:
        tickers = [str(ticker).strip().zfill(4)]
    else:
        print("Pass either a TICKER or --watchlist")
        return 1

    for tk in tickers:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? AND open IS NOT NULL AND volume IS NOT NULL "
            "ORDER BY date ASC",
            (tk,),
        ).fetchall()
        if len(rows) < 30:
            print(f"\n{tk}: only {len(rows)} OHLCV rows - re-run: "
                  f"py -3.12 run.py collect --source prices")
            continue

        df = pd.DataFrame([dict(r) for r in rows]).set_index("date")
        ind = compute_all(df)
        last = ind.iloc[-1]

        name = conn.execute(
            "SELECT company_name FROM recommendations WHERE ticker=? "
            "AND company_name IS NOT NULL ORDER BY publication_date DESC LIMIT 1",
            (tk,),
        ).fetchone()
        company = name[0] if name else ""

        print(f"\n=== {tk}  {company}  ({ind.index[-1]}) ===")
        print(f"  Close:         SAR {last['close']:.2f}")
        print(f"  EMA 20 / 50:   {last['ema_20']:.2f} / {last['ema_50']:.2f}")
        if pd.isna(last['sma_200']):
            print(f"  SMA 50 / 200:  {last['sma_50']:.2f} / (insufficient data)")
        else:
            print(f"  SMA 50 / 200:  {last['sma_50']:.2f} / {last['sma_200']:.2f}")
        print(f"  RSI(14):       {last['rsi_14']:.1f}")
        print(f"  MACD:          {last['macd']:+.3f} "
              f"(signal {last['macd_signal']:+.3f}, "
              f"hist {last['macd_hist']:+.3f})")
        print(f"  ADX(14):       {last['adx']:.1f}  "
              f"(+DI {last['plus_di']:.1f} / -DI {last['minus_di']:.1f})")
        print(f"  ATR(14):       {last['atr_14']:.2f} SAR")
        print(f"  Bollinger:     {last['bb_lower']:.2f} / {last['bb_mid']:.2f} / "
              f"{last['bb_upper']:.2f}  (width {last['bb_width']:.1f}%)")
        print(f"  Volume ratio:  {last['volume_ratio_20']:.2f}x  20-day avg")
        print(f"  ROC(10):       {last['roc_10']:+.2f}%")

    return 0


def cmd_setups(cfg: dict, ticker: str | None,
               lookback: int, days: int) -> int:
    import pandas as pd
    from tasi_agent.indicators import compute_all
    from tasi_agent.technical_setups import (detect_volume_breakout,
                                             setup_details)

    conn = init_db(db_path(cfg))

    if ticker:
        tickers = [str(ticker).strip().zfill(4)]
    else:
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT ticker FROM watchlist ORDER BY ticker"
        ).fetchall()]
        if not tickers:
            print("Watchlist is empty. Add stocks or pass a TICKER.")
            return 0

    found_any = False
    for tk in tickers:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? AND open IS NOT NULL AND volume IS NOT NULL "
            "ORDER BY date ASC",
            (tk,),
        ).fetchall()
        if len(rows) < 40:
            continue

        df = pd.DataFrame([dict(r) for r in rows]).set_index("date")
        ind = compute_all(df)
        signals = detect_volume_breakout(ind, lookback=lookback, volume_mult=1.5)

        recent = signals.tail(days)
        firings = recent[recent].index.tolist()
        if not firings:
            continue

        found_any = True
        name = conn.execute(
            "SELECT company_name FROM recommendations WHERE ticker=? "
            "AND company_name IS NOT NULL ORDER BY publication_date DESC LIMIT 1",
            (tk,),
        ).fetchone()
        company = name[0] if name else ""

        print(f"\n=== {tk}  {company} ===")
        for date_str in firings:
            i = ind.index.get_loc(date_str)
            d = setup_details(ind, i, setup_name="volume_breakout",
                              lookback=lookback)
            print(f"  {date_str}: close SAR {d['entry']:.2f} broke above "
                  f"SAR {d['recent_low']:.2f} zone")
            print(f"           entry {d['entry']:.2f}  "
                  f"stop {d['stop']:.2f}  "
                  f"target1 {d['target_1']:.2f}  target2 {d['target_2']:.2f}  "
                  f"(R:R 1:2 / 1:3, ATR {d['atr']:.2f})")

    if not found_any:
        print(f"No volume-confirmed breakouts in the last {days} sessions.")
    return 0


def cmd_regime(cfg: dict) -> int:
    from tasi_agent.market_regime import classify_regime

    conn = init_db(db_path(cfg))
    result = classify_regime(conn)
    m = result["metrics"]

    print(f"\n=== TASI Market Regime ===")
    print(f"  Regime     : {result['regime']}")
    print(f"  Confidence : {result['confidence']:.2f}")
    print(f"\n  Metrics:")
    if m.get("insufficient_index_data"):
        print(f"    ! insufficient index data ({m.get('index_rows', 0)} rows)")
        print(f"    ! run: py -3.12 run.py collect --source prices")
    else:
        print(f"    close       : {m['close']}")
        print(f"    MA20 / MA50 : {m['ma_short']} / {m['ma_long']}")
        print(f"    breadth@20  : {m['breadth_ma20']*100:.1f}% of stocks above")
        print(f"    breadth@50  : {m['breadth_ma50']*100:.1f}% of stocks above")
        print(f"    vol ratio   : {m['vol_ratio']}x long-run")

    allowed = result["regime"] in ("STRONG_BULLISH", "BULLISH")
    print(f"\n  New long positions: {'ALLOWED' if allowed else 'NOT ALLOWED'}")
    return 0


def cmd_scan(cfg: dict, min_score: float, top: int,
             capital: float, risk_pct: float) -> int:
    import pandas as pd
    from tasi_agent.indicators import compute_all
    from tasi_agent.market_regime import classify_regime
    from tasi_agent.opportunity_scoring import (compute_opportunity_score,
                                                eligibility_gates)
    from tasi_agent.position_sizing import calculate_position_size
    from tasi_agent.technical_setups import SETUPS, setup_details
    from tasi_agent.trade_plans import classify_action

    conn = init_db(db_path(cfg))
    regime_info = classify_regime(conn)
    regime = regime_info["regime"]

    idx_rows = conn.execute(
        "SELECT date, close FROM index_prices ORDER BY date ASC"
    ).fetchall()
    tasi_returns = None
    if len(idx_rows) > 30:
        idx_df = pd.DataFrame([dict(r) for r in idx_rows]).set_index("date")
        tasi_returns = idx_df["close"].pct_change(20)

    today = dt.date.today().isoformat()
    consensus = {s.ticker: s.status
                 for s in build_consensus(conn, cfg, today)}

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM recommendations ORDER BY ticker"
    ).fetchall()]

    print(f"\n=== TASI Trading Scan ===")
    print(f"  Market regime : {regime} (confidence {regime_info['confidence']:.2f})")
    print(f"  Stocks scanned: {len(tickers)}")
    print(f"  Capital       : SAR {capital:,.0f}")
    print(f"  Risk per trade: {risk_pct*100:.1f}%")

    candidates = []

    for ticker in tickers:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? AND open IS NOT NULL AND volume IS NOT NULL "
            "ORDER BY date ASC",
            (ticker,),
        ).fetchall()
        if len(rows) < 60:
            continue

        df = pd.DataFrame([dict(r) for r in rows]).set_index("date")
        ind = compute_all(df)

        latest_close = float(ind["close"].iloc[-1])

        eligible, _reasons = eligibility_gates(
            ind, ticker, latest_market_price=latest_close
        )
        if not eligible:
            continue

        for setup_name, detect_fn in SETUPS:
            try:
                signals = detect_fn(ind)
            except Exception:
                continue
            if not signals.iloc[-1]:
                continue

            i = len(ind) - 1
            plan = setup_details(ind, i, setup_name)

            score = compute_opportunity_score(
                ind, setup_name, plan, consensus.get(ticker), tasi_returns
            )

            action = classify_action(
                setup_fired_today=True,
                setup_name=setup_name,
                market_regime=regime,
                opportunity_score=score["total"],
                eligible=True,
            )

            sizing = calculate_position_size(
                capital=capital,
                risk_pct=risk_pct,
                entry=plan["entry"],
                stop=plan["stop"],
            )

            company_row = conn.execute(
                "SELECT company_name FROM recommendations WHERE ticker=? "
                "AND company_name IS NOT NULL ORDER BY publication_date DESC LIMIT 1",
                (ticker,),
            ).fetchone()

            candidates.append({
                "ticker": ticker,
                "company": company_row[0] if company_row else "",
                "setup": setup_name,
                "score": score["total"],
                "action": action["action"],
                "action_reason": action["reason"],
                "plan": plan,
                "sizing": sizing,
                "score_detail": score["components"],
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = [c for c in candidates if c["score"] >= min_score][:top]

    if regime in ("BEARISH", "HIGH_VOL_BEARISH"):
        print(f"\n  [NO TRADE] Market regime {regime} does not permit new longs.\n")

    if not candidates:
        print(f"\n=== NO TRADE - no stocks meet score >= {min_score} today ===\n")
        return 0

    print(f"\n{'Rank':<5}{'Ticker':<7}{'Company':<24}{'Setup':<20}"
          f"{'Score':>6}  {'Action':<16}{'Entry':>8}{'Stop':>8}"
          f"{'T1':>8}{'T2':>8}{'Shares':>8}")
    print("-" * 130)
    for rank, c in enumerate(candidates, 1):
        p = c["plan"]
        s = c["sizing"]
        print(f"{rank:<5}{c['ticker']:<7}{(c['company'] or '')[:22]:<24}"
              f"{c['setup']:<20}{c['score']:>6.1f}  {c['action']:<16}"
              f"{p['entry']:>8.2f}{p['stop']:>8.2f}"
              f"{p['target_1']:>8.2f}{p['target_2']:>8.2f}"
              f"{s['shares']:>8d}")

    print("\n=== Top setups ===")
    for c in candidates[:3]:
        p, s = c["plan"], c["sizing"]
        print(f"\n{c['ticker']}  {c['company']}  [{c['action']}]")
        print(f"  Setup    : {c['setup']}  (score {c['score']:.1f}/100)")
        print(f"  Reason   : {c['action_reason']}")
        print(f"  Entry    : {p['entry']:.2f}  "
              f"Stop {p['stop']:.2f}  "
              f"T1 {p['target_1']:.2f}  T2 {p['target_2']:.2f}  "
              f"ATR {p['atr']:.2f}")
        if s["valid"]:
            print(f"  Size     : {s['shares']} shares "
                  f"= SAR {s['position_value']:,.0f}  "
                  f"max loss SAR {s['max_loss']:,.0f} "
                  f"({s['actual_risk_pct']*100:.2f}%)")
        else:
            print(f"  Size     : CANNOT SIZE - {s['reason']}")
        print(f"  Score    : trend {c['score_detail']['trend']['score']:.0f}/20 . "
              f"mom {c['score_detail']['momentum']['score']:.0f}/15 . "
              f"vol {c['score_detail']['volume']['score']:.0f}/20 . "
              f"setup {c['score_detail']['setup']['score']:.0f}/15 . "
              f"RS {c['score_detail']['relative_strength']['score']:.0f}/10 . "
              f"R:R {c['score_detail']['risk_reward']['score']:.0f}/10 . "
              f"analyst {c['score_detail']['analyst']['score']:.0f}/5 . "
              f"quality {c['score_detail']['data_quality']['score']:.0f}/5")

    return 0


def cmd_daily(cfg: dict, capital: float, risk_pct: float,
              min_score: float, top: int) -> int:
    """Produce the daily trading brief with cross-sectional ranking."""
    import pandas as pd
    from tasi_agent.daily_brief import (build_brief, save_brief,
                                        scan_analyst_stocks_for_technical_status)
    from tasi_agent.indicators import compute_all, momentum_panel
    from tasi_agent.market_regime import classify_regime
    from tasi_agent.opportunity_scoring import (compute_opportunity_score,
                                                eligibility_gates)
    from tasi_agent.position_sizing import calculate_position_size
    from tasi_agent.technical_setups import SETUPS, setup_details
    from tasi_agent.trade_plans import classify_action

    try:
        from tasi_agent.cross_section import rank_universe
    except ImportError:
        rank_universe = None
        print("  [note] cross_section.py not found - ranks will be blank")

    conn = init_db(db_path(cfg))
    regime_info = classify_regime(conn)
    regime = regime_info["regime"]

    idx_rows = conn.execute(
        "SELECT date, close FROM index_prices ORDER BY date ASC"
    ).fetchall()
    tasi_returns = None
    if len(idx_rows) > 30:
        idx_df = pd.DataFrame([dict(r) for r in idx_rows]).set_index("date")
        tasi_returns = idx_df["close"].pct_change(20)

    today = dt.date.today().isoformat()
    consensus_objects = build_consensus(conn, cfg, today)
    consensus = {s.ticker: s.status for s in consensus_objects}

    analyst_stocks = []
    for s in consensus_objects:
        if s.status in ("STRONG_CONSENSUS", "CONFIRMED",
                        "CONFIRMED_WITH_DISSENT"):
            analyst_stocks.append({
                "ticker": s.ticker,
                "company": s.company_name or "",
                "status": s.status,
                "firms_positive": len(s.positive),
            })

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM recommendations ORDER BY ticker"
    ).fetchall()]

    # ---- Pass 1: scan every stock, collect raw measurements --------
    all_measurements = []
    setup_firing_rows = []

    for ticker in tickers:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker=? AND open IS NOT NULL AND volume IS NOT NULL "
            "ORDER BY date ASC",
            (ticker,),
        ).fetchall()
        if len(rows) < 60:
            continue

        df = pd.DataFrame([dict(r) for r in rows]).set_index("date")
        ind = compute_all(df)
        last = ind.iloc[-1]
        latest_close = float(last["close"])

        eligible, _ = eligibility_gates(
            ind, ticker, latest_market_price=latest_close
        )
        if not eligible:
            continue

        # Cross-sectional inputs
        close_series = ind["close"]
        panel = momentum_panel(close_series)

        rs_20d = None
        if tasi_returns is not None and len(tasi_returns) >= 20:
            stock_roc = close_series.pct_change(20).iloc[-1]
            tasi_roc = tasi_returns.iloc[-1]
            if pd.notna(stock_roc) and pd.notna(tasi_roc):
                rs_20d = float((stock_roc - tasi_roc) * 100)

        bbw_val = last.get("bbw_pctile") if hasattr(last, "get") else None
        vol_val = last.get("volume_ratio_20") if hasattr(last, "get") else None

        all_measurements.append({
            "ticker": ticker,
            "mom_6M_lagged": panel.get("6M"),
            "mom_20D": panel.get("20D"),
            "mom_5D": panel.get("5D"),
            "relative_strength_20D": rs_20d,
            "bbw_pctile": float(bbw_val) if pd.notna(bbw_val) else None,
            "volume_ratio": float(vol_val) if pd.notna(vol_val) else None,
        })

        # Detect setups
        for setup_name, detect_fn in SETUPS:
            try:
                signals = detect_fn(ind)
            except Exception:
                continue
            if not signals.iloc[-1]:
                continue

            i = len(ind) - 1
            plan = setup_details(ind, i, setup_name)

            score = compute_opportunity_score(
                ind, setup_name, plan, consensus.get(ticker), tasi_returns
            )

            action = classify_action(
                setup_fired_today=True,
                setup_name=setup_name,
                market_regime=regime,
                opportunity_score=score["total"],
                eligible=True,
            )

            sizing = calculate_position_size(
                capital=capital,
                risk_pct=risk_pct,
                entry=plan["entry"],
                stop=plan["stop"],
            )

            company_row = conn.execute(
                "SELECT company_name FROM recommendations WHERE ticker=? "
                "AND company_name IS NOT NULL ORDER BY publication_date DESC LIMIT 1",
                (ticker,),
            ).fetchone()

            setup_firing_rows.append({
                "ticker": ticker,
                "company": company_row[0] if company_row else "",
                "setup": setup_name,
                "score": score["total"],
                "action": action["action"],
                "action_reason": action["reason"],
                "plan": plan,
                "sizing": sizing,
                "score_detail": score["components"],
                "current_close": latest_close,
            })

    # ---- Pass 2: cross-sectional ranks over the whole universe ----
    rank_lookup = {}
    if all_measurements and rank_universe is not None:
        try:
            universe_df = rank_universe(all_measurements)
            if not universe_df.empty:
                for _, rk_row in universe_df.iterrows():
                    tk = str(rk_row.get("ticker", ""))
                    rank_lookup[tk] = {
                        "rank_6M": _safe_float(rk_row.get("rank_6M")),
                        "rank_20D": _safe_float(rk_row.get("rank_20D")),
                        "rank_5D": _safe_float(rk_row.get("rank_5D")),
                        "rank_rs": _safe_float(rk_row.get("rank_rs")),
                        "rank_squeeze": _safe_float(rk_row.get("rank_squeeze")),
                        "rank_volume": _safe_float(rk_row.get("rank_volume")),
                        "rank_composite": _safe_float(rk_row.get("rank_composite")),
                    }
        except Exception as exc:
            print(f"  [warn] cross-section ranking failed: {exc}")

    # ---- Pass 3: attach ranks, then filter and sort --------
    results = []
    for row in setup_firing_rows:
        rk = rank_lookup.get(row["ticker"], {})
        row["rank_composite"] = rk.get("rank_composite")
        row["rank_6M"] = rk.get("rank_6M")
        row["rank_rs"] = rk.get("rank_rs")
        row["rank_squeeze"] = rk.get("rank_squeeze")
        results.append(row)

    # Sort by composite rank if available, else by score
    results.sort(
        key=lambda c: (
            c.get("rank_composite") if c.get("rank_composite") is not None else 0,
            c["score"],
        ),
        reverse=True,
    )
    results = [r for r in results if r["score"] >= min_score][:top]

    enriched_analyst = scan_analyst_stocks_for_technical_status(
        conn, analyst_stocks, SETUPS
    )

    brief = build_brief(results, regime_info, capital, risk_pct,
                        analyst_stocks=enriched_analyst)

    print(f"\n{'=' * 70}")
    print(f"  TASI DAILY TRADING BRIEF - {brief['as_of']}")
    print(f"{'=' * 70}")
    print(f"\n  VERDICT: {brief['verdict']}")
    print(f"  {brief['verdict_text']}")
    print(f"\n  Regime     : {regime} (confidence {brief['confidence']:.0%})")
    m = brief["metrics"]
    if not m.get("insufficient_index_data"):
        print(f"  TASI close : {m.get('close')}  "
              f"(MA20 {m.get('ma_short')} / MA50 {m.get('ma_long')})")
        print(f"  Breadth    : {m.get('breadth_ma20', 0)*100:.0f}% above MA20, "
              f"{m.get('breadth_ma50', 0)*100:.0f}% above MA50")
        print(f"  Volatility : {m.get('vol_ratio', 1.0):.2f}x long-run")

    print(f"\n  Universe   : {len(all_measurements)} stocks analysed "
          f"({len(rank_lookup)} ranked cross-sectionally)")
    print(f"  Buy now    : {len(brief['buy_now'])} setups")
    print(f"  Watch list : {len(brief['watch'])} setups (wait for trigger)")
    print(f"  Analyst-confirmed (context): {len(brief['analyst_stocks'])} stocks")
    if brief["total_possible_risk"] > 0:
        print(f"  If all triggers fire: {brief['total_possible_risk']:,.0f} SAR risk "
              f"({brief['total_possible_risk']/capital*100:.1f}% of capital)")

    out_dir = Path(cfg["_root"]) / cfg["project"]["output_dir"]
    paths = save_brief(brief, out_dir)

    print(f"\n  Files saved:")
    for fmt, p in paths.items():
        print(f"    {fmt.upper():<4} {p}")

    import os
    if os.environ.get("TASI_NO_OPEN", "").strip() not in ("1", "true", "yes"):
        try:
            os.startfile(str(paths["html"]))
            print(f"  Opened: {paths['html'].name}")
        except Exception:
            pass

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="TASI Analyst Consensus Intelligence Agent")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("demo")

    p_collect = sub.add_parser("collect")
    p_collect.add_argument("--source", choices=COLLECTOR_CHOICES)
    p_collect.add_argument("--debug", action="store_true")

    p_report = sub.add_parser("report")
    p_report.add_argument("--date")

    sub.add_parser("perf")
    sub.add_parser("dashboard")
    sub.add_parser("doctor")
    sub.add_parser("run-all")

    p_watch = sub.add_parser("watch")
    watch_sub = p_watch.add_subparsers(dest="watch_cmd", required=True)
    p_wadd = watch_sub.add_parser("add")
    p_wadd.add_argument("ticker")
    p_wadd.add_argument("--notes", default="")
    p_wadd.add_argument("--level", choices=["low", "medium", "high"],
                        default="medium")
    p_wrem = watch_sub.add_parser("remove")
    p_wrem.add_argument("ticker")
    watch_sub.add_parser("list")

    p_monitor = sub.add_parser("monitor")
    p_monitor.add_argument("--dry-run", action="store_true")

    p_dossier = sub.add_parser("dossier")
    p_dossier.add_argument("ticker")

    p_ind = sub.add_parser("indicators")
    p_ind.add_argument("ticker", nargs="?")
    p_ind.add_argument("--watchlist", action="store_true")

    p_sets = sub.add_parser("setups")
    p_sets.add_argument("ticker", nargs="?")
    p_sets.add_argument("--lookback", type=int, default=20)
    p_sets.add_argument("--days", type=int, default=10)

    sub.add_parser("regime")

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--min-score", type=float, default=50.0)
    p_scan.add_argument("--top", type=int, default=10)
    p_scan.add_argument("--capital", type=float, default=100_000)
    p_scan.add_argument("--risk-pct", type=float, default=0.01)

    p_daily = sub.add_parser("daily")
    p_daily.add_argument("--capital", type=float, default=100_000)
    p_daily.add_argument("--risk-pct", type=float, default=0.01)
    p_daily.add_argument("--min-score", type=float, default=50.0)
    p_daily.add_argument("--top", type=int, default=10)

    args = parser.parse_args(argv)
    cfg = load(args.config)

    if args.command == "init":
        return cmd_init(cfg)
    if args.command == "demo":
        return cmd_demo(cfg)
    if args.command == "collect":
        return cmd_collect(cfg, args.source, debug=args.debug)
    if args.command == "report":
        return cmd_report(cfg, args.date)
    if args.command == "perf":
        return cmd_perf(cfg)
    if args.command == "dashboard":
        return cmd_dashboard(cfg)
    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "run-all":
        return cmd_run_all(cfg)

    if args.command == "watch":
        if args.watch_cmd == "add":
            return cmd_watch_add(cfg, args.ticker, args.notes, args.level)
        if args.watch_cmd == "remove":
            return cmd_watch_remove(cfg, args.ticker)
        if args.watch_cmd == "list":
            return cmd_watch_list(cfg)

    if args.command == "monitor":
        return cmd_monitor(cfg, dry_run=args.dry_run)

    if args.command == "dossier":
        return cmd_dossier(cfg, args.ticker)

    if args.command == "indicators":
        return cmd_indicators(cfg, args.ticker, args.watchlist)

    if args.command == "setups":
        return cmd_setups(cfg, args.ticker, args.lookback, args.days)

    if args.command == "regime":
        return cmd_regime(cfg)

    if args.command == "scan":
        return cmd_scan(cfg, args.min_score, args.top,
                        args.capital, args.risk_pct)

    if args.command == "daily":
        return cmd_daily(cfg, args.capital, args.risk_pct,
                         args.min_score, args.top)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())