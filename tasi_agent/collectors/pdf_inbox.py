"""
PDF inbox collector.

Drop research PDFs into data/inbox. Text is extracted with PyMuPDF and passed
to the constrained LLM extractor; the provider name is taken from the document
metadata or the extracted text, never invented.
"""
from __future__ import annotations

from pathlib import Path

from ..extract import extract_from_text
from ..models import RawItem
from ..utils import parse_date, safe_float
from .base import Collector


def _pdf_text(path: Path) -> str | None:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        with fitz.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:
        return None


class PdfInboxCollector(Collector):
    name = "pdf_inbox"
    source_type = "pdf"

    def fetch(self):
        inbox = Path(self.cfg["_root"]) / self.cfg["collectors"]["pdf_inbox"]["inbox"]
        if not inbox.exists():
            return []

        items: list[RawItem] = []
        for path in sorted(inbox.glob("*.pdf")):
            text = _pdf_text(path)
            if not text:
                continue
            fields = extract_from_text(self.cfg, text) or {}
            items.append(RawItem(
                provider_name=fields.get("provider_name") or "Unknown Provider",
                company_name=fields.get("company_name"),
                ticker=fields.get("ticker"),
                rating_raw=fields.get("rating_raw"),
                target_price=safe_float(fields.get("target_price")),
                price_at_publication=safe_float(fields.get("price_at_publication")),
                publication_date=parse_date(fields.get("publication_date")),
                source_url=fields.get("source_url"),
                source_type=self.source_type,
                document_path=str(path),
                raw_text=text[:20000],
                confidence=0.75 if fields else 0.0,
                is_withdrawal=bool(fields.get("is_withdrawal")),
            ))
        return items