"""
Argaam researchers-opinions collector - Playwright edition (fixed).

Critical rule: the ticker is NEVER extracted from cell text. Dates in
particular contain 4-digit years that look like tickers. Instead:
  1. Look for a company link in the row and pull the ticker from its href.
  2. If no link, leave ticker=None and let ingest resolve it by company name.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import RawItem
from ..utils import parse_date, safe_float
from .base import BROWSER_UA, Collector

BASE = "https://www.argaam.com/en/tadawul/tasi"

FIRM_SLUGS = [
    "arqaam-capital",
    "morgan-stanley-saudi-arabia",
    "jefferies",
    "united-securities-company",
    "goldman-sachs",
    "hsbc",
    "jpmorgan",
    "citigroup",
    "efg-hermes",
    "alphamena",
    "aljazira-capital",
    "al-rajhi-capital",
    "snb-capital",
    "riyad-capital",
    "anb-capital",
    "gib-capital",
    "bsf-capital",
    "alawwal-capital",
    "u-capital",
    "ci-capital",
]

ROW_SELECTOR = "table tbody tr"

# Regex patterns that identify a company page on Argaam and capture the ticker.
HREF_TICKER_PATTERNS = [
    r"/company/(\d{4})(?:[/?#]|$)",
    r"/tadawul/company/(\d{4})(?:[/?#]|$)",
    r"/markets/company/(\d{4})(?:[/?#]|$)",
    r"[?&]companyid=(\d{4})",
    r"[?&]symbol=(\d{4})",
]


class ArgaamPagesCollector(Collector):
    name = "argaam_pages"
    source_type = "argaam_pages"

    def __init__(self, cfg: dict, debug: bool = False):
        super().__init__(cfg)
        self.debug = debug

    # ------------------------------------------------------------------
    @staticmethod
    def _ticker_from_href(href: str | None) -> str | None:
        if not href:
            return None
        for pattern in HREF_TICKER_PATTERNS:
            m = re.search(pattern, href, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    # ------------------------------------------------------------------
    def _parse_row(self, cells, row_element, firm_hint, page_url):
        """
        Column order on the live table:
          date | firm | company | sector | prev | rating | price | target | change
        The company cell contains a link like /en/company/2222-saudi-aramco.
        """
        if not cells or len(cells) < 6:
            return None

        pub_date = parse_date(cells[0])
        firm = (cells[1] or firm_hint).strip()
        company = (cells[2] or "").strip()
        prev_rating = (cells[4] or "").strip()
        rating = (cells[5] or "").strip()
        if not rating and prev_rating:
            rating, prev_rating = prev_rating, ""

        price = safe_float(cells[6]) if len(cells) > 6 else None
        target = safe_float(cells[7]) if len(cells) > 7 else None

        if not pub_date or not company or not rating:
            return None

        # Look only inside the company cell for the ticker link.
        ticker = None
        if row_element is not None:
            tds = row_element.find_all("td")
            if len(tds) > 2:
                anchor = tds[2].find("a", href=True)
                if anchor:
                    ticker = self._ticker_from_href(anchor["href"])

        return RawItem(
            provider_name=firm,
            ticker=ticker,                      # may be None; ingest resolves by name
            company_name=company,
            rating_raw=rating,
            target_price=target,
            price_at_publication=price,
            publication_date=pub_date,
            source_url=page_url,
            source_type=self.source_type,
            raw_text=" | ".join(cells),
            confidence=0.9,
        )

    # ------------------------------------------------------------------
    def _debug_dump(self, slug: str, html: str):
        out = Path(self.cfg["_root"]) / "data" / "debug"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"argaam_{slug}_rendered.html").write_text(html, encoding="utf-8")

    # ------------------------------------------------------------------
    def fetch(self):
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            print("Playwright not installed. Run: py -3.12 -m playwright install chromium")
            return []

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        items: list[RawItem] = []
        cfg = self.cfg["collectors"]["argaam"]
        wait_ms = int(cfg.get("wait_for_rows_timeout_ms", 15000))
        load_ms = int(cfg.get("page_load_timeout_ms", 30000))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER_UA, locale="en-US")
            page = ctx.new_page()

            for slug in FIRM_SLUGS:
                url = f"{BASE}/{slug}/researchers-opinions"
                print(f"  GET {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=load_ms)
                    page.wait_for_selector(ROW_SELECTOR, timeout=wait_ms)
                except PWTimeout:
                    print(f"    -> no rows within {wait_ms}ms")
                    continue

                if self.debug:
                    self._debug_dump(slug, page.content())

                soup = BeautifulSoup(page.content(), "lxml")
                table = soup.find("table")
                if not table:
                    print(f"    -> no <table>")
                    continue

                firm_hint = slug.replace("-", " ").title()
                parsed = 0
                for row in table.select("tbody tr"):
                    tds = row.find_all("td")
                    cells = [td.get_text(" ", strip=True) for td in tds]
                    item = self._parse_row(cells, row, firm_hint, url)
                    if item:
                        items.append(item)
                        parsed += 1
                print(f"    -> parsed {parsed} rows")

            browser.close()

        print(f"  total rows parsed: {len(items)}")
        return items