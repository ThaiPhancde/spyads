"""Offline checks for the EU Market module: normalize, dedup, connector mapping (fixtures = the examples in the
official Kaufland swagger / bol OpenAPI), signing, retry/auth errors, prices, trend/rarity scoring, and the API on a
throw-away SQLite DB with mocked HTTP. No network, no credentials.
Run: cd apps/api && .venv/Scripts/python test_eu_market.py"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/eu_test.db"
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["ADMIN_TOKEN"] = "t"
for k in ("KAUFLAND_CLIENT_KEY", "KAUFLAND_SECRET_KEY", "BOL_CLIENT_ID", "BOL_CLIENT_SECRET", "BIGBUY_API_KEY", "EU_MARKET_ENABLED"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from app.eu_market import analytics, connectors  # noqa: E402
from app.eu_market.connectors import base, bigbuy, bol, kaufland  # noqa: E402
from app.eu_market.connectors.base import ConnectorError  # noqa: E402
from app.eu_market.service import fold, norm_ean, parse_ecb, split_keywords, title_key, to_eur  # noqa: E402

# --- normalize
assert fold("Glücksarmband Straße") == "glucksarmband strasse"
assert split_keywords("feng shui bracelet | Glücksarmband, bracelet porte-bonheur\nlucky bracelet") == \
    ["feng shui bracelet", "glucksarmband", "bracelet porte-bonheur", "lucky bracelet"]
assert norm_ean("4010884503135") == "4010884503135"  # Kaufland swagger example EAN
assert norm_ean("194252013212") == "0194252013212"  # GTIN-12 → EAN-13
assert norm_ean("00194252013212") == "0194252013212"  # GTIN-14 with leading 0
assert norm_ean("4010884503136") is None and norm_ean("abc") is None and norm_ean("0000000000000") is None  # bad check digit / junk
assert title_key("Lucky Bracelet Red", "Acme") == title_key("red  LUCKY bracelet", "ACME") == "acme|bracelet lucky red"
assert title_key("Lucky Bracelet Red", "Acme") != title_key("Lucky Bracelet Red", "Other")
assert title_key("Ring") is None  # too generic to group on

# --- Kaufland: signature + mapping (swagger examples)
sig = kaufland.sign("secret", "get", "https://sellerapi.kaufland.com/v2/products/search?q=x", "", 1700000000)
assert len(sig) == 64 and sig == kaufland.sign("secret", "GET", "https://sellerapi.kaufland.com/v2/products/search?q=x", "", 1700000000)
import hashlib, hmac  # noqa: E401,E402

assert sig == hmac.new(b"secret", b"GET\nhttps://sellerapi.kaufland.com/v2/products/search?q=x\n\n1700000000", hashlib.sha256).hexdigest()
KP = {"id_product": 2, "storefront": "de", "title": "Ein unmoralisches Angebot", "eans": ["4010884503135"], "id_category": 6201,
      "main_picture": "https://media.cdn.kaufland.de/product-images/original/668f.jpg", "manufacturer": "",
      "url": "https://www.kaufland.de/product/2/"}
it = kaufland.to_listing(KP, "de", "angebot")
assert it["source_product_id"] == "2" and it["market_country"] == "DE" and it["currency"] == "EUR" and it["brand"] is None
assert it["product_url"].endswith("/product/2/") and it["price"] is None and it["availability"] == "unknown"  # no buybox yet
kaufland.apply_buybox(it, {"num_units": 1, "units": [{"buybox_rank": 1, "seller": "Kaufland Seller", "price": 1999, "shipping_rate": 700}]})
assert it["price"] == 19.99 and it["shipping_price"] == 7.0 and it["seller_name"] == "Kaufland Seller" and it["availability"] == "in_stock"
assert kaufland.to_listing(KP, "cz")["currency"] == "CZK" and kaufland.to_listing(KP, "pl")["currency"] == "PLN"

# --- bol mapping (OpenAPI examples)
b = bol.to_listing({"title": "Product title", "eans": [{"ean": "0000007740404"}]}, "NL", "laptop", 1)
assert b["ean"] == "0000007740404" and b["source_product_id"] == "0000007740404" and b["signals"][0]["signal_type"] == "search_rank"
assert bol.to_listing({"title": "x", "eans": []}, "NL", None, 1) is None
bol.apply_offer(b, [{"offerId": "o", "retailerId": "0", "bestOffer": True, "price": 41.5}])
assert b["price"] == 41.5 and b["seller_id"] == "0"
bol.apply_ratings(b, [{"rating": 5, "count": 30}, {"rating": 3, "count": 10}])
assert {s["signal_type"]: s["value"] for s in b["signals"]} == {"search_rank": 1, "rating_count": 40, "rating_avg": 4.5}
assert bol.pick_image([{"usage": "PRIMARY", "order": 0, "variants": [{"size": "small", "url": "s"}, {"size": "medium", "url": "m"}]}]) == "m"

# --- BigBuy: defensive mapping, wholesale excl. VAT, no price invented
bb = bigbuy.to_listing({"id": 7, "ean13": "4010884503135", "wholesalePrice": "12.5", "stock": 3}, {"id": 7, "name": "Lamp", "url": "u"})
assert bb["kind"] == "supplier_offer" and bb["price"] == 12.5 and bb["price_includes_vat"] is False and bb["availability"] == "in_stock"
assert bigbuy.to_listing({"id": 8}, None) is None  # no title → not a listing
assert bigbuy.to_listing({"id": 9, "name": "X"}, None)["price"] is None

# --- registry / capability errors
for src in ("kaufland", "bol", "bigbuy"):
    assert connectors.get(src).info()["configured"] is False
try:
    asyncio.run(connectors.get("kaufland").search_products("x", "DE"))
    raise AssertionError("expected missing_credentials")
except ConnectorError as e:
    assert e.code == "missing_credentials" and "KAUFLAND_CLIENT_KEY" in e.detail
for src, call in (("bigbuy", lambda c: c.search_products("x", "ES")), ("allegro", lambda c: c.search_products("x", "PL"))):
    try:
        asyncio.run(call(connectors.get(src)))
        raise AssertionError("expected unsupported")
    except ConnectorError as e:
        assert e.code == "unsupported_capability", e
assert all(c["status"] == "access_restricted" for c in connectors.catalogue() if c["source"] == "emag")
assert connectors.get("allegro").capabilities == {"import"} and "allegro" not in connectors.searchable()  # file import only, never scanned live

# --- retry: 429 (Retry-After 0) then 200; 503×5 → temporarily_unavailable; 401 → unauthorized
base.backoff = lambda attempt, ra, cap=60.0: 0  # no sleeping in tests


async def _req(responses):
    seq = iter(responses)
    c = base.BaseConnector()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(seq))) as cl:
        return await c.request(cl, "GET", "https://x.test/a", params={"q": 1})


assert asyncio.run(_req([httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"ok": 1})])).json() == {"ok": 1}
for resp, code in (([httpx.Response(503)] * 5, "temporarily_unavailable"), ([httpx.Response(429)] * 5, "rate_limited"),
                   ([httpx.Response(401)], "unauthorized"), ([httpx.Response(404)], "bad_response")):
    try:
        asyncio.run(_req(resp))
        raise AssertionError(code)
    except ConnectorError as e:
        assert e.code == code, (e.code, code)

# --- FX (ECB feed shape) + conversion carries its date
day, rates = parse_ecb("""<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time="2026-10-08"><Cube currency="USD" rate="1.0850"/><Cube currency="PLN" rate="4.30"/></Cube></Cube></gesmes:Envelope>""")
assert day == "2026-10-08" and rates == {"USD": 1.085, "PLN": 4.3}
fx = {"EUR": (1.0, None), "PLN": (4.3, day)}
assert to_eur(43, "PLN", fx) == {"eur": 10.0, "rate": 4.3, "rate_date": day} and to_eur(10, "CZK", fx) is None and to_eur(None, "EUR", fx) is None

# --- price stats: one snapshot = no trend; rises counted
now = datetime(2026, 10, 9)
pt = lambda p, d: {"price": p, "currency": "EUR", "captured_at": now - timedelta(days=d)}  # noqa: E731
assert analytics.price_stats([pt(10, 3)])["trend"] is None
st = analytics.price_stats([pt(10, 3), pt(9, 2), pt(11, 1), pt(12, 0)])
assert st["trend"] == "up" and st["change_pct"] == 20.0 and st["consecutive_rises"] == 2 and st["min"] == 9

if __name__ == "__main__":
    print("unit OK")

# --- DB + API (temp SQLite, mocked connectors)
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.eu_market.models import EUListing, EUPriceSnapshot, EUProduct, EUProductMatch  # noqa: E402
from app.eu_market.service import upsert_items  # noqa: E402
from app.main import app  # noqa: E402

with TestClient(app) as client:  # startup = auto_migrate on the temp DB
    H = {"X-Admin-Token": "t"}
    with SessionLocal() as db:
        k = kaufland.to_listing({**KP, "title": "Lucky Bracelet Red", "manufacturer": "Acme"}, "de", "lucky bracelet")
        kaufland.apply_buybox(k, {"units": [{"buybox_rank": 1, "seller": "S1", "price": 1999}]})
        b = bol.to_listing({"title": "Acme lucky bracelet red", "eans": [{"ean": "4010884503135"}]}, "NL", "lucky bracelet", 3)
        bol.apply_offer(b, [{"bestOffer": True, "price": 24.99, "retailerId": "R9"}])
        s = bigbuy.to_listing({"id": 7, "ean13": "4010884503135", "wholesalePrice": 8.4, "stock": 2}, {"name": "Lucky bracelet"})
        other = kaufland.to_listing({"id_product": 3, "title": "Feng Shui Pixiu Bracelet", "eans": [], "url": "https://www.kaufland.de/product/3/"}, "de")
        ids = upsert_items(db, [k, b, s, other], "lucky bracelet", now=now)
        assert len(ids) == 4
        # same EAN across Kaufland / bol / BigBuy → one product; the no-EAN item stays separate
        assert db.scalar(select(func.count(EUProduct.id))) == 2
        assert len({l.product_id for l in db.scalars(select(EUListing).where(EUListing.ean == "4010884503135")).all()}) == 1
        # idempotent + price change → new snapshot; same price within 24h → none
        upsert_items(db, [k], now=now + timedelta(hours=1))
        assert db.scalar(select(func.count(EUListing.id))) == 4
        k2 = dict(k, price=17.99, signals=[])
        upsert_items(db, [k2], now=now + timedelta(hours=2))
        kid = db.scalar(select(EUListing.id).where(EUListing.source == "kaufland", EUListing.source_product_id == "2"))
        assert [x.price for x in db.scalars(select(EUPriceSnapshot).where(EUPriceSnapshot.listing_id == kid)
                                            .order_by(EUPriceSnapshot.captured_at)).all()] == [19.99, 17.99]
        # a lookup without title must not wipe the known title / price
        upsert_items(db, [dict(k2, title=None, price=None, signals=[])], now=now + timedelta(hours=3))
        row = db.get(EUListing, kid)
        assert row.title == "Lucky Bracelet Red" and row.price == 17.99
        assert analytics.match_suppliers(db)["added"] == 0  # BigBuy offer already grouped with the retail product by EAN
        assert db.scalar(select(func.count(EUProductMatch.id))) == 0

    r = client.get("/api/eu-market/products", params={"keywords": "glücksarmband | lucky bracelet", "countries": "DE,NL,ES"}).json()
    assert r["total"] == 1, r
    card = r["rows"][0]
    assert card["offer_count"] == 3 and set(card["sources"]) == {"kaufland", "bol", "bigbuy"} and card["ean"] == "4010884503135"
    assert all(o["observed_at"] and o["source"] and o["market_country"] for o in card["offers"])
    assert client.get("/api/eu-market/products", params={"keywords": "pixiu"}).json()["total"] == 1
    assert client.get("/api/eu-market/products", params={"keywords": "pixiu", "countries": "NL"}).json()["total"] == 0
    d = client.get(f"/api/eu-market/products/{card['id']}").json()
    cmp = d["price_compare"]
    assert cmp["retail_incl_vat"]["offers"] == 2 and cmp["wholesale_excl_vat"]["min_eur"] == 8.4 and "not a margin" in cmp["retail_vs_wholesale"]["note"]
    assert d["trend"]["insufficient_data"] is True and d["rarity"]["label"] == "rare_in_dataset"
    assert client.get("/api/eu-market/overview").json()["listings"] == 4
    src = {s["source"]: s for s in client.get("/api/eu-market/sources").json()["sources"]}
    assert src["kaufland"]["state"] == "missing_credentials" and src["allegro"]["state"] == "no_data" and src["emag"]["state"] == "access_restricted"
    assert src["jsonld"]["state"] == "ready" and src["jsonld"]["public_data"]
    assert src["merkandi"]["state"] == "unsupported_capability" and src["kaufland"]["optional_seller"]
    # writes need the admin token; sync with nothing configured → reported, nothing started, no fake data
    assert client.post("/api/eu-market/admin/sync", json={"keywords": ["x"]}).status_code == 401
    sy = client.post("/api/eu-market/admin/sync", json={"keywords": "x", "countries": ["DE"]}, headers=H).json()
    assert sy["started"] is False and {p["error"] for p in sy["problems"]} == {"missing_credentials"}
    w = client.post("/api/eu-market/watchlists", json={"name": "bracelets", "keywords": ["Lucky Bracelet"], "rules": {"price_drop_pct": 5}},
                    headers=H).json()
    assert w["keywords"] == ["lucky bracelet"] and w["rules"] == {"price_drop_pct": 5.0}
    assert client.patch(f"/api/eu-market/products/{card['id']}", json={"marketer_tags": ["bogus"]}, headers=H).status_code == 422
    assert client.patch(f"/api/eu-market/products/{card['id']}", json={"marketer_tags": ["novelty"]}, headers=H).json()["marketer_tags"] == ["novelty"]
    assert client.get("/api/eu-market/rare", params={"min_score": 0}).status_code == 200
    assert client.get("/api/eu-market/trending").status_code == 200
    assert client.get("/api/eu-market/prices").json()["movers"][0]["stats"]["change_pct"] == -10.0
    assert client.get("/api/eu-market/shops").json()["total"] >= 1
    assert client.get("/api/eu-market/suppliers").json()["suppliers"][0]["name"] == "BigBuy"

    # --- mocked Kaufland end-to-end: credentials set, HTTP mocked, signed headers present
    os.environ.update(KAUFLAND_CLIENT_KEY="ck", KAUFLAND_SECRET_KEY="sk")
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        assert req.headers["Shop-Client-Key"] == "ck" and len(req.headers["Shop-Signature"]) == 64
        if req.url.path.endswith("/products/search"):
            return httpx.Response(200, json={"data": [{"id_product": 11, "title": "Glücksarmband Rot", "eans": [], "url": "https://www.kaufland.de/product/11/"}],
                                             "pagination": {"total": 1, "limit": 30, "offset": 0}})
        return httpx.Response(200, json={"data": {"num_units": 2, "units": [{"buybox_rank": 1, "seller": "S2", "price": 1250}]}})

    real = httpx.AsyncClient
    kaufland.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    from app.eu_market import jobs  # noqa: E402

    res = asyncio.run(jobs.run_search(["glücksarmband"], ["DE"], ["kaufland"]))
    kaufland.httpx.AsyncClient = real
    assert res[0]["written"] == 1 and res[0]["next_cursor"] is None, res
    assert any("storefront=de" in str(r.url) for r in seen)
    runs = client.get("/api/eu-market/admin/sync-runs").json()["items"]
    assert runs[0]["status"] == "ok" and runs[0]["items_written"] == 1
    g = client.get("/api/eu-market/products", params={"q": "Glucksarmband"}).json()["rows"][0]["offers"][0]
    assert g["price"] == 12.5 and g["seller_name"] == "S2" and g["data_access"] == "authorized_api"
    for k in ("KAUFLAND_CLIENT_KEY", "KAUFLAND_SECRET_KEY"):
        os.environ.pop(k)

print("test_eu_market OK")
