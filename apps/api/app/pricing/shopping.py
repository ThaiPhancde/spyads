"""SerpApi Google Shopping (https://serpapi.com/google-shopping-api) — offers by keyword per country.
Shares the SerpApi monthly pool with Lens (quota.SERPAPI_POOL); its own daily soft limit (SERPAPI_SHOPPING_DAILY_LIMIT).
Google Product API (per-offer detail) is not called: one Shopping search already lists merchant + price per result.
"""
from __future__ import annotations

import os

import httpx

from ..visual.lens import _http_fail, _scrub, api_key, currency_of
from . import quota

PROVIDER = "serpapi_shopping"
URL = "https://serpapi.com/search.json"
HL = {"PH": "en", "US": "en", "GB": "en", "AU": "en", "NZ": "en", "DE": "de", "FR": "fr", "IT": "it", "ES": "es", "NL": "nl", "PL": "pl",
      "SA": "ar", "AE": "ar", "KW": "ar", "QA": "ar", "OM": "ar", "BH": "ar", "JO": "ar", "EG": "ar", "IQ": "ar", "VN": "vi"}


def unavailable() -> str | None:
    if os.getenv("SERPAPI_SHOPPING_ENABLED", "true").split("#")[0].strip().lower() not in ("1", "true", "yes", "on"):
        return "shopping_disabled"
    if not api_key():
        return "missing_api_key"
    return None


def search(query: str, country: str) -> list[dict]:
    """→ [{title, link, seller, price, currency, price_raw, thumbnail, product_id}] — prices as Google lists them (reference)."""
    why = unavailable()
    if why:
        raise quota.QuotaError(why)
    gl = (country or "us").lower()
    p = {"engine": "google_shopping", "q": query, "gl": gl, "hl": HL.get(gl.upper(), "en"), "api_key": api_key(), "num": 20}
    r = quota.call(PROVIDER, "shopping", lambda: httpx.get(URL, params=p, timeout=30))
    if r.status_code != 200:
        e = _http_fail(r)
        raise quota.QuotaError(e.code, e.detail)
    j = r.json()
    if j.get("error") and not j.get("shopping_results"):
        raise quota.QuotaError("no_results" if "hasn't returned" in str(j["error"]).lower() else "provider_error", _scrub(str(j["error"]))[:200])
    out = []
    for it in j.get("shopping_results") or []:
        val = it.get("extracted_price")
        try:
            val = float(val) if val is not None else None
        except (TypeError, ValueError):
            val = None
        out.append({"title": it.get("title"), "link": it.get("product_link") or it.get("link"), "seller": it.get("source"),
                    "price": val, "currency": currency_of(it.get("price"), gl), "price_raw": it.get("price"), "thumbnail": it.get("thumbnail"),
                    "product_id": it.get("product_id"), "delivery": it.get("delivery")})
    return out
