"""
AlJazira Capital research-report listing collector.

Discovers report links. Ratings are not usually in the listing, so each linked
PDF is downloaded to data/raw and handed to the PDF extractor. HTML selectors
are a starting point and must be verified before enabling.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import RawItem
from .base import Collector

LINK_SELECTOR = "a[href$='.pdf'], a[href*='/reports/']"   # VERIFY


class AlJaziraCollector(Collector):
    name = "aljazira"
    source_type = "provider_site"

    def fetch(self):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        conf = self.cfg["collectors"]["aljazira"]
        resp = self.get(conf["url"], delay=float(conf.get("request_delay_seconds", 3.0)))
        if resp is None:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        raw_dir = Path(self.cfg["_root"]) / self.cfg["project"]["data_dir"] / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        items: list[RawItem] = []
        for a in soup.select(LINK_SELECTOR):
            href = a["href"]
            url = href if href.startswith("http") else conf["url"].split("/en/")[0] + href
            label = a.get_text(" ", strip=True) or url.rsplit("/", 1)[-1]
            local = raw_dir / re.sub(r"[^\w.\-]", "_", url.rsplit("/", 1)[-1])[:120]
            if not local.exists():
                pdf = self.get(url, delay=float(conf.get("request_delay_seconds", 3.0)))
                if pdf is None:
                    continue
                local.write_bytes(pdf.content)
            items.append(RawItem(
                provider_name="AlJazira Capital",
                company_name=label,
                source_url=url,
                source_type=self.source_type,
                document_path=str(local),
                raw_text=None,
                confidence=0.5,
            ))
        return items