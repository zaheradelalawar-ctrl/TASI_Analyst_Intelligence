"""
Synthetic demo data.

Every record is tagged source_type='synthetic_demo' and the report displays a
red DEMO banner while any synthetic record exists. This exists purely so the
pipeline can be exercised end-to-end without touching a live provider.
"""
from __future__ import annotations

import datetime as dt

from ..db import connect  # noqa: F401  (typing convenience)
from ..models import RawItem


def _d(days_ago: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


DEMO_ITEMS = [
    # STRONG CONSENSUS: 3 firms, no negatives
    RawItem("AlJazira Capital", "synthetic_demo", "2222", "Saudi Aramco", "Buy", 32.0, 27.5, _d(12), "demo://argaam/1", confidence=0.9),
    RawItem("SNB Capital", "synthetic_demo", "2222", "Saudi Aramco", "Overweight", 31.0, 27.5, _d(20), "demo://snb/1", confidence=0.9),
    RawItem("Riyad Capital", "synthetic_demo", "2222", "Saudi Aramco", "Add", 30.5, 27.9, _d(31), "demo://riyad/1", confidence=0.88),

    # CONFIRMED: exactly 2 firms
    RawItem("Al Rajhi Capital", "synthetic_demo", "7010", "stc", "Buy", 48.0, 41.2, _d(9), "demo://alrajhi/1", confidence=0.92),
    RawItem("Arqaam Capital", "synthetic_demo", "7010", "stc", "Outperform", 46.0, 41.0, _d(25), "demo://arqaam/1", confidence=0.8),

    # CONFLICTING: positive + negative coexist
    RawItem("EFG Hermes", "synthetic_demo", "7203", "Elm", "Buy", 340.0, 300.0, _d(15), "demo://efg/1", confidence=0.86),
    RawItem("AlphaMena", "synthetic_demo", "7203", "Elm", "Reduce", 260.0, 300.0, _d(22), "demo://alphamena/1", confidence=0.82),

    # SINGLE OPINION
    RawItem("GIB Capital", "synthetic_demo", "4013", "Dr Sulaiman Al Habib", "Accumulate", 260.0, 232.0, _d(6), "demo://gib/1", confidence=0.9),

    # STALE: outside the 90-day window, must not count
    RawItem("HSBC", "synthetic_demo", "1120", "Al Rajhi Bank", "Buy", 95.0, 80.0, _d(140), "demo://hsbc/1", confidence=0.9),
    RawItem("Goldman Sachs", "synthetic_demo", "1120", "Al Rajhi Bank", "Buy", 92.0, 81.0, _d(150), "demo://gs/1", confidence=0.9),

    # DUPLICATE test: same firm/ticker/rating on a second source, 2 days apart
    RawItem("AlJazira Capital", "argaam", "2222", "Saudi Aramco", "Buy", 32.0, 27.5, _d(10), "demo://argaam/dup", confidence=0.7),
]

DEMO_PRICES = {
    "2222": [(40, 27.2), (30, 27.6), (20, 27.5), (10, 28.4), (1, 29.1)],
    "7010": [(40, 39.8), (30, 40.6), (20, 41.0), (10, 41.9), (1, 43.2)],
    "7203": [(40, 288.0), (30, 295.0), (20, 300.0), (10, 297.0), (1, 291.0)],
    "4013": [(40, 225.0), (30, 228.0), (20, 231.0), (10, 234.0), (1, 236.5)],
    "1120": [(40, 78.0), (30, 80.0), (20, 82.0), (10, 85.0), (1, 87.0)],
}


def seed_prices(conn) -> None:
    for ticker, series in DEMO_PRICES.items():
        for days_ago, close in series:
            conn.execute(
                "INSERT OR REPLACE INTO prices (ticker, date, close) VALUES (?,?,?)",
                (ticker, _d(days_ago), close),
            )
    # TASI index proxy series
    for i, days_ago in enumerate(range(60, 0, -3)):
        conn.execute("INSERT OR REPLACE INTO index_prices (date, close) VALUES (?,?)",
                     (_d(days_ago), 11800 + i * 4.5))
    conn.execute("INSERT OR REPLACE INTO index_prices (date, close) VALUES (?,?)", (_d(0), 12100.0))
    conn.commit()


class DemoCollector:
    name = "demo"
    source_type = "synthetic_demo"

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self):
        return list(DEMO_ITEMS)