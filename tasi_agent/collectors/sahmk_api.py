"""
SAHMK consensus collector.

Uses the official SDK: pip install sahmk
Free tier: 100 requests/day. Analyst consensus is available on the Pro plan,
but the company profile endpoint is free and exposes the consensus fields.
"""
from __future__ import annotations

from ..models import RawItem
from ..utils import today_iso
from .base import Collector


class SahmkCollector(Collector):
    name = "sahmk"
    source_type = "sahmk_api"

    def __init__(self, cfg, api_key: str):
        super().__init__(cfg)
        self.api_key = api_key

    def fetch(self):
        try:
            from sahmk import SahmkClient
        except ImportError:
            return []

        client = SahmkClient(self.api_key)
        items: list[RawItem] = []
        for ticker in ("2222", "1120", "7010", "7203", "4013"):
            try:
                company = client.company(ticker)
            except Exception:
                continue
            analysts = getattr(company, "analysts", None)
            if not analysts:
                continue
            items.append(RawItem(
                provider_name="SAHMK Consensus",
                ticker=ticker,
                company_name=getattr(company, "name_en", ticker),
                rating_raw=getattr(analysts, "consensus", "Hold"),
                target_price=getattr(analysts, "target_mean", None),
                price_at_publication=getattr(company, "current_price", None),
                publication_date=today_iso(),
                source_url="https://www.sahmk.sa",
                source_type=self.source_type,
                confidence=0.85,
            ))
        return items