"""Phase 7 personalized search on GET /api/search: modes (for_me / claimable / followed / similar), multi-keyword,
filters (category, demand, market, language, source, fit, real engagement), advertiser white/blacklist, ownership,
no claiming, several MKTs, paging + fit cache. Disposable DB only."""
from datetime import datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app import allocation as A, personal_search as PS, recommend as R
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from app.services import cluster_membership as CM
from app.workspace.models import AdvertiserFilter
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client, write


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    R._MEMO.clear(); R._VEC.update(at=0.0, v={}); PS._FIT.clear()
    from app import similar as S
    from app.services import llm, query_intent
    from app.visual import embed
    S._TEXT.update(at=0.0, v={}); embed._pgv.update(ok=None, at=0)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI")))
    monkeypatch.setattr(query_intent, "expand", lambda db, kw: __import__("app.services.lexicon", fromlist=["expand"]).expand(kw) | {"source": "lexicon"})


def item(name, category="accessories", country="PH", language="en", advertiser=None, network="meta", demand=0.5,
         likes=None, engagement=0, ads=4):
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        i = uuid.uuid4().hex[:8]
        p = Product(product_code=f"PS-{i}", canonical_name=name, category=category, country=country, language=language,
                    name_status="AUTO_CONFIRMED", in_target=True, first_seen_at=now - timedelta(days=5), last_seen_at=now,
                    opportunity_score=50, features={"unique_ads": ads, "advertiser_count": 2, "longevity_days": 20,
                                                    "markets": [country], "engagement_per_ad": engagement},
                    potential={"vector": {"market_demand": demand}})
        db.add(p); db.flush()
        a = Ad(external_id=f"ps-{i}", source="meta_library", platform="facebook", network=network, product_id=p.id,
               advertiser_id=advertiser, title=name, ad_text=name, search_text=name.lower(), country=country, countries=[country],
               language=language, likes=likes, is_active=True, in_target=country in ("PH", "US"), first_seen_at=now - timedelta(days=5), last_seen_at=now)
        db.add(a); db.flush()
        CM.sync(db, [a.id])
        return p.id, a.id


def search(c, **q):
    q.setdefault("limit", 100)
    return c.get("/api/search", params=q).json()


def ids(res):
    return [r["id"] for r in res["rows"]]


def allocations():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(A.ProductAllocation))


def test_multi_keyword_and_filters():
    users()
    sun, _ = item("Polarized sunglasses", language="en", network="meta", demand=0.8)
    leash, _ = item("Dog leash nylon", category="pets", language="vi", network="tiktok", demand=0.2)
    hat, _ = item("Summer hat", country="US")
    rich, _ = item("Sunglasses deluxe", likes=120, engagement=150)
    ca = client("mkt-a@example.test")
    assert set(ids(search(ca, q="sunglasses|leash"))) == {sun, leash, rich}
    assert ids(search(ca, q="sunglasses|leash", category="pets")) == [leash]
    assert ids(search(ca, q="sunglasses|leash", language="vi")) == [leash]
    assert ids(search(ca, q="sunglasses|leash", network="tiktok")) == [leash]
    assert set(ids(search(ca, q="sunglasses|leash", min_demand=0.5))) == {sun, rich}
    assert ids(search(ca, q="sunglasses|leash", min_engagement=0)) == [rich]  # only real engagement data
    assert ids(search(ca, q="sunglasses|leash", min_engagement=200)) == []
    assert hat in ids(search(ca, country="US")) and hat not in ids(search(ca, country="PH"))


def test_claimable_hides_other_mkts_products_and_search_never_claims():
    a, b, _ = users()
    p1, _ = item("Sunglasses one"); p2, _ = item("Sunglasses two"); p3, _ = item("Sunglasses three")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(cb, "POST", "/allocations/claim", {"product_id": p1})
    write(ca, "POST", "/allocations/claim", {"product_id": p3})
    before = allocations()
    plain = {r["id"]: r["allocation"] for r in search(ca, q="sunglasses")["rows"]}
    assert plain == {p1: "taken", p2: "available", p3: "mine"}  # research view: visible, labelled
    assert ids(search(ca, q="sunglasses", mode="claimable")) == [p2]
    for mode in ("for_me", "followed", "similar"):
        assert p1 not in ids(search(ca, q="sunglasses", mode=mode))
    assert {r["id"]: r["allocation"] for r in search(cb, q="sunglasses")["rows"]}[p1] == "mine"
    assert allocations() == before  # no search call created or changed an allocation


def test_for_me_ranks_by_personal_fit_with_reasons_per_mkt():
    users()
    _, seed_a = item("Beauty serum seed", category="beauty")
    _, seed_b = item("Kitchen knife seed", category="kitchen")
    beauty, _ = item("Beauty cream", category="beauty")
    kitchen, _ = item("Kitchen pan", category="kitchen")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    write(ca, "PUT", f"/workspace/ads/{seed_a}", {"saved": True})
    write(cb, "PUT", f"/workspace/ads/{seed_b}", {"saved": True})
    ra, rb = search(ca, mode="for_me", sort="fit"), search(cb, mode="for_me", sort="fit")
    assert ra["rows"][0]["id"] == beauty and rb["rows"][0]["id"] == kitchen
    top = ra["rows"][0]
    assert 0 <= top["fit"]["score"] <= 100 and any("beauty" in x for x in top["fit"]["reasons"])
    assert ra["mode"] == "for_me" and ra["mode_info"]["cold_start"] is False
    # fit is personal in every mode, and min_fit filters on it
    sa = {r["id"]: r["fit"]["score"] for r in search(ca, sort="fit", mode="claimable")["rows"]}
    sb = {r["id"]: r["fit"]["score"] for r in search(cb, sort="fit", mode="claimable")["rows"]}
    assert sa[beauty] > sb[beauty] and sb[kitchen] > sa[kitchen]
    floor = sa[beauty] - 0.1
    assert beauty in ids(search(ca, mode="claimable", min_fit=floor)) and kitchen not in ids(search(ca, mode="claimable", min_fit=floor))


def test_followed_mode_and_advertiser_blacklist():
    users()
    with SessionLocal.begin() as db:
        advs = [Advertiser(name=n, source="meta_library", source_advertiser_id=n, platform="facebook") for n in ("rival", "other")]
        db.add_all(advs); db.flush(); rival, other = advs[0].id, advs[1].id
    r1, _ = item("Rival sunglasses", advertiser=rival)
    o1, _ = item("Other sunglasses", advertiser=other)
    ca = client("mkt-a@example.test")
    assert ids(search(ca, mode="followed")) == []
    write(ca, "POST", f"/competitors/{rival}/follow", {"interval_hours": 24})
    res = search(ca, mode="followed")
    assert ids(res) == [r1] and res["mode_info"]["followed_advertisers"] == 1
    assert o1 in ids(search(ca, q="sunglasses"))
    write(ca, "PUT", f"/workspace/filters/{other}", {"mode": "blacklist"})
    assert o1 not in ids(search(ca, q="sunglasses")) and o1 not in ids(search(ca, q="sunglasses", mode="claimable"))
    assert o1 in ids(search(client("mkt-b@example.test"), q="sunglasses"))  # A's blacklist is A's alone


def test_similar_mode_seed_and_history():
    users()
    seed, seed_ad = item("Polarized sunglasses for men")
    vi, _ = item("Kính mát phân cực nam", language="vi")
    hose, _ = item("Garden hose")
    ca = client("mkt-a@example.test")
    res = search(ca, mode="similar", similar_to=seed, sort="fit")
    assert ids(res) == [vi] and res["mode_info"]["similar_to"] == seed
    assert res["rows"][0]["fit"]["similarity"] > 0 and res["rows"][0]["fit"]["similar_reasons"]
    assert ids(search(ca, mode="similar")) == []  # no history yet
    write(ca, "PUT", f"/workspace/ads/{seed_ad}", {"saved": True})
    assert vi in ids(search(ca, mode="similar")) and hose not in ids(search(ca, mode="similar"))
    assert client("mkt-a@example.test").get("/api/search", params={"mode": "similar", "similar_to": 999999}).status_code == 404


def test_paging_reuses_fit_cache(monkeypatch):
    users()
    for i in range(30):
        item(f"Sunglasses model {i}")
    ca = client("mkt-a@example.test")
    calls = {"n": 0}
    real = R.score_product

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(R, "score_product", counting)
    p1 = search(ca, q="sunglasses", mode="claimable", sort="fit", limit=10, offset=0)
    first = calls["n"]
    p2 = search(ca, q="sunglasses", mode="claimable", sort="fit", limit=10, offset=10)
    assert first == 30 and calls["n"] == first  # second page: no rescoring
    assert p1["total"] == p2["total"] == 30 and not set(ids(p1)) & set(ids(p2))
    scores = [r["fit"]["score"] for r in p1["rows"] + p2["rows"]]
    assert scores == sorted(scores, reverse=True)


def test_access_and_validation(monkeypatch):
    users()
    item("Sunglasses")
    assert client().get("/api/search", params={"mode": "for_me"}).status_code == 401
    ca = client("mkt-a@example.test")
    assert ca.get("/api/search", params={"mode": "everything"}).status_code == 422
    monkeypatch.setenv("ADMIN_TOKEN", "svc")  # a service token has no MKT identity: no personal mode
    from fastapi.testclient import TestClient
    from app.main import app
    svc = TestClient(app, headers={"X-Admin-Token": "svc"})
    assert svc.get("/api/search", params={"mode": "for_me"}).status_code == 401
    assert svc.get("/api/search", params={"q": "sunglasses"}).status_code == 200


def test_result_cache_pages_without_rescan_and_invalidates(monkeypatch):
    from app.routers import intel
    a, b, _ = users()
    pids = [item(f"Sunglasses model {i}")[0] for i in range(12)]
    with SessionLocal.begin() as db:
        adv = Advertiser(name="x", source="meta_library", source_advertiser_id="x1", platform="facebook"); db.add(adv); db.flush()
        db.get(Ad, db.scalar(select(Ad.id).where(Ad.product_id == pids[0]))).advertiser_id = adv.id
        adv_id = adv.id
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    calls = {"n": 0}
    real = intel._search_all

    def counting(*args, **kw):
        calls["n"] += 1
        return real(*args, **kw)
    monkeypatch.setattr(intel, "_search_all", counting)
    first = search(ca, q="sunglasses", mode="claimable", sort="fit", limit=5)
    second = search(ca, q="sunglasses", mode="claimable", sort="fit", limit=5, offset=5)
    assert calls["n"] == 1 and first["total"] == second["total"] == 12 and not set(ids(first)) & set(ids(second))
    search(cb, q="sunglasses", mode="claimable", sort="fit", limit=5)
    assert calls["n"] == 2  # another MKT never reads A's cached list
    write(cb, "POST", "/allocations/claim", {"product_id": pids[3]})  # a claim must show at once, not after the TTL
    assert pids[3] not in ids(search(ca, q="sunglasses", mode="claimable", limit=50)) and calls["n"] == 3
    write(ca, "PUT", f"/workspace/filters/{adv_id}", {"mode": "blacklist"})  # A's own filter change
    assert pids[0] not in ids(search(ca, q="sunglasses", mode="claimable", limit=50)) and calls["n"] == 4
    _, seed = item("Sunglasses seed"); calls["n"] = 0
    search(ca, q="sunglasses", mode="claimable", sort="fit", limit=5)
    write(ca, "PUT", f"/workspace/ads/{seed}", {"saved": True})  # behavior changes the fit → recompute
    search(ca, q="sunglasses", mode="claimable", sort="fit", limit=5)
    assert calls["n"] == 2
