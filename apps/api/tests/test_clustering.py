"""Product clustering (services/clustering.py): auto-merge only when certain, guards against wrong merges, review queue, undo,
unique advertiser / ad counting. Throw-away SQLite (conftest); CLIP vectors are inserted directly (no model)."""
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Ad, Advertiser, Creative, Event, Product, ProductLink
from app.services import clustering as C
from app.services import naming as N
from app.services.features import ProductData, compute_features
from app.visual import embed
from app.visual.models import ProductImage
from app.cluster_models import ProductCluster, AdClusterMembership

LANDING = "https://shop.example/products/magnetic-posture-corrector"
COPY = "Fix your posture in 7 days with this magnetic corrector, free shipping nationwide"
_n = {"i": 0}


def uniq() -> int:
    _n["i"] += 1
    return _n["i"]


@pytest.fixture
def db(clean_catalog):
    with SessionLocal() as s:
        embed.INDEX.ids, embed.INDEX.mat, embed.INDEX.since, embed.INDEX.count = np.zeros(0, dtype=np.int64), np.zeros((0, embed.DIM), dtype=np.float32), None, 0
        yield s
        s.rollback()


def product(db, name=None, status="UNKNOWN", category=None, manual=False) -> Product:
    i = uniq()
    p = Product(product_code=f"CL-{i}", canonical_name=name or N.UNKNOWN_LABEL, name_status=status, category=category,
                manual_override=manual or None, in_target=True)
    db.add(p)
    db.flush()
    return p


def ad(db, p, page="pg1", landing=None, text=None, vkey=None, fp=None, **kw) -> Ad:
    i = uniq()
    a = Ad(external_id=f"cl{i}", source="t", platform="facebook", product_id=p.id, page_id=page, landing_url=landing, ad_text=text,
           variation_key=vkey, creative_fingerprint=fp, is_active=True, first_seen_at=datetime(2026, 10, 1), **kw)
    db.add(a)
    db.flush()
    return a


def picture(db, a, vec, phash=None) -> None:
    c = Creative(ad_id=a.id, product_id=a.product_id, type="image", source_url="u", status="stored", position=0)
    db.add(c)
    db.flush()
    db.add(ProductImage(source="creative", source_id=c.id, phash=phash, embedding=embed.to_bytes(vec / np.linalg.norm(vec)),
                        processing_status="done", embedded_at=datetime.utcnow(), indexed_at=datetime.utcnow()))
    db.flush()


def vec(seed: int, noise: float = 0.0, base: int | None = None) -> np.ndarray:
    v = np.random.default_rng(base if base is not None else seed).normal(size=embed.DIM)
    if noise:
        v = v + noise * np.random.default_rng(seed + 1000).normal(size=embed.DIM)
    return v


def merged(db) -> int:
    return db.scalar(select(func.count(ProductCluster.id)).where(ProductCluster.merged_into_id.is_(None)))


# ---------------------------------------------------------------- merge only when certain
def test_same_page_and_same_copy_merge_automatically(db):
    a, b = product(db), product(db)
    ad(db, a, "pgA", LANDING, COPY, "v1", "fp1")
    ad(db, b, "pgB", LANDING, COPY, "v1", "fp1")  # another advertiser, same product page and same creative copy
    stat = C.run(db)
    assert stat["merged"] == 1 and merged(db) == 1
    assert db.scalar(select(func.count(Product.id))) == 2
    assert db.scalar(select(func.count(Ad.id))) == 2
    ev = db.scalar(select(Event).where(Event.type == "PRODUCT_CLUSTERED"))
    link = db.scalar(select(ProductLink))
    assert link.status == "auto_clustered" and link.event_id == ev.id


def test_one_weak_family_waits_for_mkt(db):
    a, b = product(db), product(db)
    ad(db, a, "pgA", LANDING)
    ad(db, b, "pgB", LANDING)   # same product page only: 0.85 < AUTO_AT
    stat = C.run(db)
    assert stat.get("merged", 0) == 0 and merged(db) == 2
    assert db.scalar(select(ProductLink)).status == "pending"


def test_clip_alone_is_never_enough_to_merge(db):
    a, b = product(db), product(db)
    x, y = ad(db, a, "pgA"), ad(db, b, "pgB")
    v = vec(1)
    picture(db, x, v)
    picture(db, y, v + 0.0)   # identical picture, two advertisers: 0.6 → review queue, not a merge
    C.run(db)
    assert merged(db) == 2
    link = db.scalar(select(ProductLink))
    assert link.status == "similar" and link.relation_kind == "similar_product" and "clip" in link.signals["families"]


def test_stock_picture_hash_shared_by_many_ads_is_ignored(db):
    ps = [product(db) for _ in range(C.MAX_STOCK_ADS + 1)]
    for i, p in enumerate(ps):
        a = ad(db, p, f"pg{i}")
        picture(db, a, vec(100 + i), phash="abcdef0123456789")
    C.run(db)
    assert merged(db) == len(ps) and db.scalar(select(func.count(ProductLink.id))) == 0


# ---------------------------------------------------------------- guards against merging different products
def test_names_that_disagree_never_auto_merge(db):
    a = product(db, "Massage Gun Pro", "AUTO_CONFIRMED")
    b = product(db, "Garden Water Hose", "AUTO_CONFIRMED")
    ad(db, a, "pgA", LANDING, COPY, "v1", "fp1")
    ad(db, b, "pgB", LANDING, COPY, "v1", "fp1")   # strong evidence (page + copy) but two different named products
    C.run(db)
    assert merged(db) == 2
    link = db.scalar(select(ProductLink))
    assert link.status == "blocked" and "name_conflict" in link.blockers
    assert C.prevented([{"score": link.score, "blockers": link.blockers}])  # counted as a prevented wrong merge


def test_shop_front_tied_to_several_products_is_ambiguous(db):
    hub = product(db)   # unidentified cluster sharing copy with two different products
    x, y = product(db, "Summer Dress", "SUGGESTED"), product(db, "Led Sneakers", "SUGGESTED")
    ad(db, hub, "pgH", LANDING, COPY, "v9", "fp9")
    ad(db, x, "pgH", None, COPY, "v9", "fp9")
    ad(db, y, "pgH", None, COPY, "v9", "fp9")
    C.run(db)
    assert merged(db) == 3
    links = list(db.scalars(select(ProductLink)))
    assert len(links) == 3 and all(l.blockers for l in links)
    assert all("ambiguous" in l.blockers for l in links if hub.id in (l.a_id, l.b_id))


def test_rejected_pair_is_a_permanent_cannot_link(db):
    a, b = product(db), product(db)
    ad(db, a, "pgA", LANDING, COPY, "v1", "fp1")
    ad(db, b, "pgB", LANDING, COPY, "v1", "fp1")
    C.record_cannot_link(db, a.id, b.id, "mkt")
    db.flush()
    stat = C.run(db)
    assert stat.get("merged", 0) == 0 and merged(db) == 2
    assert db.scalar(select(ProductLink)).status == "rejected"   # blocked rows never overwrite the MKT decision


def test_big_clusters_wait_for_mkt(db):
    a, b = product(db, "Posture Corrector", "AUTO_CONFIRMED"), product(db, "Back Support Brace", "AUTO_CONFIRMED")
    for i in range(C.MAX_MERGED_ADS):
        ad(db, a, f"pg{i}", f"https://s{i}.example/products/posture-{i}", None)
    ad(db, a, "pgA", LANDING, COPY, "v1", "fp1")
    ad(db, b, "pgB", LANDING, COPY, "v1", "fp1")
    C.run(db)
    assert merged(db) == 2


# ---------------------------------------------------------------- undo + MKT decisions
def test_undo_restores_both_products_and_never_remerges(db):
    a, b = product(db, "Posture Corrector", "SUGGESTED"), product(db)
    ad(db, a, "pgA", LANDING, COPY, "v1", "fp1")
    ad(db, b, "pgB", LANDING, COPY, "v1", "fp1")
    C.run(db)
    assert merged(db) == 1
    ev = db.scalar(select(Event).where(Event.type == "PRODUCT_CLUSTERED"))
    C.undo_event(db, ev.product_id, ev.id, "mkt")
    db.flush()
    assert merged(db) == 2   # the unnamed one is back
    assert db.scalar(select(ProductLink).where(ProductLink.status == "rejected")) is not None
    C.run(db)
    assert merged(db) == 2   # the undone pair is not proposed or merged again


def test_accept_merges_the_better_named_product(db):
    a, b = product(db), product(db, "Magnetic Posture Corrector", "AUTO_CONFIRMED")
    ad(db, a, "pgA", LANDING)
    ad(db, b, "pgB", LANDING)
    C.run(db)
    link = db.scalar(select(ProductLink))
    assert link.status == "pending"
    tgt, ev = C.accept(db, link, "mkt")
    assert tgt == b.id and merged(db) == 1 and link.status == "accepted" and link.event_id == ev


# ---------------------------------------------------------------- names
def test_name_is_proposed_only_from_evidence():
    mk = lambda i, page, url=None, title=None: Ad(id=i, page_id=page, landing_url=url, title=title, ad_text=title)
    two_pages = [mk(1, "p1", "https://x.example/products/magnetic-posture-corrector"), mk(2, "p2", "https://y.example/products/magnetic-posture-corrector")]
    nm = C.propose_name(two_pages, {})
    assert nm["status"] == "AUTO_CONFIRMED" and nm["pages"] == 2 and "posture" in nm["name"].lower()
    assert C.propose_name([mk(3, "p3", "https://shop.example/", None)], {}) is None   # no evidence → no name, never a guess


def test_grouping_keeps_original_unknown_names(db):
    a, b = product(db), product(db)
    ad(db, a, "pgA", "https://x.example/products/magnetic-posture-corrector", COPY, "v1", "fp1")
    ad(db, b, "pgB", "https://x.example/products/magnetic-posture-corrector", COPY, "v1", "fp1")
    C.run(db)
    assert merged(db) == 1
    assert all(p.name_status == "UNKNOWN" for p in db.scalars(select(Product)))


# ---------------------------------------------------------------- mixed clusters already in the data
def test_detect_impure_flags_untied_name_groups(db):
    p = product(db, "Mixed", "SUGGESTED")
    ad(db, p, "pgA", "https://a.example/products/magnetic-posture-corrector")
    ad(db, p, "pgB", "https://b.example/products/stainless-steel-water-bottle")
    rows = C.detect_impure(db)
    assert len(rows) == 1 and rows[0]["product_id"] == p.id and len(rows[0]["groups"]) == 2
    ok = product(db, "Fine", "SUGGESTED")
    ad(db, ok, "pgC", "https://c.example/products/magnetic-posture-corrector")
    ad(db, ok, "pgD", "https://c.example/products/magnetic-posture-corrector")
    assert {r["product_id"] for r in C.detect_impure(db)} == {p.id}


# ---------------------------------------------------------------- ads ≠ advertisers
def test_unique_advertisers_and_unique_ads_do_not_double_count(db):
    p = product(db, "Posture Corrector", "SUGGESTED")
    advs = [Advertiser(name=n) for n in ("Shop X", "Shop X (SA)", "Shop Y")]   # one Facebook page filed under two advertiser rows
    db.add_all(advs)
    db.flush()
    a1 = ad(db, p, "pageX", None, COPY, "vk", None, advertiser_id=advs[0].id, country="PH")
    a2 = ad(db, p, "pageX", None, COPY, "vk", None, advertiser_id=advs[1].id, country="SA")
    a3 = ad(db, p, "pageY", None, "Another creative entirely for the same product", "vk2", None, advertiser_id=advs[2].id, country="PH")
    f = compute_features(ProductData(product=p, ads=[a1, a2, a3]))
    assert f["active_ads"] == 3 and f["unique_ads"] == 2 and f["advertiser_count"] == 2


# ---------------------------------------------------------------- the MKT API
def test_review_queue_accept_undo_through_the_api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    a, b = product(db), product(db, "Magnetic Posture Corrector", "AUTO_CONFIRMED")
    ad(db, a, "pgA", LANDING)
    ad(db, b, "pgB", LANDING)
    C.run(db)
    db.commit()
    monkeypatch.setenv("ADMIN_TOKEN", "adm")
    c = TestClient(app, headers={"X-Admin-Token": "adm"})
    rows = c.get("/api/clusters/candidates").json()["rows"]
    assert len(rows) == 1 and rows[0]["keep_id"] == b.id and "landing" in rows[0]["signals"]["families"]
    resp = c.post(f"/api/clusters/candidates/{rows[0]['id']}/accept", json={"user": "mkt"})
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["ok"] and r["target_id"] == b.id
    done = c.get("/api/clusters/candidates", params={"status": "accepted"}).json()["rows"]
    assert len(done) == 1 and {done[0]["a"]["id"], done[0]["b"]["id"]} == {a.id, b.id}
    assert c.post(f"/api/clusters/events/{r['event_id']}/undo", json={"user": "mkt"}).status_code == 200
    assert c.get("/api/clusters/candidates", params={"status": "accepted"}).json()["rows"] == []
    assert c.get("/api/clusters/stats").json()["products"] == 2
    # "not the same" is permanent
    C.run(db)
    db.commit()
    assert c.get("/api/clusters/candidates").json()["rows"] == []
