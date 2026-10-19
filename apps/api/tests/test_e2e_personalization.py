"""Phase 9 end-to-end: several MKTs using the whole personalization stack at once (dashboard KPIs, concurrent claims,
personal ranking, Follow, Search, Library, logout/login) plus a cross-access privacy matrix. Disposable DB only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading

import pytest
from sqlalchemy import func, select

from app import allocation as A
from app.cluster_models import AdClusterMembership, ProductCluster
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_personal_search import item, reset  # noqa: F401 — reset: caches + no AI
from test_workspace import client, create_user, write


def dash(c):
    r = c.get("/api/library/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


def keys_of(obj):
    if isinstance(obj, dict):
        return set(obj) | set().union(*(keys_of(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(keys_of(v) for v in obj)) if obj else set()
    return set()


def merge(root, member):
    with SessionLocal.begin() as db:
        db.get(ProductCluster, member).merged_into_id = root
        for m in db.scalars(select(AdClusterMembership).where(AdClusterMembership.source_product_id == member)):
            m.cluster_id, m.confidence, m.method = root, 0.99, "automatic"


def test_dashboard_kpis_are_real_and_separate_ads_from_clusters():
    users()
    p1, ad1 = item("Sunglasses classic")
    p2, ad2 = item("Sunglasses classic copy")
    merge(p1, p2)  # one Product Cluster, two ads
    with SessionLocal.begin() as db:
        extra = Ad(external_id="x-2", source="meta_library", platform="facebook", product_id=p1, title="second ad",
                   is_active=True, in_target=True, country="PH")
        db.add(extra); db.flush(); ad3 = extra.id
        db.get(Product, p1).recommendation = "TEST"
    ca = client("mkt-a@example.test")
    assert dash(ca)["kpis"]["products"]["saved"] == 0
    for ad in (ad1, ad2, ad3):
        write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True})
    d = dash(ca)
    assert d["kpis"]["ads"]["saved"] == 3 and d["kpis"]["products"]["saved"] == 1  # 3 ads, 1 Product Cluster
    assert d["lists"]["saved"][0]["id"] == p1
    assert not {k for k in keys_of(d) if "win" in k.lower() and k != "winner"} and "rare" not in str(keys_of(d)).lower()
    assert set(d["kpis"]["products"]) >= {"recommended", "test_candidates", "saved", "favorite", "claimed", "testing",
                                          "test_success", "test_fail", "test_inconclusive"}


def test_three_mkts_claim_the_same_product_concurrently_over_http():
    users()
    pid, _ = item("Hot product")
    clients = [client(e) for e in ("mkt-a@example.test", "mkt-b@example.test", "mkt-c@example.test")]
    barrier = threading.Barrier(3)

    def claim(c):
        barrier.wait()
        return write(c, "POST", "/allocations/claim", {"product_id": pid}).status_code
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(claim, clients))
    assert sorted(codes) == [200, 409, 409]
    claimed = [dash(c)["kpis"]["products"]["claimed"] for c in clients]
    assert sorted(claimed) == [0, 0, 1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(A.AllocationLock)) == 1


def test_full_day_of_two_mkts():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="r1", platform="facebook", country="PH")
        db.add(adv); db.flush(); rival = adv.id
    beauty_seed, beauty_ad = item("Vitamin C beauty serum", category="beauty")
    beauty2, _ = item("Beauty serum vitamin C brightening", category="beauty")
    kitchen_seed, kitchen_ad = item("Kitchen knife", category="kitchen")
    kitchen2, _ = item("Kitchen pan", category="kitchen")
    rival_p, rival_ad = item("Rival gadget", category="gadgets", advertiser=rival)
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    # personalization diverges per MKT
    write(ca, "PUT", f"/workspace/ads/{beauty_ad}", {"saved": True, "favorite": True})
    write(cb, "PUT", f"/workspace/ads/{kitchen_ad}", {"saved": True})
    ra, rb = dash(ca), dash(cb)
    assert ra["recommended"][0]["id"] == beauty2 and rb["recommended"][0]["id"] == kitchen2
    assert ra["top_categories"][0]["key"] == "beauty" and rb["top_categories"][0]["key"] == "kitchen"
    assert beauty2 in [p["id"] for p in ra["similar"]]
    # Follow → new ads from competitors (real ad rows, first_seen within 24 h)
    write(ca, "POST", f"/competitors/{rival}/follow", {"interval_hours": 24})
    da = dash(ca)
    assert da["kpis"]["followed_advertisers"] == 1 and da["kpis"]["ads"]["new_from_followed_7d"] == 1
    assert [a["id"] for a in da["new_ads"]] == [rival_ad] and da["followed"][0]["name"] == "Rival"
    assert dash(cb)["new_ads"] == []
    # Search claimable → Library start test → result
    found = ca.get("/api/search", params={"mode": "claimable", "q": "beauty", "limit": 50}).json()
    assert beauty2 in [r["id"] for r in found["rows"]]
    t = write(ca, "POST", f"/library/products/{beauty2}/start-test").json()
    assert beauty2 not in [r["id"] for r in cb.get("/api/search", params={"mode": "claimable", "q": "beauty"}).json()["rows"]]
    assert dash(ca)["kpis"]["products"]["testing"] == 1
    write(ca, "POST", f"/allocations/{t['allocation_id']}/results", {"outcome": "loser", "market": "PH"})
    da = dash(ca)
    assert da["kpis"]["products"]["test_fail"] == 1 and da["kpis"]["products"]["testing"] == 0
    assert da["lists"]["test_fail"][0]["result"]["outcome"] == "loser"
    assert dash(cb)["kpis"]["products"]["test_fail"] == 0
    # logout / login: same numbers
    before = dash(ca)
    write(ca, "POST", "/auth/logout")
    assert ca.get("/api/library/dashboard").status_code == 401
    after = dash(client("mkt-a@example.test"))
    assert after["kpis"] == before["kpis"] and after["lists"] == before["lists"]


def test_cross_access_matrix():
    a, b, c = users()
    pid, ad = item("Private product")
    ca, cb, cx, anon = client("mkt-a@example.test"), client("mkt-b@example.test"), client("mkt-c@example.test"), client()
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    write(ca, "PUT", f"/workspace/ads/{ad}", {"saved": True, "note": "A only"})
    write(ca, "PUT", "/preferences", {"priority_markets": ["SA"]})
    # B can't read or act on A's things
    assert cb.get(f"/api/allocations/{aid}").status_code == 404
    for path, body in ((f"/allocations/{aid}/release", {}), (f"/allocations/{aid}/results", {"outcome": "winner"})):
        assert write(cb, "POST", path, body).status_code == 404
    assert write(cb, "PATCH", f"/allocations/{aid}/status", {"status": "testing"}).status_code == 404
    assert cb.get("/api/preferences", params={"user_id": a}).status_code == 404
    assert cb.get("/api/recommendations", params={"user_id": a}).status_code == 404
    assert cb.get("/api/behavior/events", params={"user_id": a}).json()["total"] == 0
    assert write(cb, "POST", "/admin/allocations/reassign", {"product_id": pid, "user_id": b}).status_code == 403
    db_ = dash(cb)
    assert db_["kpis"]["products"]["claimed"] == 0 and db_["kpis"]["ads"]["saved"] == 0 and "A only" not in str(db_)
    assert "A only" not in str(cb.get("/api/library").json()) and "A only" not in str(cx.get("/api/library").json())
    assert cb.get("/api/workspace/ads").json()["total"] == 0
    # B's view of the product: taken, nothing else
    s = cb.get(f"/api/allocations/status/{pid}").json()
    assert s["status"] != "available" and not s["mine"] and s["allocation_id"] is None and "owner_id" not in s
    # anonymous: every personal endpoint refuses
    for path in ("/api/library", "/api/library/dashboard", "/api/preferences", "/api/recommendations", "/api/behavior/events",
                 "/api/similar/for-me", "/api/similar/followed", f"/api/allocations/{aid}", "/api/workspace/ads", "/api/allocations"):
        assert anon.get(path).status_code == 401, path
    assert anon.get("/api/search", params={"mode": "for_me"}).status_code == 401


@pytest.mark.skipif(__import__("app.db", fromlist=["engine"]).engine.dialect.name != "postgresql", reason="PostgreSQL indexes")
def test_personalization_indexes_are_idempotent_and_used():
    from sqlalchemy import text
    from app.db import engine
    from app.personalization_migrate import migrate
    assert migrate() and migrate()  # twice: IF NOT EXISTS
    with engine.connect() as c:
        names = set(c.execute(text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_product%_opportunity' "
                                   "OR indexname = 'ix_product_images_creative_done'")).scalars())
        assert names == {"ix_products_category_opportunity", "ix_products_target_opportunity", "ix_product_images_creative_done"}
        c.execute(text("SET enable_seqscan = off"))  # tiny test table: make the planner show it *can* use the index
        plan = "\n".join(c.execute(text("EXPLAIN SELECT id FROM products WHERE category = 'beauty' "
                                        "ORDER BY opportunity_score DESC LIMIT 10")).scalars())
        assert "ix_products_category_opportunity" in plan
