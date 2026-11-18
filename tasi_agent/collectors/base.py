"""Collector interface and shared HTTP politeness helpers."""
from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

# Argaam's robots.txt explicitly blocks several named bots. Using a browser-like
# UA improves the chance the public pages render, but you must still honour
# robots.txt and each provider's terms of use. The `respect_robots` flag below
# is true by default. Set it false only if you have confirmed you are allowed.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CRAWLER_UA = "TASIAnalystIntelligence/1.1 (+research contact)"


class Collector:
    name = "base"
    source_type = "unknown"
    respect_robots = True

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

    def fetch(self):                                   # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------
    def robots_allows(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
            rp.read()
            return rp.can_fetch(CRAWLER_UA, url)
        except Exception:
            return True     # if robots.txt is unreachable, default to allow

    # ------------------------------------------------------------------
    def get(self, url: str, delay: float = 3.0) -> requests.Response | None:
        if self.respect_robots and not self.robots_allows(url):
            print(f"    [robots] disallowed by robots.txt: {url}")
            print(f"    [robots] set respect_robots=false or use the Argaam API")
            return None

        time.sleep(delay)
        try:
            r = self.session.get(url, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            print(f"    [http] {exc}")
            return None

        if r.status_code != 200:
            print(f"    [http] status {r.status_code} for {url}")
            return None
        return r