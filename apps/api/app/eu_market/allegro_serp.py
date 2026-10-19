"""Allegro via Google Shopping (SerpApi) — allegro.pl itself answers 403 (DataDome), Google's shopping index does not.

One SerpApi call per keyword: engine=google_shopping, gl=pl, q="<keyword> allegro" (measured 2026-10-11: 38/40 results from
merchant "Allegro", vs 4/40 without the suffix; organic `site:allegro.pl/oferta` returned no offer pages at all).
Quota + audit go through the SerpApi budget manager in visual/lens.py (shared IMAGE_SEARCH_*_BUDGET, external_api_calls).

What Google gives and what it doesn't (never filled in by us):
  title, price (PLN), thumbnail (Google-hosted image URL, referenced only), rating / review count  → stored
  the allegro.pl offer URL / offer id and the shop (seller) name                                   → NOT given:
  product_url = Google Shopping product page (lists the Allegro offer), seller_name = None.
"""
from __future__ import annotations

import hashlib
import re

import httpx

from ..visual import lens
from .connectors.base import listing

SOURCE, COUNTRY = "allegro", "PL"
_CATALOG = re.compile(r"catalogid:(\d+)")


def _pid(r: dict) -> str:
    pid = r.get("product_id") or (m.group(1) if (m := _CATALOG.search(r.get("product_link") or "")) else None)
    return f"gshop:{pid}" if pid else "gshop:h" + hashlib.sha1(f"{r.get('title')}|{r.get('price')}".encode()).hexdigest()[:20]


def normalize(j: dict, keyword: str) -> list[dict]:
    out, seen = [], set()
    for r in j.get("shopping_results") or []:
        if "allegro" not in str(r.get("source") or "").lower() or not (r.get("title") or "").strip():
            continue
        sid = _pid(r)
        if sid in seen:
            continue
        seen.add(sid)
        price = r.get("extracted_price") if isinstance(r.get("extracted_price"), (int, float)) and r["extracted_price"] > 0 else None
        cur = lens.currency_of(r.get("price"), "pl") if price is not None else None
        signals = [{"signal_type": "rating_count", "value": r["reviews"], "confidence": 0.6}] if isinstance(r.get("reviews"), int) else []
        out.append(listing(
            source=SOURCE, source_product_id=sid, market_country=COUNTRY, title=r["title"].strip(), price=price,
            currency=cur or ("PLN" if price is not None else None), price_includes_vat=True if price is not None else None,
            thumbnail_url=r.get("thumbnail"), product_url=r.get("product_link"), seller_name=None,
            data_access="serp_google_shopping", confidence=0.6, signals=signals,
            raw={"via": "serpapi_google_shopping", "keyword": keyword, "position": r.get("position"), "merchant": r.get("source"),
                 "price_text": r.get("price"), "rating": r.get("rating"), "reviews": r.get("reviews"), "google_product_id": r.get("product_id")}))
    return out


def unavailable() -> str | None:
    return None if lens.api_key() else "missing_api_key"


def search(keyword: str, limit: int = 20, retries: int = 1) -> dict:
    """-> {"items": [listing], "results_total": n, "search_id": serpapi id}. Raises lens.LensError (quota / provider)."""
    if why := unavailable():
        raise lens.LensError(why, "SERPAPI_API_KEY chưa đặt")
    params = {"engine": "google_shopping", "q": f"{keyword} allegro", "gl": "pl", "hl": "pl", "google_domain": "google.pl",
              "location": "Poland", "api_key": lens.api_key()}
    for attempt in range(retries + 1):
        call_id = lens.reserve(1, None, ["search"])[0]
        try:
            j = lens._call(call_id, lambda: httpx.get(lens.SEARCH_URL, params=params, timeout=60))
            break
        except lens.LensError as e:
            if attempt >= retries or e.code not in ("network_error", "timeout", "provider_error"):
                raise
    items = normalize(j, keyword)
    return {"items": items[:limit], "results_total": len(j.get("shopping_results") or []),
            "search_id": (j.get("search_metadata") or {}).get("id")}
