"""SQLite schema and low-level data access."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS providers (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    name_ar       TEXT,
    kind          TEXT,
    country       TEXT,
    homepage      TEXT,
    status        TEXT NOT NULL DEFAULT 'candidate',
    discovered_at TEXT NOT NULL,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    id                    INTEGER PRIMARY KEY,
    provider_id           INTEGER NOT NULL REFERENCES providers(id),
    ticker                TEXT NOT NULL,
    company_name          TEXT,
    rating_raw            TEXT,
    rating_norm           TEXT,
    rating_assumed        INTEGER DEFAULT 0,
    target_price          REAL,
    currency              TEXT DEFAULT 'SAR',
    price_at_publication  REAL,
    publication_date      TEXT NOT NULL,
    source_url            TEXT,
    source_type           TEXT NOT NULL,
    document_path         TEXT,
    raw_text              TEXT,
    extraction_confidence REAL DEFAULT 0.0,
    validation_status     TEXT NOT NULL DEFAULT 'unverified',
    is_withdrawal         INTEGER DEFAULT 0,
    fingerprint           TEXT NOT NULL UNIQUE,
    collected_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rec_provider_ticker
    ON recommendations(provider_id, ticker, publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_rec_ticker ON recommendations(ticker);
CREATE INDEX IF NOT EXISTS idx_rec_date   ON recommendations(publication_date);
CREATE INDEX IF NOT EXISTS idx_rec_collected ON recommendations(collected_at);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    close  REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS index_prices (
    date  TEXT PRIMARY KEY,
    close REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS splits (
    ticker     TEXT NOT NULL,
    ex_date    TEXT NOT NULL,
    ratio      REAL NOT NULL,
    source     TEXT DEFAULT 'yahoo',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, ex_date)
);

CREATE TABLE IF NOT EXISTS review_queue (
    id                INTEGER PRIMARY KEY,
    recommendation_id INTEGER REFERENCES recommendations(id),
    reason            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    resolved          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id      INTEGER PRIMARY KEY,
    run_at  TEXT NOT NULL,
    stage   TEXT,
    level   TEXT,
    message TEXT
);

-- ===========================================================================
-- Monitor tables (added for watchlist + alerting)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS watchlist (
    ticker       TEXT PRIMARY KEY,
    company_name TEXT,
    added_at     TEXT NOT NULL,
    notes        TEXT,
    alert_level  TEXT DEFAULT 'medium'
);

CREATE TABLE IF NOT EXISTS stock_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT,
    payload_json TEXT,
    source_url   TEXT,
    occurred_at  TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    notified     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ticker
    ON stock_events(ticker, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_notified
    ON stock_events(notified) WHERE notified=0;

CREATE TABLE IF NOT EXISTS monitor_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT NOT NULL,
    tickers_checked INTEGER,
    events_created  INTEGER,
    alerts_high     INTEGER,
    alerts_medium   INTEGER,
    alerts_low      INTEGER,
    errors          TEXT
);
"""


def connect(db_file: str | Path) -> sqlite3.Connection:
    p = Path(db_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_prices_table(conn: sqlite3.Connection) -> None:
    """Add OHLCV columns to prices table if they don't exist yet.
    Existing rows get NULL for the new columns; re-collection fills them."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(prices)")}
    new_cols = [
        ("open",     "REAL"),
        ("high",     "REAL"),
        ("low",      "REAL"),
        ("volume",   "INTEGER"),
        ("adj_close", "REAL"),
    ]
    added = 0
    for name, typ in new_cols:
        if name not in existing:
            conn.execute(f"ALTER TABLE prices ADD COLUMN {name} {typ}")
            added += 1
    if added:
        print(f"  migrated prices table: added {added} column(s)")
    conn.commit()


def init_db(db_file: str | Path) -> sqlite3.Connection:
    conn = connect(db_file)
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_prices_table(conn)
    return conn

def log(conn: sqlite3.Connection, stage: str, message: str, level: str = "info") -> None:
    from .utils import now_iso
    conn.execute(
        "INSERT INTO run_log (run_at, stage, level, message) VALUES (?,?,?,?)",
        (now_iso(), stage, level, message),
    )
    conn.commit()


def latest_price(conn: sqlite3.Connection, ticker: str,
                 on_or_before: str | None = None) -> float | None:
    if on_or_before:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
            (ticker, on_or_before),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
    return float(row["close"]) if row else None


def price_on_or_after(conn: sqlite3.Connection, ticker: str,
                      date_iso: str) -> tuple[str, float] | None:
    row = conn.execute(
        "SELECT date, close FROM prices WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1",
        (ticker, date_iso),
    ).fetchone()
    return (row["date"], float(row["close"])) if row else None


def trading_calendar(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT date FROM index_prices ORDER BY date ASC").fetchall()
    if not rows:
        rows = conn.execute("SELECT DISTINCT date FROM prices ORDER BY date ASC").fetchall()
    return [r["date"] for r in rows]