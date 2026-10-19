"""Price Intelligence self-check, offline (throw-away SQLite, no provider calls): parsers, classification, queue dedupe,
budget refusal, API shape. Run: .venv/Scripts/python test_price_intelligence.py"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.update(DISABLE_SCHEDULER="1", ADMIN_TOKEN="t", EU_MARKET_ENABLED="0", BRIGHTDATA_ENABLED="true", BRIGHTDATA_API_TOKEN="x",
                  BRIGHTDATA_MONTHLY_LIMIT="3", BRIGHTDATA_DAILY_SOFT_LIMIT="2", BRIGHTDATA_RESERVE="0", IMAGE_EMBED_ENABLED="0")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, auto_migrate  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Ad, Product  # noqa: E402
from app.pricing import brightdata, extract, jobs, quota, router  # noqa: E402
from app.pricing.models import MARKET_REFERENCE, NOT_FOUND, VERIFIED_COMPETITOR, PriceJob  # noqa: E402

auto_migrate()

# --- parsers
assert brightdata.snippet_prices("From ₱1,900.00 PHP · Wealth Pixiu Bracelet") == [(1900.0, "PHP")]
assert brightdata.snippet_prices("only $19.99 today") == [], "a bare $ without a country is not a price"
assert brightdata.snippet_prices("only $19.99 today", "US") == [(19.99, "USD")]
assert brightdata.snippet_prices("€1.299,00 Preis") == [(1299.0, "EUR")]
assert brightdata.strip_untrusted("SECURITY NOTICE\n=====UNTRUSTED_ab12_BEGIN=====\n{\"a\":1}\n=====UNTRUSTED_ab12_END=====") == '{"a":1}'
md = "# Pixiu Bracelet\n\nSALE ₱899 (was ₱1,299)\n\n## Related products\n₱5,000 necklace"
assert extract.markdown_price(md, "PH")["price"] == 899.0 and extract.markdown_price(md, "PH")["currency"] == "PHP"
assert extract.markdown_price("# Page\nno money here", "PH") is None
html = '<html><script type="application/ld+json">{"@type":"Product","name":"Pixiu","offers":{"@type":"Offer","price":"1290","priceCurrency":"PHP"}}</script></html>'
got = extract.parse_html(html, "https://shop.example.ph/products/pixiu")
assert got["price"] == 1290.0 and got["currency"] == "PHP" and got["method"] == "jsonld"
assert extract.parse_html('<meta property="og:title" content="X"><meta property="product:price:amount" content="49.90"><meta property="product:price:currency" content="EUR">', "https://x.de/p")["method"] == "meta"
assert extract.parse_html("<p>nothing</p>", "https://x.de/p") is None
assert router.query_text("🔥 Lucky Pixiu Bracelet ₱999 only! #fengshui https://t.co/x") == "Lucky Pixiu Bracelet only"
assert router.query_text("TEMU Haul Download the TEMU app and enjoy back to school finds with code") is None, "a sentence is not a product name"
assert router.query_text("", None, "ab") is None and router.query_text("Thebagmaster (Chưa Đặt Tên)") is None, "placeholder names are not queries"
assert router.overlap("Makapal Tingnan Mas Healthy", "MACA 500 MG 60 KAPS") == 0 and router.overlap("Pixiu bracelet lucky", "Lucky Pixiu Feng Shui Bracelet") == 3

# --- classification: page read → verified only above the confidence floor
o = router._from_page("ad:1", None, "PH", {"price": 1290, "currency": "php", "confidence": 0.9, "url": "https://s.ph/p", "method": "jsonld"}, True, "direct")
assert o.price_type == VERIFIED_COMPETITOR and o.currency == "PHP" and o.seller == "s.ph"
o = router._from_page("ad:1", None, "PH", {"price": 1290, "currency": "PHP", "confidence": 0.5, "url": "https://s.ph/p", "method": "markdown"}, True, "brightdata")
assert o.price_type == MARKET_REFERENCE, "markdown heuristic is never a verified price"
assert router._from_page("ad:1", None, "PH", {"price": 1290, "currency": None, "confidence": 0.9}, True, "direct") is None, "no currency → no price"

# --- queue dedupe + memory
with SessionLocal() as db:
    p = Product(product_code="t-1", canonical_name="Pixiu bracelet", country="PH", features={"markets": ["PH"]})
    db.add(p)
    db.flush()
    a = Ad(external_id="x1", source="t", platform="facebook", product_id=p.id, country="PH", price=999, currency="PHP", price_source="landing",
           landing_url="https://shop.example.ph/products/pixiu", funnel="ladi")
    b = Ad(external_id="x2", source="t", platform="facebook", product_id=p.id, country="PH", funnel="mess", landing_url="https://m.me/shop", ad_text="Pixiu bracelet lucky")
    db.add_all([a, b])
    db.commit()
    ida, idb, idp = a.id, b.id, p.id
    st = jobs.enqueue(db, [f"ad:{ida}", f"ad:{idb}", f"ad:{ida}"], priority=1)
    assert st == {f"ad:{ida}": "queued", f"ad:{idb}": "queued"}
    assert db.query(PriceJob).count() == 2, "one job per subject"
    assert jobs.enqueue(db, [f"ad:{ida}"], priority=10)[f"ad:{ida}"] == "queued" and db.get(PriceJob, 1).priority == 10

# ad with a landing price → verified from the ads row, no network
r = jobs.run_one(1)
assert r["found"] == [VERIFIED_COMPETITOR], r
with SessionLocal() as db:
    s = router.summary(db, [f"ad:{ida}", f"ad:{idb}"])
    assert s[f"ad:{ida}"]["price"] == 999 and s[f"ad:{ida}"]["price_type"] == VERIFIED_COMPETITOR
    assert f"ad:{idb}" not in s
    assert jobs.enqueue(db, [f"ad:{ida}"])[f"ad:{ida}"] == "fresh", "fresh memory → never re-queued"

# --- budget: 3/month, 2/day → the third reservation today is refused, nothing is called
quota.reserve("brightdata", "search_engine")
quota.reserve("brightdata", "search_engine")
try:
    quota.reserve("brightdata", "search_engine")
    raise AssertionError("daily budget not enforced")
except quota.QuotaError as e:
    assert e.code == "quota_exceeded_daily"
with SessionLocal() as db:
    q = quota.remaining(db, "brightdata")
    assert q["day_used"] == 2 and q["month_remaining"] == 1
# messenger ad: the background queue never spends Bright Data / SerpApi → NOT_FOUND (needs_click), ledger untouched
os.environ["SERPAPI_API_KEY"] = ""
r = jobs.run_one(2)
print("job2 ->", r)
assert r.get("found") == [NOT_FOUND], r
with SessionLocal() as db:
    j = db.get(PriceJob, 2)
    assert j.status == "done" and any(s["step"] == "search" and s["code"] == "needs_click" for s in j.steps), j.steps
    assert quota.remaining(db, "brightdata")["day_used"] == 2
# the "Hiện giá" button (paid=True): Bright Data refused by budget, SerpApi has no key → nothing found, nothing stored
l = jobs.lookup_now(f"ad:{idb}")
assert not l["found"] and any(s["step"] == "brightdata_search" and s["code"] == "quota_exceeded_daily" for s in l["steps"]), l

# --- API
c = TestClient(app)
admin = {"X-Admin-Token": "t"}  # writes go through AdminGuard (the web middleware adds this header)
j = c.get(f"/api/prices?subjects=ad:{ida},ad:{idb},bogus,ad:x").json()["prices"]
assert set(j) == {f"ad:{ida}", f"ad:{idb}"} and j[f"ad:{ida}"]["price_type"] == VERIFIED_COMPETITOR and j[f"ad:{idb}"]["price_type"] == NOT_FOUND
j = c.post("/api/prices/enrich", json={"subjects": [f"product:{idp}"], "priority": 5}, headers=admin).json()["prices"]
assert j[f"product:{idp}"]["status"] == "queued"
assert c.get("/api/prices/quota").json()["brightdata"]["day_used"] == 2
assert c.get(f"/api/prices/history?subject=ad:{idb}").json()["job"]["status"] == "done"
assert c.get("/api/prices?subjects=" + ",".join(f"ad:{i}" for i in range(300))).status_code == 400
print("ok")
assert router.coherent_range([330, 900, 1200, 1500, 1800, 22580]) == (1500, 900, 1800), "outliers trimmed"
assert router.coherent_range([100, 5000]) is None and router.coherent_range([899, 950]) == (950, 899, 950)
assert router.plausible(390000, "USD", "US") == "out_of_range" and router.plausible(4399000, "VND", "US") == "currency_market_mismatch"
assert router.plausible(2750, "PHP", "PH") is None and router.plausible(37.99, "USD", "PH") is None
print("ok ranges")
