"""
Argaam Developer API collector.

Endpoint (production):
  GET https://api.argaam.com/api/v1.1/markets/{marketId}/analyst-opinions
  Header: apikey: <your key>

Endpoint (sandbox, free for testing):
  GET https://api-sandbox.argaam.com/api/v1.1/markets/1/analyst-opinions

Each record includes: analyst firm name, rating (Buy / Overweight / Hold /
Underweight / Sell), target price, and publication date.
"""
from __future__ import annotations

from ..models import RawItem
from ..utils import parse_date, safe_float
from .base import Collector


class ArgaamApiCollector(Collector):
    name = "argaam_api"
    source_type = "argaam_api"

    def __init__(self, cfg, api_key: str, sandbox: bool = False):
        super().__init__(cfg)
        self.api_key = api_key
        self.base = ("https://api-sandbox.argaam.com"
                     if sandbox else "https://api.argaam.com")
        self.session.headers.update({"apikey": api_key, "Accept": "application/json"})

    def fetch(self):
        items: list[RawItem] = []
        page = 1
        while True:
            url = f"{self.base}/api/v1.1/markets/1/analyst-opinions?page={page}&records=50"
            resp = self.get(url, delay=1.0)
            if resp is None:
                break
            payload = resp.json()
            data = payload.get("data") or []
            if not data:
                break
            for rec in data:
                items.append(RawItem(
                    provider_name=rec.get("analystFirmName") or rec.get("firm"),
                    ticker=str(rec.get("stockSymbol") or rec.get("ticker") or ""),
                    company_name=rec.get("companyName") or rec.get("company"),
                    rating_raw=rec.get("rating"),
                    target_price=safe_float(rec.get("targetPrice")),
                    price_at_publication=safe_float(rec.get("currentPrice")),
                    publication_date=parse_date(rec.get("publicationDate") or rec.get("ratingDate")),
                    source_url="https://www.argaam.com/en/tadawul/tasi",
                    source_type=self.source_type,
                    confidence=0.95,
                ))
            meta = payload.get("meta") or {}
            if page >= (meta.get("totalPages") or 1):
                break
            page += 1
        return items