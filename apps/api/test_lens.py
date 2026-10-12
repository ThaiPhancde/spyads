"""Offline checks for live web image discovery (app/visual/lens.py) — SerpApi is MOCKED here: this proves the plumbing
(normalize, currency rules, cache, single-flight, hard budget, error handling, key redaction, API surface), NOT live
discovery. The live test is test_lens_live.py (spends real quota).
Run: cd apps/api && .venv/Scripts/python test_lens.py"""
import glob
import os
import tempfile
import time

_tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/lens.db", DATA_DIR=_tmp, DISABLE_SCHEDULER="1", STORAGE_BACKEND="local",
                  IMAGE_EMBED_ENABLED="0", ADMIN_TOKEN="t", IMAGE_SEARCH_DAILY_BUDGET="3", IMAGE_SEARCH_MONTHLY_BUDGET="250")
os.environ["SERPAPI_API_KEY"] = ""  # app/__init__ fills unset vars from .env — an empty value keeps the real key out

import httpx  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import SessionLocal, auto_migrate  # noqa: E402
from app.visual import lens  # noqa: E402
from app.visual.models import ExternalApiCall, LensSearch  # noqa: E402
from app.visual.service import EXACT, LIKELY_SAME, SIMILAR, QueryImage  # noqa: E402

auto_migrate()
THUMBS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "thumbs", "*", "*.jpg")))
raw = open(THUMBS[0], "rb").read()

# --- currency: only unambiguous
assert lens.currency_of("€19,90", None) == "EUR" and lens.currency_of("$34*", "us") == "USD" and lens.currency_of("$34*", "de") is None
assert lens.currency_of("$34", None) is None and lens.currency_of("US$ 12", None) == "USD" and lens.currency_of("₱499", None) == "PHP"
assert lens.currency_of("120 kr", "se") == "SEK" and lens.currency_of("120 kr", "us") is None and lens.currency_of("29,99 zł", None) == "PLN"
assert lens.currency_of("EUR 5", "us") == "EUR" and lens.currency_of(None, "us") is None

# --- normalize: both SerpApi price shapes, dedupe by link (exact wins), missing fields stay None, position not a %
J = {"exact_matches": [{"position": 1, "title": "Pixiu Bracelet", "link": "https://www.shop.de/p/1?ref=x", "source": "Shop DE",
                        "thumbnail": "https://encrypted-tbn0.gstatic.com/a", "price": "€19,90", "extracted_price": 19.9, "in_stock": True}],
     "visual_matches": [{"position": 1, "title": "Pixiu Bracelet dup", "link": "https://shop.de/p/1/", "source": "Shop DE"},
                        {"position": 2, "title": "Lucky charm", "link": "https://m.example.com/x", "source": "Example",
                         "thumbnail": "https://encrypted-tbn1.gstatic.com/b", "image": "https://example.com/b.jpg",
                         "price": {"value": "$34*", "extracted_value": 34.0, "currency": "$"}, "rating": 4.5, "reviews": 12},
                        {"position": 3, "title": "No price", "link": "https://nop.example/p"}]}
from datetime import datetime  # noqa: E402

items = lens.normalize(J, "de", datetime(2026, 10, 9))
assert len(items) == 3, items
a, b, c = items
assert a["match_type"] == "exact_match" and a["tier"] == LIKELY_SAME and a["price"] == 19.9 and a["currency"] == "EUR" and a["in_stock"] is True
assert a["marketplace"] == "shop.de" and a["shop_name"] == "Shop DE" and a["price_verified"] is False and a["search_origin"] == "live_web"
assert b["price"] == 34.0 and b["currency"] is None and b["price_raw"] == "$34*"  # '$' with country=de → currency unknown, not guessed
assert b["tier"] == SIMILAR and b["similarity"] is None and b["rating"] == 4.5 and b["country"] is None
assert c["price"] is None and c["currency"] is None and c["thumbnail_url"] is None
assert lens.norm_link("https://www.Shop.de/p/1/?a=1") == lens.norm_link("http://shop.de/p/1") == "shop.de/p/1"

# --- missing key: local keeps working, live says why, no call
q = QueryImage(raw)
try:
    lens.start(q, country="de")
    raise AssertionError
except lens.LensError as e:
    assert e.code == "missing_api_key"
assert lens.start(q, country="de", cache_only=True)["status"] == "not_cached"  # cache lookup never needs the key
with SessionLocal() as db:
    assert lens.quota(db)["live_unavailable"] == "missing_api_key" and lens.quota(db)["account"] is None

# --- mocked SerpApi
os.environ["SERPAPI_API_KEY"] = "SECRETKEY123"
calls = {"upload": 0, "search": 0}
mode = {"search": "ok"}
real_post, real_get = httpx.post, httpx.get


def fake_post(url, **kw):
    calls["upload"] += 1
    assert url == lens.UPLOAD_URL and kw["data"]["api_key"] == "SECRETKEY123" and len(kw["files"]["image"][1]) <= lens.UPLOAD_MAX
    return httpx.Response(200, json={"message": "Image uploaded successfully.", "image_id": "IMG123"}, request=httpx.Request("POST", url))


def fake_get(url, params=None, **kw):
    if url == lens.ACCOUNT_URL:
        return httpx.Response(200, json={"plan_name": "Free Plan", "searches_per_month": 250, "plan_searches_left": 249, "this_month_usage": 1},
                              request=httpx.Request("GET", url))
    calls["search"] += 1
    assert params["engine"] == "google_lens" and params["type"] == "all" and params["country"] == params["country"].lower() and "url" not in params
    assert params["image_id"] == "IMG123"
    if mode["search"] == "401":
        return httpx.Response(401, json={"error": "Invalid API key. api_key=SECRETKEY123"}, request=httpx.Request("GET", url + "?api_key=SECRETKEY123"))
    if mode["search"] == "timeout":
        raise httpx.ReadTimeout("timeout")
    time.sleep(0.3)
    return httpx.Response(200, json=J, request=httpx.Request("GET", url))


httpx.post, httpx.get = fake_post, fake_get
try:
    def wait(jid):
        for _ in range(100):
            with SessionLocal() as db:
                s = db.get(LensSearch, jid)
                if s.status != "running":
                    return s
            time.sleep(0.1)
        raise AssertionError("job stuck")

    j1 = lens.start(q, country="DE")
    j1b = lens.start(q, country="de")  # same params while running → joins the same job (single-flight)
    assert j1["status"] == "running" and j1b["job_id"] == j1["job_id"] and j1b.get("joined")
    s = wait(j1["job_id"])
    assert s.status == "ok" and s.result_count == 3 and s.api_calls == 2 and calls == {"upload": 1, "search": 1}, (s.status, s.error_detail, calls)
    assert "api_key" not in str(s.raw) and "SECRETKEY123" not in str(s.raw)
    # same image + params → cache, no call; other country → new search
    j2 = lens.start(q, country="de")
    assert j2["cached"] and j2["job_id"] == j1["job_id"] and calls["search"] == 1
    with SessionLocal() as db:
        v = lens.job_view(db.get(LensSearch, j1["job_id"]), keywords=["lucky charm"])
        assert v["rows"][0]["title"] == "Lucky charm" and v["total_listings"] == 3 and v["discovered"] == 3 and v["with_price"] == 2
        u = lens.usage(db)
        assert u["month_used"] == 1 and u["month_cache_hits"] == 1, u  # upload not counted (SERPAPI_UPLOAD_COUNTS=0)
    # errors: 401 → no retry, error stored, key redacted; timeout → no retry
    mode["search"] = "401"
    j3 = lens.start(q, country="fr")
    s3 = wait(j3["job_id"])
    assert s3.status == "error" and s3.error_code == "unauthorized" and "SECRETKEY123" not in (s3.error_detail or "") and calls["search"] == 2
    mode["search"] = "timeout"
    s4 = wait(lens.start(q, country="it")["job_id"])
    assert s4.status == "error" and s4.error_code == "timeout" and calls["search"] == 3
    with SessionLocal() as db:
        assert db.scalar(__import__("sqlalchemy").select(ExternalApiCall.counted).where(ExternalApiCall.search_id == s3.id, ExternalApiCall.kind == "search")) is False  # 401 → not counted
    # hard daily budget (3): used = de(1) + it timeout(1) = 2 → one more allowed, then refused without calling
    mode["search"] = "ok"
    s5 = wait(lens.start(q, country="es")["job_id"])
    assert s5.status == "ok" and calls["search"] == 4
    s6 = wait(lens.start(q, country="nl")["job_id"])
    assert s6.status == "error" and s6.error_code == "quota_exceeded_daily" and calls["search"] == 4, (s6.error_code, calls)
    assert lens.start(q, country="de")["cached"]  # cache still served when the budget is gone
    with SessionLocal() as db:
        qq = lens.quota(db)
        assert qq["day_used"] == 3 and qq["day_remaining"] == 0 and qq["month_refused"] == 1 and qq["account"]["plan_searches_left"] == 249

    # --- API surface
    os.environ["IMAGE_SEARCH_DAILY_BUDGET"] = "50"
    from fastapi.testclient import TestClient

    from app.main import app

    cl = TestClient(app)
    H = {"X-Admin-Token": "t"}
    up = cl.post("/api/search/images/upload", files={"file": ("a.jpg", raw, "image/jpeg")}, headers=H).json()
    assert len(up["image_ref"]) == 64
    r = cl.post("/api/search/images/live", data={"image_ref": up["image_ref"], "country": "de"}, headers=H).json()
    assert r["cached"] and r["status"] == "ok", r  # the same picture as above → cache hit, no new call
    r2 = cl.post("/api/search/images/live", data={"image_ref": up["image_ref"], "country": "pl", "cache_only": "true"}, headers=H).json()
    assert r2["status"] == "not_cached" and calls["search"] == 4
    assert cl.post("/api/search/images/live", data={"image_ref": "0" * 64}, headers=H).json()["detail"]["code"] == "image_ref_expired"
    res = cl.get(f"/api/search/images/results/{r['job_id']}?limit=2").json()
    assert res["total_listings"] == 3 and len(res["rows"]) == 2 and res["status"] == "ok"
    cp = cl.post("/api/products/compare-prices-by-image", data={"image_ref": up["image_ref"], "lens_job_id": str(r["job_id"])}, headers=H).json()
    assert cp["web"]["priced"] == 2 and any(x["kind"] == "web" for x in cp["same_product"]["rows"] + cp["similar_products"]["rows"])
    web_rows = [x for x in cp["same_product"]["rows"] + cp["similar_products"]["rows"] if x["kind"] == "web"]
    assert all(x["thumb"] or x["image_url"] is None for x in web_rows) and all(x["price_source"] == "search_result" for x in web_rows)
    # rows carry their stored index even when the view re-orders them (keywords) — verify / save act on that index
    kv = cl.get(f"/api/search/images/results/{r['job_id']}?keywords=lucky charm").json()["rows"]
    assert kv[0]["title"] == "Lucky charm" and kv[0]["index"] == 1 and sorted(x["index"] for x in kv) == [0, 1, 2]
    bad = cl.post(f"/api/search/images/results/{r['job_id']}/verify", json={"index": 99}, headers=H)
    assert bad.status_code == 404 and bad.json()["detail"]["code"] == "not_found" and "99" in bad.json()["detail"]["message"]
    assert cl.post("/api/search/images/results/9999/verify", json={"index": 0}, headers=H).json()["detail"]["code"] == "not_found"
    from app.eu_market.connectors import jsonld as _j
    from app.eu_market.connectors.base import ConnectorError as _CE

    def _blocked(url):
        raise _CE("access_restricted", "HTTP 403")

    def _ok(url):
        return {"price": 31.5, "currency": "EUR", "price_includes_vat": True, "availability": "in_stock", "title": "x", "ean": None, "brand": None, "data_access": "public_page_jsonld"}
    _orig = _j.fetch_product
    try:
        _j.fetch_product = _blocked
        v0 = cl.post(f"/api/search/images/results/{r['job_id']}/verify", json={"index": 1}, headers=H).json()
        assert v0["price_check"]["ok"] is False and v0["price_check"]["code"] == "access_restricted" and "chặn" in v0["price_check"]["reason"] and v0["price_verified"] is False and v0["price"] == 34.0
        _j.fetch_product = _ok
        v1 = cl.post(f"/api/search/images/results/{r['job_id']}/verify", json={"index": 1}, headers=H).json()
        assert v1["price_verified"] is True and v1["price"] == 31.5 and v1["currency"] == "EUR" and v1["price_check"]["observed_before"] == 34.0 and v1["index"] == 1
    finally:
        _j.fetch_product = _orig
    sv = cl.post(f"/api/search/images/results/{r['job_id']}/save", json={"index": 0}, headers=H).json()
    assert sv["eu_listing_id"] and not sv["already"], sv
    assert cl.post(f"/api/search/images/results/{r['job_id']}/save", json={"index": 0}, headers=H).json()["already"]
    assert cl.get(f"/api/search/images/results/{r['job_id']}").json()["in_database"] == 1
    qd = cl.get("/api/search/images/quota").json()
    assert qd["api_key_configured"] and qd["monthly_budget"] == 250 and "SECRETKEY123" not in str(qd)
    assert cl.post("/api/search/images/live", data={"image_ref": up["image_ref"]}).status_code == 401  # admin token required
finally:
    httpx.post, httpx.get = real_post, real_get
print(f"lens (MOCKED SerpApi): offline checks passed — calls made to the mock: {calls}")
