"""bol Retailer API v10 (https://api.bol.com/retailer/public/redoc/v10/retailer.html), NL + BE.

Verified against the official OpenAPI spec (api.bol.com/retailer/public/apispec/Retailer API - v10):
  token: POST https://login.bol.com/token?grant_type=client_credentials (Basic client_id:secret) -> access_token, expires_in
  POST /retailer/products/list {countryCode NL|BE, searchTerm ≤50 chars, page (50/page, ≤200)} -> {products: [{title, eans: [{ean}]}]}
  GET  /retailer/products/{ean}/offers?country-code&best-offer-only=true -> {offers: [{retailerId, price (incl. VAT), condition…}]}
  GET  /retailer/products/{ean}/placement?country-code -> {url, categories: [{categoryName…}]}
  GET  /retailer/products/{ean}/assets?usage=PRIMARY   -> {assets: [{variants: [{size small|medium, url}]}]}
  GET  /retailer/products/{ean}/ratings                -> {ratings: [{rating 1..5, count}]}
Content type application/vnd.retailer.v10+json. The list call gives only title + EAN, so price/url/image need one
call each — limited to BOL_ENRICH_TOP per page to respect bol's per-endpoint rate limits.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx

from .base import NOT_CONFIGURED, UNAUTHORIZED, BaseConnector, ConnectorError, listing

BASE = "https://api.bol.com/retailer"
TOKEN_URL = "https://login.bol.com/token?grant_type=client_credentials"
CT = "application/vnd.retailer.v10+json"
LANG = {"NL": "nl", "BE": "nl-BE"}
ENRICH_TOP = int(os.getenv("BOL_ENRICH_TOP", "12"))
_token: dict = {}  # process cache: {"value", "exp"}


def to_listing(p: dict, country: str, query: str | None, rank: int) -> dict | None:
    eans = [e.get("ean") for e in p.get("eans") or [] if e.get("ean")]
    if not eans or not p.get("title"):
        return None
    it = listing(source="bol", source_product_id=eans[0], market_country=country, title=p["title"].strip(), ean=eans[0],
                 currency="EUR", price_includes_vat=True, raw={"product": p, "query": query})
    it["signals"].append({"signal_type": "search_rank", "value": rank})
    return it


def apply_offer(it: dict, offers: list[dict]) -> None:
    best = next((o for o in offers if o.get("bestOffer")), offers[0] if offers else None)
    if not best:
        it["availability"] = "out_of_stock"
        return
    it.update(price=best.get("price"), seller_id=best.get("retailerId"), availability="in_stock")


def apply_ratings(it: dict, ratings: list[dict]) -> None:
    n = sum(r.get("count") or 0 for r in ratings)
    if n:
        avg = sum((r.get("rating") or 0) * (r.get("count") or 0) for r in ratings) / n
        it["signals"] += [{"signal_type": "rating_count", "value": n}, {"signal_type": "rating_avg", "value": round(avg, 2), "sample_size": n}]


def pick_image(assets: list[dict]) -> str | None:
    for a in sorted(assets, key=lambda a: (a.get("usage") != "PRIMARY", a.get("order") or 0)):
        vs = {v.get("size"): v.get("url") for v in a.get("variants") or [] if v.get("url")}
        if vs:
            return vs.get("medium") or vs.get("small") or next(iter(vs.values()))
    return None


class BolConnector(BaseConnector):
    source, label, kind = "bol", "bol", "marketplace"
    countries = ("NL", "BE")
    env_vars = ("BOL_CLIENT_ID", "BOL_CLIENT_SECRET")
    capabilities = frozenset({"search", "get_product", "price"})
    docs_url = "https://api.bol.com/retailer/public/redoc/v10/retailer.html"
    notes = "Retailer API v10: product list by search term, best competing offer, placement URL, image, rating counts."

    async def _auth(self, c: httpx.AsyncClient) -> str:
        if _token.get("value") and _token["exp"] > time.time():
            return _token["value"]
        r = await c.post(TOKEN_URL, auth=(os.environ["BOL_CLIENT_ID"], os.environ["BOL_CLIENT_SECRET"]),
                         headers={"Accept": "application/json"}, timeout=self.timeout)
        if r.status_code in (400, 401, 403):
            raise ConnectorError(UNAUTHORIZED, f"token HTTP {r.status_code}")
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 299)) - 30)
        return _token["value"]

    async def _call(self, c, method, path, country=None, **kw):
        h = {"Authorization": f"Bearer {await self._auth(c)}", "Accept": CT}
        if kw.get("json_body"):
            h["Content-Type"] = CT
        if country:
            h["Accept-Language"] = LANG[country]
        return await self.request(c, method, f"{BASE}{path}", headers=h, **kw)

    async def healthcheck(self) -> dict:
        self.require("search")
        async with httpx.AsyncClient() as c:
            await self._auth(c)
        return {"ok": True}

    async def search_products(self, query, country, cursor=None):
        self.require("search", country)
        page = int(cursor or 1)
        async with httpx.AsyncClient() as c:
            r = await self._call(c, "POST", "/products/list", country,
                                 json_body=json.dumps({"countryCode": country, "searchTerm": query[:50], "page": page}))
            prods = r.json().get("products") or []
            items = [x for x in (to_listing(p, country, query, (page - 1) * 50 + i) for i, p in enumerate(prods, 1)) if x]
            await asyncio.gather(*(self._enrich(c, it, country) for it in items[:ENRICH_TOP]))
        return {"items": items, "next_cursor": str(page + 1) if len(prods) == 50 and page < 200 else None, "total": None}

    async def get_product(self, source_id, country):
        self.require("get_product", country)
        it = listing(source="bol", source_product_id=source_id, market_country=country, ean=source_id, currency="EUR",
                     price_includes_vat=True)
        async with httpx.AsyncClient() as c:
            await self._enrich(c, it, country)
        return it

    async def _enrich(self, c, it, country):
        ean = it["ean"]
        calls = {"offers": (f"/products/{ean}/offers", {"country-code": country, "best-offer-only": "true"}),
                 "placement": (f"/products/{ean}/placement", {"country-code": country}),
                 "assets": (f"/products/{ean}/assets", {"usage": "PRIMARY"}),
                 "ratings": (f"/products/{ean}/ratings", None)}
        res = await asyncio.gather(*(self._call(c, "GET", p, country, params=q) for p, q in calls.values()), return_exceptions=True)
        for name, r in zip(calls, res):
            if isinstance(r, ConnectorError) and r.code in (UNAUTHORIZED, NOT_CONFIGURED):
                raise r
            if isinstance(r, Exception):
                it["raw"][f"{name}_error"] = getattr(r, "code", type(r).__name__)  # that field stays unknown
                continue
            j = r.json()
            it["raw"][name] = j
            if name == "offers":
                apply_offer(it, j.get("offers") or [])
            elif name == "placement":
                it["product_url"] = j.get("url")
                cats = [x.get("categoryName") for x in j.get("categories") or [] if x.get("categoryName")]
                it["category"] = cats[-1] if cats else None
            elif name == "assets":
                it["thumbnail_url"] = pick_image(j.get("assets") or [])
            elif name == "ratings":
                apply_ratings(it, j.get("ratings") or [])
