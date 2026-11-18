"""Configuration loading with deep-merge over sane defaults."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "project": {
        "name": "TASI Analyst Consensus Intelligence Agent",
        "timezone": "Asia/Riyadh",
        "freshness_days": 90,
        "data_dir": "data",
        "output_dir": "output",
        "db_path": "data/tasi_analyst.db",
    },
    "consensus": {
        "confirm_threshold": 2,
        "strong_threshold": 3,
        "require_verified_only": True,
        "allowed_statuses": ["verified", "partial"],
    },
    "extraction": {
        "backend": "ollama",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.1:8b",
        "min_confidence": 0.55,
    },
    "collectors": {
        "argaam": {"enabled": False, "base_url": "https://www.argaam.com/en/tadawul/tasi",
                   "request_delay_seconds": 3.0},
        "aljazira": {"enabled": False,
                     "url": "https://aljaziracapital.com.sa/en/insights/research-reports/",
                     "request_delay_seconds": 3.0},
        "pdf_inbox": {"enabled": True, "inbox": "data/inbox"},
    },
    "reporting": {"formats": ["html", "md", "xlsx"], "activity_window_days": 1, "top_n_other": 40},
    "performance": {"horizons": [20, 60, 120], "min_sample_for_publication": 5},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path = "config.yaml") -> dict:
    p = Path(path)
    cfg = dict(DEFAULTS)
    if p.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    root = p.parent if p.exists() else Path.cwd()
    cfg["_root"] = str(root)
    return cfg


def project_path(cfg: dict, key: str) -> Path:
    """Resolve a configured path relative to the project root."""
    root = Path(cfg.get("_root", "."))
    val = Path(cfg["project"][key])
    return val if val.is_absolute() else (root / val)


def db_path(cfg: dict) -> Path:
    root = Path(cfg.get("_root", "."))
    p = Path(cfg["project"]["db_path"])
    return p if p.is_absolute() else root / p