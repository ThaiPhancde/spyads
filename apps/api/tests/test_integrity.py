"""Audit 2026-10-26 C1-C3 (06_critic.md), ported from the critic repros and flipped to the fixed behaviour: the discovery tab
cache is per user, clustering never unites two clusters held by two different MKT, and one MKT has one vote per product.
Disposable DB only (conftest)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import allocation as A
from app.cluster_models import AdClusterMembership, ProductCluster
from app.db import SessionLocal
from app.models import Product, ProductLink, Vote
from app.routers import intel
from app.services import clustering as C
from app.workspace.migrate import one_vote
from test_allocation import seed, users
from test_clustering import COPY, LANDING, ad, merged, product
from test_workspace import client, write


@pytest.fixture(autouse=True)
def fresh(clean_catalog, monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    intel._cache.clear()


def test_discovery_favorites_cache_is_per_user():
    a, b, _ = users()
    (p, _), = seed(1)
    with SessionLocal.begin() as db:
        db.add(Vote(user_id=a, product_id=p, user="a", decision="LOVE"))
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    ids = lambda c: [r["id"] for r in c.get("/api/discovery/tab/favorites").json()["rows"]]
    assert ids(ca) == [p]
    assert ids(cb) == []  # B never voted: A's favorites must not come back from the shared cache


def test_merge_never_unites_two_owners_and_claim_returns_my_own():
    a, b, _ = users()
    with SessionLocal() as db:  # two products auto-merge would unite (same landing, copy, creative)
        pa, pb = product(db, "Posture Corrector", "SUGGESTED"), product(db)
        ad(db, pa, "pgA", LANDING, COPY, "v1", "fp1")
        ad(db, pb, "pgB", LANDING, COPY, "v1", "fp1")
        pa, pb = pa.id, pb.id
        A.claim(db, a, pa)
        A.claim(db, b, pb)
        db.commit()
        C.run(db)
        db.commit()
        link = db.scalar(select(ProductLink))
        assert merged(db) == 2 and link.status == "pending" and "allocation_conflict" in link.blockers
    ca = client("mkt-a@example.test")
    r = write(ca, "POST", f"/clusters/candidates/{link.id}/accept")
    assert r.status_code == 409 and "hai MKT" in r.json()["detail"]

    # a cluster merged before the guard existed: each owner keeps working on their own product instead of a 409
    with SessionLocal.begin() as db:
        db.get(ProductCluster, pb).merged_into_id = pa
        for m in db.scalars(select(AdClusterMembership).where(AdClusterMembership.source_product_id == pb)):
            m.cluster_id, m.confidence, m.method = pa, 0.99, "automatic"
    cb = client("mkt-b@example.test")
    assert write(ca, "POST", f"/library/products/{pa}/start-test").status_code == 200
    assert write(cb, "POST", f"/library/products/{pb}/start-test").status_code == 200
    with SessionLocal() as db:
        assert A.claim(db, a, pb).user_id == a  # A asking for B's merged-in product gets A's own allocation back


def test_legacy_merge_refuses_two_owners():
    a, b, _ = users()
    with SessionLocal() as db:
        pa, pb = product(db, "Neck Fan", "SUGGESTED"), product(db)
        pa, pb = pa.id, pb.id
        A.claim(db, a, pa)
        A.claim(db, b, pb)
        db.commit()
    r = write(client("mkt-a@example.test"), "POST", "/products/merge", {"source_id": pb, "target_id": pa})
    assert r.status_code == 409 and "hai MKT" in r.json()["detail"]


def test_one_vote_per_product_and_user():
    a, b, _ = users()
    (p, _), = seed(1)
    ca = client("mkt-a@example.test")
    for i in range(5):  # a different `market` each time used to add a row each time
        assert write(ca, "POST", f"/products/{p}/vote", {"user": "x", "decision": "LOVE", "market": f"M{i}"}).status_code == 200
    with SessionLocal.begin() as db:
        rows = db.scalars(select(Vote).where(Vote.product_id == p, Vote.user_id == a)).all()
        assert [v.market for v in rows] == ["M4"]
        # a stale older duplicate (what live PG has) and one vote from B
        db.add(Vote(user_id=a, product_id=p, user="a", decision="SKIP", created_at=datetime.utcnow() - timedelta(days=1)))
        db.add(Vote(user_id=b, product_id=p, user="b", decision="LOVE"))
        for name in ("loadtest", "e2e"):  # pre-account rows (user_id NULL): one voter, latest kept
            db.add(Vote(user_id=None, product_id=p, user=name, decision="WATCH", created_at=datetime(2026, 10, 7)))
    with SessionLocal.begin() as db:
        from app.services.engine import ScoreContext, rescore_products
        ScoreContext.invalidate()
        rescore_products(db, [p])
        pot = db.get(Product, p).potential
        assert pot["mkt_appeal_source"] == "3 votes" and pot["vector"]["mkt_appeal"] == 85  # mean per user (100, 100, 55)
        one_vote(db)
        one_vote(db)  # idempotent
    with SessionLocal() as db:
        left = sorted((v.user_id or 0, v.user, v.decision) for v in db.scalars(select(Vote).where(Vote.product_id == p)))
        assert left == [(0, "e2e", "WATCH"), (a, str(a), "LOVE"), (b, "b", "LOVE")]
