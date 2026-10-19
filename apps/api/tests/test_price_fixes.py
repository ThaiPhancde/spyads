"""Price engine fixes (2026-10-14): the HTTP 500 / price-job error (DISTINCT over json columns), SerpApi Lens `products` (HTTP 400),
rung isolation, two-way SerpApi ↔ Bright Data fallback, evidenced product names, free reuse of a near-identical picture's Lens search.
Throw-away SQLite (conftest); providers are stubs — no request leaves the machine."""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import Ad, Creative, Product
from app.pricing import brightdata, identity, jobs, naming_hints, quota, router, shopping
from app.pricing.router import Step
from app.visual import embed, lens
from app.visual import service as vs
from app.visual.models import ExternalApiCall, LensSearch, ProductImage

RUN = uuid.uuid4().hex[:6]
_n = {"i": 0}


def uniq() -> int:
    _n["i"] += 1
    return _n["i"]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    monkeypatch.setenv("PRICE_AUTO_TEST_ENABLED", "0")
    with SessionLocal() as s:
        yield s
        s.rollback()


def product(db, name=None, status="SUGGESTED", **kw) -> Product:
    i = uniq()
    p = Product(product_code=f"PF-{RUN}-{i}", canonical_name=name or "Chưa xác định sản phẩm", name_status=status, country="PH", **kw)
    db.add(p)
    db.flush()
    return p


def ad(db, p, **kw) -> Ad:
    i = uniq()
    a = Ad(external_id=f"pf{RUN}-{i}", source="t", platform="facebook", network="meta", product_id=p.id, country="PH", is_active=True,
           first_seen_at=datetime(2026, 10, 1), last_seen_at=datetime.utcnow(), **kw)
    db.add(a)
    db.flush()
    return a


# ---------------------------------------------------------------- 1. the HTTP 500: DISTINCT over a json column (Postgres)
def test_local_sources_never_selects_distinct_product_rows(db):
    p = product(db, "Posture Corrector Pro", "AUTO_CONFIRMED")
    ads = [ad(db, p, title="Posture Corrector Pro")]
    seen = []

    @event.listens_for(engine, "before_cursor_execute")
    def spy(conn, cur, statement, *a):
        seen.append(statement)

    try:
        identity.local_sources(db, p, ads)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    # Postgres has no equality operator for json: `SELECT DISTINCT products.*` raised "could not identify an equality operator for type json"
    assert not [s for s in seen if s.lstrip().upper().startswith("SELECT DISTINCT") and "products.product_identity" in s], seen


# ---------------------------------------------------------------- 2. SerpApi Lens: type=products was discontinued
def test_lens_products_type_is_sent_as_all_and_a_400_is_not_billed(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "k-test")
    monkeypatch.setenv("IMAGE_SEARCH_DAILY_BUDGET", "100")
    monkeypatch.setenv("IMAGE_SEARCH_MONTHLY_BUDGET", "1000")
    sent = []

    def fake_get(url, params=None, timeout=None):
        sent.append(dict(params))
        if params["type"] == "products":   # what SerpApi answered before the fix
            return httpx.Response(400, json={"error": "The `products` search type has been discontinued."})
        return httpx.Response(200, json={"visual_matches": [{"title": "x", "link": "https://shop.example/p"}]})

    monkeypatch.setattr(lens.httpx, "get", fake_get)
    ids = lens.reserve(1, None, ["search"])
    j = lens.serp_lens({"type": "products", "image_id": "abc", "country": "ph"}, ids[0])
    assert sent[-1]["type"] == "all" and j["visual_matches"]            # our `products` → the provider's `all`
    ids = lens.reserve(1, None, ["search"])
    monkeypatch.setattr(lens, "PROVIDER_TYPE", {})                      # as if the mapping were missing: the 400 must not cost quota
    with pytest.raises(lens.LensError) as e:
        lens.serp_lens({"type": "products", "image_id": "abc"}, ids[0])
    assert e.value.code == "bad_request"
    with SessionLocal() as s:
        assert s.get(ExternalApiCall, ids[0]).counted is False and s.get(ExternalApiCall, ids[0]).error_code == "bad_request"


def test_a_stale_bad_request_never_blocks_lens_for_six_hours(db, monkeypatch):
    """rung_lens used to skip an image for 6 h after ANY failed search, including our own bad_request."""
    p = product(db, "Posture Corrector Pro", "AUTO_CONFIRMED")
    c = Creative(product_id=p.id, type="image", source_url="u", status="stored", thumb_key="thumbs/x.jpg")
    db.add(c)
    db.flush()
    started = []
    monkeypatch.setattr(router, "near_cached_search", lambda *a, **k: None)
    monkeypatch.setattr("app.storage.store", lambda: SimpleNamespace(get=lambda k: _png()))
    key = lens.cache_key(vs.QueryImage(_png()).sha256, None, "PH", "en", "products", None)
    db.add(LensSearch(cache_key=key, status="error", error_code="bad_request", created_at=datetime.utcnow()))
    db.commit()
    monkeypatch.setattr(lens, "start", lambda *a, **k: started.append(k) or {"job_id": None, "status": "not_cached"})
    steps = Step()
    router.rung_lens(db, p, "product:1", "product:1", "PH", steps, False)
    assert started, steps.rows   # not short-circuited by the stale bad_request row


def _png() -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.effect_noise((64, 64), 80).convert("RGB").save(b, "PNG")
    return b.getvalue()


# ---------------------------------------------------------------- 3. one broken rung must not 500 the request
def test_a_rung_that_raises_is_logged_and_the_ladder_goes_on(db, monkeypatch):
    p = product(db, "Posture Corrector Pro", "AUTO_CONFIRMED")
    ad(db, p, title="Posture Corrector Pro")
    db.commit()
    monkeypatch.setattr(router, "rung_local", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    steps = Step()
    out = router.resolve_product(db, p.id, f"product:{p.id}", steps, paid=False)
    assert out == [] and any(s["step"] == "local_marketplace" and s["code"] == "internal_error" for s in steps.rows)
    assert any(s["step"] == "search" and s["code"] == "needs_click" for s in steps.rows)   # it reached the later rungs


def test_lookup_endpoint_answers_with_a_reason_not_a_500(db, monkeypatch):
    p = product(db, "Posture Corrector Pro", "AUTO_CONFIRMED")
    db.commit()
    c = TestClient(app, headers={"X-Admin-Token": "adm"})
    assert c.post("/api/prices/lookup", json={"subject": "product:99999999"}).status_code == 404   # a deleted subject is the caller's mistake
    monkeypatch.setattr(jobs, "resolve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")))
    r = c.post("/api/prices/lookup", json={"subject": f"product:{p.id}"})
    assert r.status_code == 200
    j = r.json()
    assert j["found"] is False and j["error"]["code"] == "internal_error" and "db exploded" in j["error"]["message"]


# ---------------------------------------------------------------- 4. SerpApi and Bright Data cover each other
OFFER = {"title": "Posture Corrector Pro Back Brace", "link": "https://shop.example/p/1", "seller": "Shop", "price": 1500.0, "currency": "PHP", "price_raw": "₱1,500"}
SERP = [{"link": "https://shop.example/p/2", "title": "Posture Corrector Pro Back Brace", "description": "From ₱1,900.00 PHP", "prices": [(1900.0, "PHP")]}]


def stub_providers(monkeypatch, order):
    monkeypatch.setattr(brightdata, "unavailable", lambda cap="search": None)
    monkeypatch.setattr(shopping, "unavailable", lambda: None)
    monkeypatch.setattr(router, "provider_order", lambda country: order)
    monkeypatch.setattr(router, "rung_page", lambda *a, **k: None)   # no page reads: the search results are the evidence


def test_brightdata_down_serpapi_shopping_answers(monkeypatch):
    stub_providers(monkeypatch, ["brightdata", "serpapi_shopping"])
    monkeypatch.setattr(brightdata, "search", lambda q, c=None: (_ for _ in ()).throw(brightdata.BrightDataError("timeout")))
    monkeypatch.setattr(shopping, "search", lambda q, c: [OFFER])
    steps = Step()
    out = router.rung_reference("product:1", "product:1", "PH", "Posture Corrector Pro", steps)
    assert [o.provider for o in out] == ["serpapi_shopping"] and out[0].price_amount == 1500.0
    assert [(s["step"], s["ok"]) for s in steps.rows if s["step"] in ("brightdata_search", "serpapi_shopping")] == [("brightdata_search", False), ("serpapi_shopping", True)]


def test_serpapi_out_of_quota_brightdata_answers(monkeypatch):
    stub_providers(monkeypatch, ["serpapi_shopping", "brightdata"])
    monkeypatch.setattr(shopping, "search", lambda q, c: (_ for _ in ()).throw(quota.QuotaError("quota_exceeded_daily")))
    monkeypatch.setattr(brightdata, "search", lambda q, c=None: SERP)
    steps = Step()
    out = router.rung_reference("product:1", "product:1", "PH", "Posture Corrector Pro", steps)
    assert [o.provider for o in out] == ["brightdata"] and out[0].currency == "PHP"
    assert [(s["step"], s["code"]) for s in steps.rows if s["step"] == "serpapi_shopping"] == [("serpapi_shopping", "quota_exceeded_daily")]


def test_both_down_is_empty_and_says_why_never_a_guess(monkeypatch):
    stub_providers(monkeypatch, ["brightdata", "serpapi_shopping"])
    monkeypatch.setattr(brightdata, "search", lambda q, c=None: (_ for _ in ()).throw(brightdata.BrightDataError("rate_limited")))
    monkeypatch.setattr(shopping, "search", lambda q, c: (_ for _ in ()).throw(quota.QuotaError("timeout")))
    steps = Step()
    assert router.rung_reference("product:1", "product:1", "PH", "Posture Corrector Pro", steps) == []
    assert {s["code"] for s in steps.rows if not s["ok"]} == {"rate_limited", "timeout"}


def test_provider_with_more_budget_left_goes_first(monkeypatch):
    monkeypatch.setattr(brightdata, "unavailable", lambda cap="search": None)
    monkeypatch.setattr(shopping, "unavailable", lambda: None)
    monkeypatch.setenv("BRIGHTDATA_MONTHLY_LIMIT", "5000")
    monkeypatch.setenv("IMAGE_SEARCH_MONTHLY_BUDGET", "250")
    assert router.provider_order("PH")[0] == "brightdata"
    monkeypatch.setattr(brightdata, "unavailable", lambda cap="search": "brightdata_disabled")
    assert router.provider_order("PH") == ["serpapi_shopping", "brightdata"]   # an off provider goes last, it is still tried at the end


# ---------------------------------------------------------------- 5. a name worth searching, with its source
def test_name_candidates_come_with_their_source_best_first(db):
    p = product(db, "Mas Healthy Makapal Tingnan")   # an ad hook as name: not trusted
    a = ad(db, p, title="Magnetic Posture Corrector Back Brace for Men", landing_url="https://shop.example/products/magnetic-posture-corrector")
    db.add(Creative(ad_id=a.id, product_id=p.id, type="image", source_url="u", status="stored", ocr_text="Posture Corrector\nOrder now"))
    db.commit()
    got = naming_hints.candidates(db, p, [a])
    assert got and got[0]["source"] in ("caption", "evidence") and "posture" in got[0]["name"].lower()
    assert any(c["source"] == "ocr" and "Posture Corrector" in c["name"] for c in got)
    assert not any("healthy" in c["name"].lower() for c in got)          # the hook is not a candidate


def test_unnamed_product_without_evidence_has_no_search_name(db):
    p = product(db)
    a = ad(db, p, title="Shop now", landing_url="https://www.facebook.com/somepage")
    db.commit()
    assert naming_hints.best_name(db, p, [a]) is None
    assert identity.search_name(p, [a], db) is None


def test_clip_neighbour_with_a_confirmed_name_supplies_the_name(db):
    embed.INDEX.ids, embed.INDEX.mat, embed.INDEX.since, embed.INDEX.count, embed.INDEX.checked_at = np.zeros(0, dtype=np.int64), np.zeros((0, embed.DIM), dtype=np.float32), None, 0, 0.0
    named = product(db, "Magnetic Posture Corrector", "AUTO_CONFIRMED")
    anon = product(db)
    v = np.random.default_rng(7).normal(size=embed.DIM)
    for p in (named, anon):
        a = ad(db, p)
        c = Creative(ad_id=a.id, product_id=p.id, type="image", source_url="u", status="stored", position=0)
        db.add(c)
        db.flush()
        db.add(ProductImage(source="creative", source_id=c.id, embedding=embed.to_bytes(v / np.linalg.norm(v)), processing_status="done",
                            phash=None, embedded_at=datetime.utcnow(), indexed_at=datetime.utcnow()))
    db.commit()
    got = naming_hints.candidates(db, anon, [])
    assert [c["source"] for c in got] == ["clip"] and got[0]["name"] == "Magnetic Posture Corrector"


# ---------------------------------------------------------------- 6. the same picture is never searched (and paid) twice
def test_near_identical_picture_reuses_the_finished_lens_search(db):
    ph = "a5a5a5a5a5a5a5a5"
    sha = uuid.uuid4().hex + uuid.uuid4().hex[:32]
    db.add(ProductImage(source="creative", source_id=900000 + uniq(), image_sha256=sha, phash=ph, dhash=ph, processing_status="done", indexed_at=datetime.utcnow()))
    s = LensSearch(cache_key=uuid.uuid4().hex, image_sha256=sha, status="ok", params={"country": "ph", "type": "products"}, results=[],
                   expires_at=datetime.utcnow() + timedelta(days=3))
    db.add(s)
    db.commit()
    vs.HASH_INDEX.refresh(db, force=True)
    q = SimpleNamespace(phash="a5a5a5a5a5a5a5a4", dhash=ph)       # a re-encoded copy: 1 bit away
    assert router.near_cached_search(db, q, "PH").id == s.id
    assert router.near_cached_search(db, q, "SA") is None          # another market = another shopping result: not reused
    assert router.near_cached_search(db, SimpleNamespace(phash="0123456789abcdef", dhash="fedcba9876543210"), "PH") is None


def test_every_configured_country_has_a_currency_and_a_search_language():
    from app.markets import REGIONS

    missing = [c for r in REGIONS.values() for c in r if c != "ALL" and c not in router.MARKET_CURRENCY]
    assert not missing, missing


def test_metadata_read_off_an_app_install_link_is_not_a_name(db):
    p = product(db, "U Cloud Touch Blurring Skin Tint Foundation", "SUGGESTED", name_source="ad_title")
    a = ad(db, p, title="U Cloud Touch Blurring Skin Tint Foundation", funnel="app", landing_url="https://snssdk1180.onelink.me/BAuo?af_dp=x",
           product_metadata={"title": "Yate 1916 Digital Kid's Fashion Sports Watch"})
    db.commit()
    names = [c["name"] for c in naming_hints.candidates(db, p, [a])]
    assert names and "Watch" not in " ".join(names) and "Foundation" in names[0]


def test_google_shopping_redirect_links_are_not_read_as_shop_pages(monkeypatch):
    stub_providers(monkeypatch, ["serpapi_shopping", "brightdata"])
    read = []
    monkeypatch.setattr(router, "rung_page", lambda subject, cluster, market, url, *a, **k: read.append(url))
    google = {**OFFER, "link": "https://www.google.com/search?ibp=oshop&q=Posture+Corrector&prds=productid:1"}
    shop = {**OFFER, "link": "https://shop.example/p/1"}
    monkeypatch.setattr(shopping, "search", lambda q, c: [google, shop])
    monkeypatch.setattr(brightdata, "search", lambda q, c=None: [])
    router.rung_reference("product:1", "product:1", "PH", "Posture Corrector Pro", Step())
    assert read == ["https://shop.example/p/1"]
