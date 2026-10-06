"""Base connector interface + rate limiting / retry (spy_app_chat_summary §6, §19).

Connectors only: authenticate → fetch → extract → map to AdRecord. They never score.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from .contract import AdRecord


class ConnectorError(Exception):
    pass


class NotConfigured(ConnectorError):
    """Missing credentials / config — shown as 'not_configured' in the health screen."""


class AuthExpired(ConnectorError):
    pass


@dataclass
class RateLimit:
    requests_per_minute: float = 30
    retries: int = 3
    backoff_base: float = 2.0
    concurrency: int = 1


class TokenBucket:
    def __init__(self, per_minute: float):
        self.rate = per_minute / 60.0
        self.capacity = max(1.0, per_minute / 6)
        self.tokens = self.capacity
        self.ts = time.monotonic()
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.ts) * self.rate)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


_buckets: dict[str, TokenBucket] = {}


@dataclass
class FetchParams:
    query: str | None = None  # keyword search
    countries: list[str] = field(default_factory=lambda: ["ALL"])
    page_ids: list[str] = field(default_factory=list)
    active_only: bool = True
    media_type: str | None = None  # video | image | None
    limit: int = 100
    since: str | None = None  # ISO date — incremental sync
    extra: dict[str, Any] = field(default_factory=dict)


class BaseConnector:
    """Subclasses set `key`, `name`, `kind`, `group` and implement `fetch_ads`."""

    key = "base"
    name = "Base"
    kind = "official_api"  # official_api | commercial_api | first_party | export | browser | webhook
    group = "ad_intel"
    rate_limit = RateLimit()
    #: config keys the UI should ask for; secrets are masked
    config_fields: list[dict] = []
    #: True when the connector can run a live keyword search on demand (Product search page)
    supports_search = False

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.bucket = _buckets.setdefault(self.key, TokenBucket(self.rate_limit.requests_per_minute))

    # ---- lifecycle -------------------------------------------------------
    def authenticate(self) -> None:
        missing = [f["key"] for f in self.config_fields if f.get("required") and not self.config.get(f["key"])]
        if missing:
            raise NotConfigured(f"Thiếu cấu hình: {', '.join(missing)}")

    def fetch_ads(self, params: FetchParams) -> Iterator[AdRecord]:
        raise NotImplementedError

    def fetch_products(self, params: FetchParams) -> Iterator[dict]:
        return iter(())

    def health_check(self) -> dict:
        self.authenticate()
        return {"status": "healthy"}

    # ---- helpers ---------------------------------------------------------
    def request(self, method: str, url: str, **kw) -> httpx.Response:
        """HTTP with token-bucket rate limit, Retry-After and exponential backoff on 429/5xx."""
        rl = self.rate_limit
        last: Exception | None = None
        for attempt in range(rl.retries + 1):
            self.bucket.take()
            try:
                r = httpx.request(method, url, timeout=kw.pop("timeout", 60), **kw)
            except httpx.TransportError as e:
                last = e
            else:
                if r.status_code in (401, 403):
                    raise AuthExpired(f"{self.name}: HTTP {r.status_code} — token/cookie hết hạn hoặc không có quyền")
                if r.status_code == 429 or r.status_code >= 500:
                    last = ConnectorError(f"HTTP {r.status_code}")
                    ra = r.headers.get("retry-after")
                    if ra and ra.isdigit():
                        time.sleep(min(int(ra), 120))
                        continue
                else:
                    r.raise_for_status()
                    return r
            time.sleep(min(60, rl.backoff_base ** attempt + random.random()))
        raise ConnectorError(f"{self.name}: hết số lần retry ({last})")
