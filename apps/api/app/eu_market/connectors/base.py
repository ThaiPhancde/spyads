"""Connector contract for EU marketplaces / B2B suppliers (spec §8).

A connector only fetches + maps one source into the normalized listing dict below; grouping, storage and scoring
live in ..service. Credentials come from env vars only (never the frontend / DB / logs). A connector that cannot do
something raises ConnectorError — never fabricates data.
"""
from __future__ import annotations

import asyncio
import os
import random

import httpx

# the error vocabulary the UI shows (spec §8; image-search spec §6.2: missing_credentials / access_restricted / …)
NOT_CONFIGURED, UNAUTHORIZED, RATE_LIMITED = "missing_credentials", "unauthorized", "rate_limited"
UNAVAILABLE, UNSUPPORTED, BAD_RESPONSE = "temporarily_unavailable", "unsupported_capability", "bad_response"


class ConnectorError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def listing(**kw) -> dict:
    """Normalized listing (spec §6 JSON). Unknown stays None / "unknown" — never guessed."""
    base = {"kind": "marketplace_listing", "source": None, "source_product_id": None, "market_country": None, "title": None,
            "price": None, "currency": None, "shipping_price": None, "price_includes_vat": None, "thumbnail_url": None,
            "product_url": None, "seller_id": None, "seller_name": None, "ean": None, "brand": None, "category": None,
            "availability": "unknown", "data_access": "authorized_api", "confidence": 1.0, "signals": [], "supplier": None,
            "raw": {}}
    base.update(kw)
    return base


class BaseConnector:
    source = ""
    label = ""
    kind = "marketplace"  # marketplace | supplier
    status = "available"  # available = implemented against a verified spec | planned = registry entry only
    countries: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()  # all required
    capabilities: frozenset[str] = frozenset()  # search | get_product | price | catalog_sync
    docs_url = ""
    notes = ""
    max_concurrency = 2  # per-source request concurrency
    retries = 4
    timeout = 25.0

    def __init__(self):
        self._sem = asyncio.Semaphore(self.max_concurrency)

    def configured(self) -> bool:
        return bool(self.env_vars) and all(os.getenv(v) for v in self.env_vars)

    def info(self) -> dict:
        return {"source": self.source, "label": self.label, "kind": self.kind, "status": self.status,
                "countries": list(self.countries), "env_vars": list(self.env_vars), "configured": self.configured(),
                "capabilities": sorted(self.capabilities), "docs_url": self.docs_url, "notes": self.notes,
                "optional_seller": self.source in ("kaufland", "bol", "bigbuy"),  # spec §6.1: never required
                "public_data": self.status == "available" and self.source == "jsonld"}

    def require(self, capability: str, country: str | None = None):
        if self.status != "available" or capability not in self.capabilities:
            raise ConnectorError(UNSUPPORTED, f"{self.source} does not support {capability}")
        if not self.configured():
            raise ConnectorError(NOT_CONFIGURED, "set " + ", ".join(self.env_vars))
        if country is not None and country not in self.countries:
            raise ConnectorError(UNSUPPORTED, f"{self.source} has no {country} marketplace ({', '.join(self.countries)})")

    async def healthcheck(self) -> dict:
        raise ConnectorError(UNSUPPORTED, "healthcheck")

    async def search_products(self, query: str, country: str, cursor: str | None = None) -> dict:
        """-> {"items": [listing], "next_cursor": str | None, "total": int | None}"""
        raise ConnectorError(UNSUPPORTED, f"{self.source} has no product search")

    async def get_product(self, source_id: str, country: str) -> dict:
        raise ConnectorError(UNSUPPORTED, f"{self.source} has no product lookup")

    async def sync_catalog(self, cursor: str | None = None) -> dict:
        """B2B catalogs without search: page through the feed. -> {"items", "next_cursor"}"""
        raise ConnectorError(UNSUPPORTED, f"{self.source} has no catalog feed")

    # ------------------------------------------------------------------ HTTP
    def _headers(self, method: str, url: str, body: str) -> dict:
        return {}

    async def request(self, client: httpx.AsyncClient, method: str, url: str, *, params: dict | None = None,
                      json_body: str | None = None, headers: dict | None = None) -> httpx.Response:
        """Bounded concurrency + backoff on 429/5xx/network errors; maps final failures to ConnectorError codes."""
        if params:
            url = str(httpx.URL(url, params=params))  # signed sources (Kaufland) sign the exact final URI
        last = ""
        for attempt in range(self.retries + 1):
            hdrs = {**self._headers(method, url, json_body or ""), **(headers or {})}
            try:
                async with self._sem:
                    r = await client.request(method, url, content=json_body, headers=hdrs, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last, code = f"{type(e).__name__}", UNAVAILABLE
            else:
                if r.status_code in (401, 403):
                    raise ConnectorError(UNAUTHORIZED, f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code == 429 or r.status_code >= 500:
                    code = RATE_LIMITED if r.status_code == 429 else UNAVAILABLE
                    last = f"HTTP {r.status_code}"
                    if attempt < self.retries:
                        await asyncio.sleep(backoff(attempt, r.headers.get("Retry-After")))
                        continue
                    raise ConnectorError(code, last)
                if r.status_code >= 400:
                    raise ConnectorError(BAD_RESPONSE, f"HTTP {r.status_code}: {r.text[:300]}")
                return r
            if attempt < self.retries:
                await asyncio.sleep(backoff(attempt, None))
        raise ConnectorError(code, last)


def backoff(attempt: int, retry_after: str | None, cap: float = 60.0) -> float:
    """Retry-After (seconds) when the source sends it, else exponential 1,2,4,8… with jitter."""
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), cap)
    return min(cap, 2 ** attempt) * (0.75 + random.random() / 2)
