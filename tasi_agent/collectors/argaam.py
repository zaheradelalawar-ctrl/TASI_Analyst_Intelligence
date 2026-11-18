"""
Argaam researcher-opinions collector.

IMPORTANT: the selectors below are a starting point, not a verified contract.
Argaam changes its markup and may require session handling. Verify in a browser
before enabling this collector, and comply with Argaam's terms of use.

Argaam pages expose useful structured columns: research firm, company, date,
rating, current price and target price, which is why structured parsing is
preferred over LLM extraction here.
"""
from __future__ import annotations

import re

from ..models import RawItem
from ..utils import parse_date, safe_float
from .base import Collector

SELECTORS = {
    "table": "table",                       # VERIFY
    "row": "tbody tr",                      # VERIFY
}


class ArgaamCollector(Collector):
    name = "argaam"
    source_type = "argaam"

    def fetch(self):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        conf = self.cfg["collectors"]["argaam"]
        url = conf["base_url"] + "/snb-capital/researchers-opinions"
        resp = self.get(url, delay=float(conf.get("request_delay_seconds", 3.0)))
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        items: list[RawItem] = []
        for row in soup.select(SELECTORS["row"]):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 5:
                continue
            link = row.find("a", href=True)
            items.append(RawItem(
                provider_name=cells[0],
                company_name=cells[1],
                ticker=(re.search(r"\b(\d{4})\b", " ".join(cells)) or [None, None])[1],
                rating_raw=cells[2],
                price_at_publication=safe_float(cells[3]),
                target_price=safe_float(cells[4]),
                publication_date=parse_date(cells[5] if len(cells) > 5 else None),
                source_url=("https://www.argaam.com" + link["href"]) if link else url,
                source_type=self.source_type,
                confidence=0.85,
            ))
        return items