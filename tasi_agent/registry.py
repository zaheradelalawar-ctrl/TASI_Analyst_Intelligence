"""
Research Provider Registry.

Firms are the unit of independence. Two analysts at the same house count once.
Names are canonicalised through an alias map so the same firm scraped from
Argaam, its own website and a PDF is always the same provider row.
"""
from __future__ import annotations

import re

from .utils import now_iso

DEFAULT_PROVIDERS = [
    # (name, kind, country, homepage)
    ("AlJazira Capital", "saudi", "SA", "https://aljaziracapital.com.sa"),
    ("Al Rajhi Capital", "saudi", "SA", "https://www.alrajhi-capital.com"),
    ("SNB Capital", "saudi", "SA", "https://www.snbcapital.com"),
    ("Riyad Capital", "saudi", "SA", "https://www.riyadcapital.com"),
    ("ANB Capital", "saudi", "SA", "https://www.anbcapital.com.sa"),
    ("BSF Capital", "saudi", "SA", "https://www.bsfcapital.com.sa"),
    ("GIB Capital", "saudi", "SA", "https://www.gibcapital.com"),
    ("Alawwal Capital", "saudi", "SA", "https://www.alawwalcapital.com"),
    ("Derayah Financial", "saudi", "SA", "https://www.derayah.com"),
    ("Yaqeen Capital", "saudi", "SA", "https://www.yaqeen.com.sa"),
    ("AlphaMena", "regional", "AE", "https://www.alphamena.net"),
    ("Arqaam Capital", "regional", "AE", "https://www.arqaamcapital.com"),
    ("EFG Hermes", "regional", "EG", "https://www.efghermes.com"),
    ("U-Capital", "regional", "KW", "https://www.u-capital.net"),
    ("CI Capital", "regional", "EG", "https://www.cicapital.com"),
    ("Goldman Sachs", "international", "US", "https://www.goldmansachs.com"),
    ("HSBC", "international", "UK", "https://www.hsbc.com"),
    ("J.P. Morgan", "international", "US", "https://www.jpmorgan.com"),
    ("Morgan Stanley", "international", "US", "https://www.morganstanley.com"),
    ("Bank of America", "international", "US", "https://www.bofa.com"),
]

ALIASES = {
    "aljazira capital": "AlJazira Capital",
    "al jazira capital": "AlJazira Capital",
    "aljazira": "AlJazira Capital",
    "aljazira capital co": "AlJazira Capital",
    "al rajhi capital": "Al Rajhi Capital",
    "alrajhi capital": "Al Rajhi Capital",
    "al rajhi": "Al Rajhi Capital",
    "snb capital": "SNB Capital",
    "saudi national bank capital": "SNB Capital",
    "ncbc capital": "SNB Capital",
    "riyad capital": "Riyad Capital",
    "riyadh capital": "Riyad Capital",
    "anb capital": "ANB Capital",
    "arab national bank capital": "ANB Capital",
    "bsf capital": "BSF Capital",
    "banque saudi fransi capital": "BSF Capital",
    "gib capital": "GIB Capital",
    "gulf international bank capital": "GIB Capital",
    "alawwal capital": "Alawwal Capital",
    "al awal capital": "Alawwal Capital",
    "derayah financial": "Derayah Financial",
    "derayah": "Derayah Financial",
    "yaqeen capital": "Yaqeen Capital",
    "alphamena": "AlphaMena",
    "alpha mena": "AlphaMena",
    "arqaam capital": "Arqaam Capital",
    "arqaam": "Arqaam Capital",
    "efg hermes": "EFG Hermes",
    "efg-hermes": "EFG Hermes",
    "u capital": "U-Capital",
    "u-capital": "U-Capital",
    "ucapital": "U-Capital",
    "ci capital": "CI Capital",
    "goldman sachs": "Goldman Sachs",
    "hsbc": "HSBC",
    "hsbc saudi arabia": "HSBC",
    "j p morgan": "J.P. Morgan",
    "jp morgan": "J.P. Morgan",
    "jpmorgan": "J.P. Morgan",
    "morgan stanley": "Morgan Stanley",
    "bank of america": "Bank of America",
    "bofa": "Bank of America",
    "bofa merrill lynch": "Bank of America",
}


def canonical_name(name: str) -> str:
    if not name:
        return "Unknown Provider"
    key = re.sub(r"[^\w\s\-\.]", " ", name).strip().lower()
    key = re.sub(r"[\-_]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    if key in ALIASES:
        return ALIASES[key]
    # Try without trailing legal suffixes.
    stripped = re.sub(r"\b(capital|financial|securities|investment|company|co)\b", "", key)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped in ALIASES:
        return ALIASES[stripped]
    return re.sub(r"\s+", " ", name).strip()


def seed_providers(conn) -> int:
    added = 0
    for name, kind, country, homepage in DEFAULT_PROVIDERS:
        cur = conn.execute(
            "SELECT id FROM providers WHERE name=?", (name,)
        ).fetchone()
        if cur:
            continue
        conn.execute(
            "INSERT INTO providers (name, kind, country, homepage, status, discovered_at) "
            "VALUES (?,?,?,?,?,?)",
            (name, kind, country, homepage, "candidate", now_iso()),
        )
        added += 1
    conn.commit()
    return added


def get_or_create_provider(conn, name: str, *, status: str | None = None) -> int:
    """Return provider id, creating a discovered provider row when needed."""
    canon = canonical_name(name)
    row = conn.execute("SELECT id FROM providers WHERE name=?", (canon,)).fetchone()
    if row:
        if status:
            conn.execute("UPDATE providers SET status=? WHERE id=?", (status, row["id"]))
            conn.commit()
        return int(row["id"])

    row = conn.execute("SELECT id FROM providers WHERE lower(name)=?", (canon.lower(),)).fetchone()
    if row:
        return int(row["id"])

    cur = conn.execute(
        "INSERT INTO providers (name, kind, status, discovered_at, notes) VALUES (?,?,?,?,?)",
        (canon, "discovered", status or "candidate", now_iso(), "auto-discovered during collection"),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_providers(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM providers ORDER BY kind, name").fetchall()
    return [dict(r) for r in rows]