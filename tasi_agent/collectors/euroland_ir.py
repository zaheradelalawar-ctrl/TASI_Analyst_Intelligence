"""
Euroland IR analyst-coverage collector - Playwright edition.

The coverage table is rendered inside a tab that loads via AJAX. Plain
`requests` sees only the page shell. This adapter uses a headless browser to
let the tab render, then extracts the table.

Live table column order:
  Company | Analyst | Country | Rating
"""
from __future__ import annotations

from pathlib import Path

from ..models import RawItem
from ..utils import today_iso
from .base import BROWSER_UA, Collector

COMPANY_CODES = [
    "sa-stag",
    "sa-aramco",
    "sa-sabic",
    "sa-stc",
    "sa-elm",
]

INVALID_RATINGS = {"", "-", "not rated", "n/a", "na", "nr", "--", "no rating"}

ROW_SELECTOR = "table tbody tr"


class EurolandIrCollector(Collector):
    name = "euroland_ir"
    source_type = "ir_page"
    respect_robots = True

    def __init__(self, cfg: dict, debug: bool = False):
        super().__init__(cfg)
        self.debug = debug

    # ------------------------------------------------------------------
    def _parse_row(self, cells: list[str], company: str, page_url: str):
        if not cells or len(cells) < 4:
            return None
        company_cell = (cells[0] or company).strip()
        analyst = (cells[1] or "").strip()
        rating = (cells[3] or "").strip()
        if not analyst or rating.lower() in INVALID_RATINGS:
            return None
        return RawItem(
            provider_name=analyst,
            ticker=None,
            company_name=company_cell,
            rating_raw=rating,
            target_price=None,
            price_at_publication=None,
            publication_date=today_iso(),
            source_url=page_url,
            source_type=self.source_type,
            confidence=0.7,
        )

    # ------------------------------------------------------------------
    def fetch(self):
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            print("Playwright not installed. Run:")
            print("  py -3.12 -m pip install playwright")
            print("  py -3.12 -m playwright install chromium")
            return []

        items: list[RawItem] = []
        debug_dir = Path(self.cfg["_root"]) / "data" / "debug"
        if self.debug:
            debug_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER_UA, locale="en-US")
            page = ctx.new_page()

            for code in COMPANY_CODES:
                url = (f"http://ksatools.eurolandir.com/tools/ee/"
                       f"?companycode={code}&lang=en")
                print(f"  GET {url}")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PWTimeout:
                    print(f"    -> load timeout")
                    continue

                # Try clicking any "Analyst Coverage" tab if present.
                try:
                    tab = page.query_selector(
                        "a:has-text('Analyst'), button:has-text('Analyst'), "
                        "[role='tab']:has-text('Analyst')"
                    )
                    if tab:
                        tab.click()
                        page.wait_for_timeout(1500)
                except Exception:
                    pass

                # Wait for the coverage table to appear.
                try:
                    page.wait_for_selector(ROW_SELECTOR, timeout=10000)
                except PWTimeout:
                    print(f"    -> no table rows appeared")

                if self.debug:
                    (debug_dir / f"euroland_{code}_rendered.html").write_text(
                        page.content(), encoding="utf-8"
                    )

                rows = []
                try:
                    rows = page.eval_on_selector_all(
                        ROW_SELECTOR,
                        """rows => rows.map(r =>
                            Array.from(r.querySelectorAll('td'))
                                .map(c => c.innerText.trim()))""",
                    ) or []
                except Exception as e:
                    print(f"    -> extract failed: {e}")

                print(f"    -> {len(rows)} DOM rows")

                added = 0
                company = code.replace("sa-", "").upper()
                for cells in rows:
                    item = self._parse_row(cells, company, url)
                    if item:
                        items.append(item)
                        added += 1
                print(f"    -> parsed {added} coverage rows")

            browser.close()

        print(f"  total rows parsed: {len(items)}")
        return items