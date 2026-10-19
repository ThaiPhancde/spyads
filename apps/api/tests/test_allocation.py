"""Phase 2 exclusive product allocation: atomic claim races, TTL, Saved/Favorite/Tested retention, cluster confidence,
access control, owner self-release (no ADMIN, no reassign), and Follow staying independent. Disposable DB only (conftest)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import allocation as A
from app.cluster_models import AdClusterMembership, ProductCluster
from app.db import SessionLocal
from app.main import app  # noqa: F401 — registers every table before clean_catalog recreates them
from app.models import Ad, Advertiser, Product
from app.services import cluster_membership as CM
from test_workspace import client, create_user, write


@pytest.fixture(autouse=True)
def fresh(clean_catalog, monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.delenv("ALLOCATION_RESERVE_HOURS", raising=False)
    monkeypatch.delenv("ALLOCATION_GROUP_MIN_CONFIDENCE", raising=False)


def seed(n=1, advertiser=None):
    """n single-product clusters, one ad each. Returns [(product_id, ad_id)]."""
    out = []
    with SessionLocal.begin() as db:
        for _ in range(n):
            i = uuid.uuid4().hex[:10]
            p = Product(product_code=f"AL-{i}", canonical_name=f"item {i}", name_status="AUTO_CONFIRMED", in_target=True)
            db.add(p); db.flush()
            a = Ad(external_id=f"al-{i}", source="meta_library", platform="facebook", product_id=p.id, advertiser_id=advertiser,
                   title="item", ad_text="item", is_active=True, first_seen_at=datetime(2026, 10, 1))
            db.add(a); db.flush()
            out.append((p.id, a.id))
        CM.sync(db)
    return out


def group(confidence, method="automatic"):
    """Two products grouped by Phase 1 with the given merge confidence. Returns (root_pid, member_pid, member_ad)."""
    (p, _), (q, qa) = seed(2)
    with SessionLocal.begin() as db:
        db.get(ProductCluster, q).merged_into_id = p
        m = db.get(AdClusterMembership, qa)
        m.cluster_id, m.confidence, m.method = p, confidence, method
    return p, q, qa


def users():
    """Three ordinary MKT accounts — the only account type since 2026-10-19 (no ADMIN)."""
    return create_user("mkt-a@example.test"), create_user("mkt-b@example.test"), create_user("mkt-c@example.test")


def claim_in_thread(uid, pid, barrier):
    with SessionLocal() as db:
        barrier.wait()
        try:
            A.claim(db, uid, pid)
            db.commit()
            return "ok"
        except HTTPException as e:
            db.rollback()
            return e.status_code


# ---------- atomic claim / race ----------

def test_claim_reserves_with_ttl_and_second_mkt_is_refused():
    a, b, _ = users()
    (pid, _), = seed()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    r = write(ca, "POST", "/allocations/claim", {"product_id": pid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "reserved" and body["products"] == [pid] and body["user_id"] == a
    until = datetime.fromisoformat(body["reserved_until"])
    assert timedelta(hours=47.9) < until - datetime.utcnow() <= timedelta(hours=48)
    assert write(cb, "POST", "/allocations/claim", {"product_id": pid}).status_code == 409
    again = write(ca, "POST", "/allocations/claim", {"product_id": pid})  # idempotent for the owner
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_concurrent_claims_on_same_product_grant_exactly_one_owner():
    uids = [create_user(f"race{i}@example.test") for i in range(6)]
    (pid, _), = seed()
    barrier = threading.Barrier(len(uids))
    with ThreadPoolExecutor(max_workers=len(uids)) as pool:
        results = list(pool.map(lambda u: claim_in_thread(u, pid, barrier), uids))
    assert results.count("ok") == 1 and results.count(409) == len(uids) - 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(A.AllocationLock)) == 1
        assert db.scalar(select(func.count()).select_from(A.ProductAllocation)) == 1


def test_concurrent_claims_on_different_products_of_one_confident_cluster_grant_one_owner():
    a, b = create_user("x@example.test"), create_user("y@example.test")
    p, q, _ = group(0.97)
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: claim_in_thread(*args, barrier), [(a, p), (b, q)]))
    assert sorted(results, key=str) == sorted(["ok", 409], key=str)
    with SessionLocal() as db:
        owners = set(db.scalars(select(A.ProductAllocation.user_id).join(A.AllocationLock,
                                A.AllocationLock.allocation_id == A.ProductAllocation.id)))
        assert len(owners) == 1 and db.scalar(select(func.count()).select_from(A.AllocationLock)) == 2


# ---------- cluster confidence ----------

def test_confident_cluster_is_locked_as_a_whole():
    a, b, _ = users()
    p, q, _ = group(0.95)
    r = write(client("mkt-a@example.test"), "POST", "/allocations/claim", {"product_id": q}).json()
    assert r["scope"] == "cluster" and r["cluster_id"] == p and r["products"] == sorted([p, q])
    cb = client("mkt-b@example.test")
    assert write(cb, "POST", "/allocations/claim", {"product_id": p}).status_code == 409
    s = cb.get(f"/api/allocations/status/{p}").json()
    assert s["status"] == "reserved" and not s["mine"] and s["allocation_id"] is None and "owner_id" not in s


@pytest.mark.parametrize("confidence", [0.7, None])
def test_low_confidence_cluster_only_locks_the_requested_product(confidence):
    users()
    p, q, _ = group(confidence)
    ra = write(client("mkt-a@example.test"), "POST", "/allocations/claim", {"product_id": p}).json()
    assert ra["scope"] == "product" and ra["products"] == [p]
    cb = client("mkt-b@example.test")
    assert cb.get(f"/api/allocations/status/{q}").json()["status"] == "available"
    rb = write(cb, "POST", "/allocations/claim", {"product_id": q})
    assert rb.status_code == 200 and rb.json()["products"] == [q]


def test_manual_merge_counts_as_confident_and_threshold_is_configurable(monkeypatch):
    users()
    p, q, _ = group(0.5, method="manual")
    assert write(client("mkt-a@example.test"), "POST", "/allocations/claim", {"product_id": p}).json()["scope"] == "cluster"
    monkeypatch.setenv("ALLOCATION_GROUP_MIN_CONFIDENCE", "0.6")
    r, s, _ = group(0.65)
    with SessionLocal() as db:
        assert A.scope_of(db, r)[2] == "cluster"


# ---------- TTL ----------

def test_reservation_expires_after_ttl_and_frees_the_product(monkeypatch):
    a, b, _ = users()
    (pid, _), = seed()
    monkeypatch.setenv("ALLOCATION_RESERVE_HOURS", "1")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    r = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()
    assert datetime.fromisoformat(r["reserved_until"]) - datetime.utcnow() <= timedelta(hours=1)
    with SessionLocal.begin() as db:
        db.get(A.ProductAllocation, r["id"]).reserved_until = datetime.utcnow() - timedelta(seconds=1)
    assert cb.get(f"/api/allocations/status/{pid}").json()["status"] == "available"  # reads never write
    assert write(ca, "PATCH", f"/allocations/{r['id']}/status", {"status": "claimed"}).status_code == 409
    rb = write(cb, "POST", "/allocations/claim", {"product_id": pid})
    assert rb.status_code == 200 and rb.json()["user_id"] == b
    old = ca.get(f"/api/allocations/{r['id']}").json()
    assert old["status"] == "released" and old["release_reason"] == "expired"
    assert [x["action"] for x in old["audit"]] == ["claim", "expire"] and old["audit"][1]["actor_id"] is None


@pytest.mark.parametrize("field", ["saved", "favorite"])
def test_save_and_favorite_never_change_ownership_or_its_ttl(field):
    users()
    (pid, ad_id), = seed()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    r = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()
    assert write(ca, "PUT", f"/workspace/ads/{ad_id}", {field: True}).status_code == 200
    with SessionLocal() as db:
        a = db.get(A.ProductAllocation, r["id"])
        assert a.status == "reserved" and a.reserved_until is not None  # soft signal only: TTL still runs
        A.expire(db, datetime.utcnow() + timedelta(days=3)); db.commit()
    assert write(cb, "POST", "/allocations/claim", {"product_id": pid}).status_code == 200  # expired → free


def test_save_without_claim_creates_no_ownership_and_other_mkts_can_save_too():
    users()
    (pid, ad_id), = seed()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    for c in (ca, cb):
        assert write(c, "PUT", f"/workspace/ads/{ad_id}", {"saved": True, "favorite": True}).status_code == 200
    assert ca.get(f"/api/allocations/status/{pid}").json()["status"] == "available"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(A.ProductAllocation)) == 0


def test_marking_tested_on_an_owned_product_moves_it_to_testing():
    users()
    (pid, ad_id), = seed()
    ca = client("mkt-a@example.test")
    r = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()
    write(ca, "PUT", f"/workspace/ads/{ad_id}", {"tested": True})
    with SessionLocal() as db:
        a = db.get(A.ProductAllocation, r["id"])
        assert a.status == "testing" and a.reserved_until is None


def test_interaction_and_search_never_create_ownership():
    users()
    (pid, ad_id), = seed()
    ca = client("mkt-a@example.test")
    assert write(ca, "PUT", f"/workspace/ads/{ad_id}", {"saved": True, "tested": True}).status_code == 200
    for path in ("/api/ads?limit=5", "/api/products?limit=5", f"/api/products/{pid}"):
        assert ca.get(path).status_code in (200, 404)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(A.ProductAllocation)) == 0


# ---------- lifecycle, results, access ----------

def test_status_flow_results_and_release():
    users()
    (pid, _), = seed()
    ca = client("mkt-a@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    assert write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "tested"}).status_code == 409  # reserved → tested
    assert write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"}).json()["status"] == "testing"
    r = write(ca, "POST", f"/allocations/{aid}/results", {"outcome": "winner", "market": "PH", "spend": 120.5, "orders": 14})
    assert r.status_code == 200 and r.json()["status"] == "tested"
    assert r.json()["results"][0]["outcome"] == "winner" and r.json()["results"][0]["market"] == "PH"
    assert ca.get(f"/api/allocations/status/{pid}").json()["status"] == "tested"
    assert write(ca, "POST", f"/allocations/{aid}/release", {}).json()["status"] == "released"
    detail = ca.get(f"/api/allocations/{aid}").json()
    assert detail["products"] == [] and len(detail["results"]) == 1  # results survive release
    assert [x["action"] for x in detail["audit"]] == ["claim", "status", "result", "release"]
    assert write(ca, "POST", f"/allocations/{aid}/release", {}).status_code == 409
    assert ca.get(f"/api/allocations/status/{pid}").json()["status"] == "available"


def test_claim_works_before_cluster_sync():
    a, _, _ = users()
    with SessionLocal.begin() as db:
        p = Product(product_code="AL-nosync", canonical_name="fresh", name_status="UNKNOWN")
        db.add(p); db.flush(); pid = p.id
    r = write(client("mkt-a@example.test"), "POST", "/allocations/claim", {"product_id": pid})
    assert r.status_code == 200 and r.json()["cluster_id"] == pid and r.json()["products"] == [pid]


def test_access_control():
    users()
    (pid, _), = seed()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    anon = client()
    assert anon.get(f"/api/allocations/status/{pid}").status_code == 401
    assert write(anon, "POST", "/allocations/claim", {"product_id": pid}).status_code == 401
    assert cb.get(f"/api/allocations/{aid}").status_code == 404
    assert write(cb, "POST", f"/allocations/{aid}/release", {}).status_code == 404
    assert write(cb, "PATCH", f"/allocations/{aid}/status", {"status": "claimed"}).status_code == 404
    assert write(cb, "POST", f"/allocations/{aid}/results", {"outcome": "loser"}).status_code == 404
    assert write(cb, "POST", "/admin/allocations/reassign", {"product_id": pid, "user_id": 1}).status_code == 403  # gone + system area
    assert cb.get("/api/allocations", params={"user_id": 1}).json()["total"] == 0  # no widening by parameter
    assert cb.get("/api/allocations").json()["total"] == 0 and ca.get("/api/allocations").json()["total"] == 1
    no_csrf = ca.post("/api/allocations/claim", json={"product_id": pid})
    assert no_csrf.status_code == 403
    assert write(ca, "POST", "/allocations/claim", {"product_id": 999999}).status_code == 404


def test_owner_releases_and_history_survives_without_any_reassign():
    a, b, c = users()
    (pid, _), = seed()
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": pid}).json()["id"]
    write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"})
    write(ca, "POST", f"/allocations/{aid}/results", {"outcome": "loser", "spend": 50})
    st = cb.get(f"/api/allocations/status/{pid}").json()
    assert st["status"] == "tested" and st["allocation_id"] is None and "owner_id" not in st  # state, never who
    taken = write(cb, "POST", "/allocations/claim", {"product_id": pid})
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "taken"
    assert write(cb, "POST", f"/allocations/{aid}/release", {}).status_code == 404  # only the owner releases
    r = write(ca, "POST", f"/allocations/{aid}/release", {"reason": "done"})
    assert r.status_code == 200 and r.json()["release_reason"] == "done"
    detail = ca.get(f"/api/allocations/{aid}").json()
    assert detail["results"][0]["outcome"] == "loser" and detail["results"][0]["spend"] == 50  # business result kept
    assert [x["action"] for x in detail["audit"]][-1] == "release"
    nb = write(cb, "POST", "/allocations/claim", {"product_id": pid})
    assert nb.status_code == 200 and nb.json()["user_id"] == b


def test_conflict_names_the_state_and_suggests_open_look_alikes():
    users()
    with SessionLocal.begin() as db:
        ids = []
        for name in ("Polarized sunglasses men", "Polarized sunglasses women", "Polarized sunglasses kids"):
            p = Product(product_code=f"AL-{name.split()[-1]}", canonical_name=name, category="accessories", name_status="AUTO_CONFIRMED", in_target=True)
            db.add(p); db.flush()
            db.add(Ad(external_id=f"al-{p.id}", source="t", platform="facebook", product_id=p.id, title=name,
                      search_text=name.lower(), is_active=True)); db.flush()
            ids.append(p.id)
        CM.sync(db)
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    aid = write(ca, "POST", "/allocations/claim", {"product_id": ids[0]}).json()["id"]
    write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"})
    r = write(cb, "POST", "/allocations/claim", {"product_id": ids[0]})
    d = r.json()["detail"]
    assert r.status_code == 409 and d["state"] == "testing" and "đang được MKT khác test" in d["message"]
    assert {x["product_id"] for x in d["similar"]} >= {ids[1], ids[2]} and "mkt-a" not in str(d)


def test_follow_and_advertiser_research_stay_independent_of_ownership():
    users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="777", platform="facebook", country="PH")
        db.add(adv); db.flush(); adv_id = adv.id
    (pid, _), = seed(advertiser=adv_id)
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    assert write(ca, "POST", "/allocations/claim", {"product_id": pid}).status_code == 200
    assert write(cb, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24}).status_code == 200
    assert cb.get(f"/api/competitors/{adv_id}").status_code == 200
    assert write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24}).status_code == 200
