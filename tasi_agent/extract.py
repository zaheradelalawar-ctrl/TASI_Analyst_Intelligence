"""
AI-assisted extraction for unstructured research text (PDFs, article bodies).

Hard rule: the model may only transcribe facts that are explicitly present.
Missing fields must be null. The caller rejects records whose required fields
are absent — the extractor never fills gaps with plausible guesses.
"""
from __future__ import annotations

import json
import re

import requests

PROMPT = """You are a strict information-extraction component for equity research documents.

Extract ONLY facts explicitly written in the document. Never infer, never guess,
never complete a missing value. If a field is not explicitly stated, return null.

Return a single JSON object with exactly these keys:
{
  "provider_name": string|null,          // the research house that published the report
  "company_name": string|null,           // the company analysed
  "ticker": string|null,                 // 4-digit Tadawul ticker if printed
  "rating_raw": string|null,             // the rating exactly as printed, verbatim
  "target_price": number|null,           // 12-month target price if printed
  "price_at_publication": number|null,   // current/share price printed in the report
  "publication_date": string|null,       // ISO YYYY-MM-DD if a date is printed
  "is_withdrawal": boolean               // true only if coverage is explicitly withdrawn
}

Return JSON only. No commentary, no markdown fences.
"""


def _ollama_generate(cfg: dict, prompt: str, text: str) -> str | None:
    url = cfg["extraction"]["ollama_url"].rstrip("/") + "/api/generate"
    payload = {
        "model": cfg["extraction"]["ollama_model"],
        "prompt": f"{prompt}\n\nDOCUMENT:\n{text[:12000]}",
        "stream": False,
        "options": {"temperature": 0.0},
    }
    try:
        r = requests.post(url, json=payload, timeout=180)
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception:
        return None


def _parse_json_blob(text: str | None) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def extract_from_text(cfg: dict, text: str) -> dict | None:
    """Return a dict of extracted fields, or None if extraction is unavailable."""
    if cfg["extraction"]["backend"] == "none" or not text.strip():
        return None
    raw = _ollama_generate(cfg, PROMPT, text)
    return _parse_json_blob(raw)