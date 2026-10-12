"""Kaufland Marketplace Seller API v2 (https://sellerapi.kaufland.com/?page=rest-api).

Verified against the official docs + the swagger.json shipped in github.com/saleweaver/python-kaufland-api:
  signature = HMAC-SHA256(secret, METHOD \n full-URI \n body \n timestamp), hex — headers Shop-Client-Key,
  Shop-Timestamp, Shop-Signature, User-Agent (software name or "Inhouse_development").
  GET /v2/products/search?storefront&q&limit&offset   -> {data: [Product], pagination: {total, limit, offset}}
  GET /v2/products/{id_product}?storefront             -> {data: Product}
  GET /v2/buybox?id_product&storefront&condition&limit -> {data: {units: [{buybox_rank, seller, price, shipping_rate…}]}}
Buybox prices are integer cents of the storefront currency (swagger example: price 1999, shipping_rate 700).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time

import httpx

from .base import BAD_RESPONSE, UNAUTHORIZED, BaseConnector, ConnectorError, listing

BASE = "https://sellerapi.kaufland.com/v2"
STOREFRONTS = {"DE": "de", "AT": "at", "CZ": "cz", "SK": "sk", "PL": "pl", "FR": "fr", "IT": "it"}
CURRENCY = {"de": "EUR", "at": "EUR", "sk": "EUR", "fr": "EUR", "it": "EUR", "cz": "CZK", "pl": "PLN"}
PAGE = 30
BUYBOX_TOP = int(os.getenv("KAUFLAND_BUYBOX_TOP", "15"))  # price lookups per search page (1 request each)


def sign(secret: str, method: str, uri: str, body: str, ts: int) -> str:
    return hmac.new(secret.encode(), "\n".join([method.upper(), uri, body, str(ts)]).encode(), hashlib.sha256).hexdigest()


def to_listing(p: dict, sf: str, query: str | None = None) -> dict:
    eans = [e for e in p.get("eans") or [] if e]
    cat = (p.get("category") or {}).get("title") if isinstance(p.get("category"), dict) else None
    return listing(source="kaufland", source_product_id=str(p["id_product"]), market_country=sf.upper(),
                   title=(p.get("title") or "").strip(), ean=eans[0] if eans else None, brand=p.get("manufacturer") or None,
                   category=cat, thumbnail_url=p.get("main_picture") or None, product_url=p.get("url") or None,
                   currency=CURRENCY[sf], price_includes_vat=True, raw={"product": p, "query": query})


def apply_buybox(item: dict, data: dict) -> dict:
    units = sorted(data.get("units") or [], key=lambda u: u.get("buybox_rank") or 99)
    if not units:
        item["availability"] = "out_of_stock"
        return item
    best = units[0]
    if best.get("price") is not None:
        item["price"] = round(best["price"] / 100, 2)
    if best.get("shipping_rate") is not None:
        item["shipping_price"] = round(best["shipping_rate"] / 100, 2)
    item.update(availability="in_stock", seller_name=best.get("seller"), seller_id=best.get("seller"))
    item["signals"].append({"signal_type": "offer_count", "value": data.get("num_units") or len(units)})
    item["raw"]["buybox"] = data
    return item


class KauflandConnector(BaseConnector):
    source, label, kind = "kaufland", "Kaufland", "marketplace"
    countries = tuple(STOREFRONTS)
    env_vars = ("KAUFLAND_CLIENT_KEY", "KAUFLAND_SECRET_KEY")
    capabilities = frozenset({"search", "get_product", "price"})
    docs_url = "https://sellerapi.kaufland.com/?page=rest-api"
    notes = "Seller API: catalog search + buybox price per storefront. Optional KAUFLAND_USER_AGENT (default Inhouse_development)."

    def _headers(self, method, url, body):
        ts = int(time.time())
        h = {"Accept": "application/json", "Shop-Client-Key": os.environ["KAUFLAND_CLIENT_KEY"], "Shop-Timestamp": str(ts),
             "Shop-Signature": sign(os.environ["KAUFLAND_SECRET_KEY"], method, url, body, ts),
             "User-Agent": os.getenv("KAUFLAND_USER_AGENT", "Inhouse_development")}
        if body:
            h["Content-Type"] = "application/json"
        return h

    async def healthcheck(self) -> dict:
        self.require("search")
        async with httpx.AsyncClient() as c:
            r = await self.request(c, "GET", f"{BASE}/info/storefront")
        return {"ok": True, "storefronts": r.json().get("data")}

    async def search_products(self, query, country, cursor=None):
        self.require("search", country)
        sf, offset = STOREFRONTS[country], int(cursor or 0)
        async with httpx.AsyncClient() as c:
            r = await self.request(c, "GET", f"{BASE}/products/search",
                                   params={"storefront": sf, "q": query, "limit": PAGE, "offset": offset, "embedded": "category"})
            body = r.json()
            items = [to_listing(p, sf, query) for p in body.get("data") or [] if p.get("id_product")]
            for rank, it in enumerate(items, offset + 1):
                it["signals"].append({"signal_type": "search_rank", "value": rank})
            await asyncio.gather(*(self._price(c, it, sf) for it in items[:BUYBOX_TOP]))
        pg = body.get("pagination") or {}
        nxt = offset + PAGE if pg.get("total") and offset + PAGE < pg["total"] else None
        return {"items": items, "next_cursor": str(nxt) if nxt is not None else None, "total": pg.get("total")}

    async def get_product(self, source_id, country):
        self.require("get_product", country)
        sf = STOREFRONTS[country]
        async with httpx.AsyncClient() as c:
            r = await self.request(c, "GET", f"{BASE}/products/{int(source_id)}", params={"storefront": sf, "embedded": "category"})
            p = r.json().get("data")
            if not p:
                raise ConnectorError(BAD_RESPONSE, "empty product")
            it = to_listing(p, sf)
            await self._price(c, it, sf)
        return it

    async def _price(self, c, item, sf):
        try:
            r = await self.request(c, "GET", f"{BASE}/buybox",
                                   params={"id_product": item["source_product_id"], "storefront": sf, "condition": "new", "limit": 10})
        except ConnectorError as e:
            if e.code == UNAUTHORIZED:
                raise
            item["raw"]["buybox_error"] = e.code  # price stays unknown; listing is still real
            return
        apply_buybox(item, r.json().get("data") or {})
