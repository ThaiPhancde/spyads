"""BigBuy B2B dropshipping catalog (https://www.bigbuy.eu/, API reference api.bigbuy.eu/rest/doc).

Verified against the OpenAPI spec in github.com/shopiqo/bigbuy-oas (dist/v1/openapi.json):
  auth: Authorization: Bearer <API key>; hosts https://api.bigbuy.eu (prod) / https://api.sandbox.bigbuy.eu
  GET /rest/catalog/products.json?page&pageSize&isoCode            — rate limit 10 / hour
  GET /rest/catalog/productsinformation.json?page&pageSize&isoCode — rate limit 24 / hour
There is NO search endpoint: the catalog is paged into eu_listings (kind=supplier_offer) by jobs.sync_catalog with a
checkpointed cursor (= next page), then searched locally. Response field names are not in the spec (no schemas), so
`to_listing` reads them defensively and keeps the raw item — confirm the mapping on the first authorized response.
Wholesale prices exclude VAT (price_includes_vat=False).
"""
from __future__ import annotations

import os

import httpx

from .base import BaseConnector, listing

PAGE_SIZE = int(os.getenv("BIGBUY_PAGE_SIZE", "1000"))
SUPPLIER = {"name": "BigBuy", "supplier_type": "dropshipper", "country": "ES", "website": "https://www.bigbuy.eu",
            "vat_note": "wholesale prices exclude VAT", "access_terms": "BigBuy account + API plan"}


def _base() -> str:
    return "https://api.sandbox.bigbuy.eu" if os.getenv("BIGBUY_SANDBOX", "").lower() in ("1", "true") else "https://api.bigbuy.eu"


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def _num(v):
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_listing(p: dict, info: dict | None) -> dict | None:
    pid = _first(p, "id", "idProduct")
    info = info or {}
    title = _first(info, "name", "title") or _first(p, "name", "title")
    if pid is None or not title:
        return None
    ean = _first(p, "ean13", "ean")
    img = _first(p, "image", "imageUrl", "mainImage")
    if not img and isinstance(p.get("images"), list) and p["images"]:
        first = p["images"][0]
        img = first.get("url") if isinstance(first, dict) else first
    stock = _first(p, "stock", "quantity")
    return listing(kind="supplier_offer", source="bigbuy", source_product_id=str(pid), market_country="ES",
                   title=str(title).strip(), ean=str(ean) if ean else None, brand=_first(p, "manufacturer", "brand"),
                   category=str(_first(p, "category", "taxonomy") or "") or None,
                   price=_num(_first(p, "wholesalePrice", "wholesale_price")), currency="EUR", price_includes_vat=False,
                   thumbnail_url=img, product_url=_first(info, "url"), seller_name="BigBuy", seller_id="bigbuy",
                   availability=("in_stock" if (_num(stock) or 0) > 0 else "out_of_stock") if _num(stock) is not None else "unknown",
                   supplier=SUPPLIER,
                   raw={"product": p, "info": info, "retail_reference": {"retailPrice": p.get("retailPrice"), "inShopsPrice": p.get("inShopsPrice")}})


class BigBuyConnector(BaseConnector):
    source, label, kind = "bigbuy", "BigBuy (B2B)", "supplier"
    countries = ("ES",)  # ships from Spain to the whole EU
    env_vars = ("BIGBUY_API_KEY",)
    capabilities = frozenset({"catalog_sync"})
    docs_url = "https://www.bigbuy.eu/"
    notes = "No search endpoint: catalog synced page-by-page (10 req/hour) then searched locally. BIGBUY_SANDBOX=1 uses the sandbox host."
    max_concurrency = 1
    timeout = 120.0

    def _headers(self, method, url, body):
        return {"Authorization": f"Bearer {os.environ['BIGBUY_API_KEY']}", "Accept": "application/json"}

    async def healthcheck(self) -> dict:
        self.require("catalog_sync")
        async with httpx.AsyncClient() as c:
            await self.request(c, "GET", f"{_base()}/rest/catalog/languages.json")
        return {"ok": True}

    async def sync_catalog(self, cursor=None):
        self.require("catalog_sync")
        page = int(cursor or 1)
        q = {"page": page, "pageSize": PAGE_SIZE, "isoCode": os.getenv("BIGBUY_ISO", "en")}
        async with httpx.AsyncClient() as c:
            prods = (await self.request(c, "GET", f"{_base()}/rest/catalog/products.json", params=q)).json() or []
            infos = (await self.request(c, "GET", f"{_base()}/rest/catalog/productsinformation.json", params=q)).json() or []
        by_id = {str(_first(i, "id", "idProduct")): i for i in infos if isinstance(i, dict)}
        items = [x for x in (to_listing(p, by_id.get(str(_first(p, "id", "idProduct")))) for p in prods if isinstance(p, dict)) if x]
        return {"items": items, "next_cursor": str(page + 1) if len(prods) >= PAGE_SIZE else None, "total": None}
