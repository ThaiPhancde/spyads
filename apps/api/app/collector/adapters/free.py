"""Free, key-less sources (no paid Pipiads / Minea plan needed). All unofficial public endpoints:
they can change without notice → an empty result is flagged 'degraded' by run_connector.

_CffiConnector: browser-impersonating HTTP base shared by the free scrapers (marketplaces.py …).
"""
from __future__ import annotations

from datetime import datetime, timezone
from ..base import BaseConnector


def _csv(v, default: str) -> list[str]:
    return [x.strip() for x in str(v or default).replace("\n", ",").split(",") if x.strip()]


def _iso(sec) -> str | None:
    try:
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError):
        return None


class _CffiConnector(BaseConnector):
    """curl_cffi with a Chrome TLS fingerprint (already installed with meta-ads-collector) — plain httpx gets blocked."""

    def _req(self, method: str, url: str, **kw):
        from curl_cffi import requests as cr

        if not hasattr(self, "_s"):
            self._s = cr.Session(impersonate="chrome")
        self.bucket.take()
        r = self._s.request(method, url, timeout=kw.pop("timeout", 40), **kw)
        r.raise_for_status()
        return r

