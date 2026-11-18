"""Alert delivery: rolling daily log + console banner. Nothing invented."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

SEVERITY_LABEL = {"high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]"}


def _format_banner(event: dict) -> str:
    bar = "=" * 72
    sev = SEVERITY_LABEL.get(event["severity"], "[----]")
    lines = [
        bar,
        f"{sev}  {event['ticker']} — {event['title']}",
        bar,
        event["detail"] or "",
    ]
    if event.get("source_url"):
        lines.append("")
        lines.append(f"Source: {event['source_url']}")
    lines.append(bar)
    return "\n".join(lines)


def _safe_print(text: str) -> None:
    """Print without crashing on characters the console cannot encode."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = (getattr(sys.stdout, "encoding", None) or "utf-8")
        try:
            print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
        except Exception:
            # Absolute last resort: strip non-ascii
            print(text.encode("ascii", errors="replace").decode("ascii"))


def _log_path(project_root: Path) -> Path:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"alerts_{dt.date.today().isoformat()}.log"


def dispatch(events: list[dict], project_root: Path,
             mark_cb=None, console: bool = True) -> int:
    if not events:
        return 0
    path = _log_path(project_root)
    delivered = 0
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            banner = _format_banner(e)
            try:
                f.write(banner + "\n\n")
                if console:
                    _safe_print(banner)
                    _safe_print("")
                if mark_cb:
                    mark_cb(e["id"])
                delivered += 1
            except Exception as exc:                     # noqa: BLE001
                _safe_print(f"[notifier] failed to deliver event {e.get('id')}: {exc}")
    _safe_print(f"[notifier] delivered {delivered}/{len(events)} alert(s) -> {path}")
    return delivered