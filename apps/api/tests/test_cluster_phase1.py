"""Phase 1 contract: additive migration, preserved originals, identity guards, review and no model calls."""
from datetime import datetime, date
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.cluster_migrate import migrate
from app.cluster_models import AdClusterMembership, DuplicateAdLink, ProductCluster
from app.db import SessionLocal, engine
from app.models import Ad, Event, LifecycleEvent, Product, ProductDailySnapshot, ProductLink
from app.services import clustering as C, cluster_membership as CM
from app.visual import embed
from test_clustering import ad, product, picture, vec


@pytest.fixture
def db(clean_catalog, monkeypatch):
    embed.INDEX.ids, embed.INDEX.mat, embed.INDEX.since, embed.INDEX.count = np.zeros(0, dtype=np.int64), np.zeros((0, 512), dtype=np.float32), None, 0
    embed._pgv.update(ok=None, at=0)
    def forbidden(*args, **kwargs):
        raise AssertionError("No inference/provider calls during clustering or review")
    monkeypatch.setattr(embed, "embed_image", forbidden)
    monkeypatch.setattr(embed, "embed_batch", forbidden)
    from app.services import llm
    monkeypatch.setattr(llm, "complete_json", forbidden)
    with SessionLocal() as s:
        yield s
        s.rollback()


def roots(db):
    return list(db.scalars(select(ProductCluster.id).where(ProductCluster.merged_into_id.is_(None))))


def originals(db):
    tables = ("ads", "products", "creatives", "product_daily_snapshots", "lifecycle_events")
    return {t: [dict(r._mapping) for r in db.execute(text(f'SELECT * FROM "{t}" ORDER BY id'))] for t in tables}


def same_product(db, names=("Portable water bottle", "Portable Water Bottle"), pages=("shop-a", "shop-b")):
    a, b = [product(db, n, "AUTO_CONFIRMED") for n in names]
    x, y = ad(db, a, pages[0]), ad(db, b, pages[1])
    picture(db, x, vec(22), "1234567890abcdef")
    picture(db, y, vec(22), "1234567890abcdef")
    return a, b, x, y


def test_same_product_different_shops_groups_without_touching_original_rows(db):
    a, b, x, y = same_product(db)
    db.add(LifecycleEvent(product_id=a.id, from_status="DISCOVERED", to_status="WATCH"))
    db.add(ProductDailySnapshot(product_id=a.id, date=date(2026, 10, 1)))
    db.flush()
    before = originals(db)
    result = C.run(db)
    assert result["merged"] == 1 and len(roots(db)) == 1
    assert db.get(AdClusterMembership, x.id).cluster_id == db.get(AdClusterMembership, y.id).cluster_id
    assert originals(db) == before
    link = db.scalar(select(ProductLink))
    assert link.relation_kind == "duplicate_product" and link.algorithm_version == CM.VERSION
    assert 0.93 <= link.score <= 1 and "phash" in link.signals["families"]
    event_count = db.scalar(select(func.count(Event.id)))
    C.run(db)
    assert db.scalar(select(func.count(Event.id))) == event_count
    assert originals(db) == before


def test_same_shop_without_product_evidence_never_groups(db):
    a, b = product(db, "Water bottle", "AUTO_CONFIRMED"), product(db, "Garden hose", "AUTO_CONFIRMED")
    ad(db, a, "same-shop", "https://shop.example/")
    ad(db, b, "same-shop", "https://shop.example/collections/all")
    C.run(db)
    assert len(roots(db)) == 2 and db.scalar(select(func.count(ProductLink.id))) == 0


@pytest.mark.parametrize("left,right,blocker", [
    ({"brand": "A"}, {"brand": "B"}, "metadata_conflict:brand"),
    ({"model": "X100"}, {"model": "X200"}, "metadata_conflict:model"),
    ({"color": "red"}, {"color": "blue"}, "metadata_conflict:color"),
    ({"capacity": "128GB"}, {"capacity": "256GB"}, "metadata_conflict:capacity"),
    ({"gtin": "4006381333931"}, {"gtin": "5901234123457"}, "metadata_conflict:gtin"),
])
def test_identical_images_cannot_override_metadata_conflicts(db, left, right, blocker):
    a, b, x, y = same_product(db)
    x.product_metadata, y.product_metadata = left, right
    db.flush()
    C.run(db)
    assert len(roots(db)) == 2
    link = db.scalar(select(ProductLink))
    assert link.status == "blocked" and blocker in link.blockers
    assert link.relation_kind == "different_product"


@pytest.mark.parametrize("names", [("Phone 15", "Phone 16"), ("Phone 15 Pro", "Phone 15"), ("Serum 50ml", "Serum 100ml")])
def test_normalized_names_keep_models_sizes_and_variants(db, names):
    same_product(db, names=names)
    C.run(db)
    assert len(roots(db)) == 2
    assert db.scalar(select(ProductLink)).status == "blocked"


def test_similar_clip_is_a_separate_relation_not_a_duplicate_product(db):
    a, b = product(db), product(db)
    x, y = ad(db, a), ad(db, b, "shop-b")
    picture(db, x, vec(3))
    picture(db, y, vec(9, noise=0.25, base=3))
    C.run(db)
    link = db.scalar(select(ProductLink))
    assert link.status == "similar" and link.relation_kind == "similar_product"
    assert len(roots(db)) == 2


def test_valid_gtin_groups_across_shops_without_images(db):
    a, b = product(db), product(db)
    a.product_identity, b.product_identity = {"gtin": "4006381333931"}, {"gtin": "04006381333931"}
    ad(db, a, "first"); ad(db, b, "second")
    db.flush()
    C.run(db)
    assert len(roots(db)) == 1
    assert db.scalar(select(ProductLink)).signals["identity"] == ["gtin"]


def test_brand_and_model_metadata_is_used(db):
    a, b = product(db), product(db)
    ad(db, a, "one", product_metadata={"brand": "Acme", "model": "X-100"})
    ad(db, b, "two", product_metadata={"brand": "ACME", "model": "X100"})
    C.run(db)
    assert len(roots(db)) == 1


@pytest.mark.parametrize("same_shop", [True, False])
def test_sku_is_scoped_to_its_shop(db, same_shop):
    a, b = product(db, "Water bottle", "AUTO_CONFIRMED"), product(db, "Water bottle", "AUTO_CONFIRMED")
    ad(db, a, "one", product_metadata={"sku": "123"})
    ad(db, b, "one" if same_shop else "two", product_metadata={"sku": "123"})
    C.run(db)
    assert len(roots(db)) == (1 if same_shop else 2)
    if not same_shop:
        assert db.scalar(select(ProductLink)).relation_kind == "similar_product"


def test_different_sku_same_shop_is_blocked_even_with_same_image(db):
    a, b, x, y = same_product(db, pages=("same", "same"))
    x.product_metadata, y.product_metadata = {"sku": "small"}, {"sku": "large"}
    db.flush()
    C.run(db)
    assert len(roots(db)) == 2 and "metadata_conflict:sku" in db.scalar(select(ProductLink)).blockers


def test_product_query_parameters_are_preserved_and_tracking_is_ignored(db):
    a, b, c = product(db), product(db), product(db)
    ad(db, a, "one", "https://shop.example/item?id=1&utm_source=a")
    ad(db, b, "two", "https://shop.example/item?id=1&fbclid=tracking")
    ad(db, c, "three", "https://shop.example/item?id=2")
    C.run(db)
    links = list(db.scalars(select(ProductLink)))
    assert len(links) == 1 and {links[0].a_id, links[0].b_id} == {a.id, b.id}
    assert links[0].status == "pending"


def test_duplicate_ads_are_identity_links_and_originals_are_retained(db):
    a, b, x, y = same_product(db)
    y.external_id = x.external_id
    db.flush()
    before = originals(db)
    C.run(db)
    d = db.scalar(select(DuplicateAdLink))
    assert {d.a_id, d.b_id} == {x.id, y.id} and d.evidence["kind"] == "duplicate_ad"
    assert originals(db) == before
    C.run(db)
    assert db.scalar(select(func.count(DuplicateAdLink.a_id))) == 1


def test_same_creative_across_shops_with_different_ad_ids_is_not_duplicate_ad(db):
    same_product(db)
    C.run(db)
    assert db.scalar(select(func.count(DuplicateAdLink.a_id))) == 0
    assert db.scalar(select(ProductLink)).relation_kind == "duplicate_product"


def test_duplicate_ad_identifier_groups_even_without_images_or_names(db):
    a, b = product(db), product(db)
    x, y = ad(db, a), ad(db, b)
    y.external_id = x.external_id
    db.flush()
    C.run(db)
    assert len(roots(db)) == 1 and db.scalar(select(DuplicateAdLink))


def test_cannot_link_propagates_across_a_transitive_cluster_bridge(db):
    a, b, x, y = same_product(db)
    c = product(db, "Portable water bottle", "AUTO_CONFIRMED")
    z = ad(db, c, "third")
    picture(db, z, vec(22), "1234567890abcdef")
    C.record_cannot_link(db, a.id, c.id, "reviewer")
    db.flush()
    C.run(db); C.run(db)
    assert db.get(AdClusterMembership, x.id).cluster_id != db.get(AdClusterMembership, z.id).cluster_id


def test_undo_restores_memberships_including_new_ads_but_not_original_data(db):
    a, b, x, y = same_product(db)
    C.run(db)
    e = db.scalar(select(Event).where(Event.type == "PRODUCT_CLUSTERED"))
    source = db.get(Product, e.data["source_cluster_id"])
    new = ad(db, source, "new")
    CM.sync(db, [new.id])
    before = originals(db)
    CM.undo(db, e, "reviewer")
    assert len(roots(db)) == 2 and db.get(AdClusterMembership, new.id).cluster_id == source.id
    assert originals(db) == before
    with pytest.raises(ValueError):
        CM.undo(db, e, "reviewer")


def test_migration_adds_old_schema_columns_and_is_idempotent(db):
    a = product(db); x = ad(db, a)
    db.commit()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE product_links DROP COLUMN relation_kind"))
        conn.execute(text("ALTER TABLE product_links DROP COLUMN algorithm_version"))
    before = originals(db)
    db.rollback()
    migrate(engine); migrate(engine)
    db.expire_all()
    assert originals(db) == before
    m = db.get(AdClusterMembership, x.id)
    assert m.cluster_id == a.id and m.confidence is None and m.method == "legacy"
    assert {"relation_kind", "algorithm_version"} <= {r["name"] for r in inspect(db.connection()).get_columns("product_links")}


def test_real_mkt_session_can_review_and_undo_without_impersonating_actor(db, monkeypatch):
    from app.main import app
    from app.workspace.auth import hash_password
    from app.workspace.models import User, Profile
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    u = User(name="MKT", email="cluster@example.test", password_hash=hash_password("cluster-test-password"), role="MKT", status="active")
    db.add(u); db.flush(); db.add(Profile(user_id=u.id))
    a, b = product(db), product(db)
    ad(db, a, "a", "https://shop.example/item/1"); ad(db, b, "b", "https://shop.example/item/1")
    C.run(db); db.commit()
    client = TestClient(app)
    client.get("/api/auth/csrf")
    headers = {"X-CSRF-Token": client.cookies["toolspy_csrf"]}
    assert client.post("/api/auth/login", json={"email": u.email, "password": "cluster-test-password"}, headers=headers).status_code == 200
    headers = {"X-CSRF-Token": client.cookies["toolspy_csrf"]}
    row = client.get("/api/clusters/candidates").json()["rows"][0]
    assert client.post(f"/api/clusters/candidates/{row['id']}/accept", json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    response = client.post(f"/api/clusters/candidates/{row['id']}/accept", json={"user": "fake-admin"}, headers=headers)
    assert response.status_code == 200, response.text
    db.expire_all()
    event = db.get(Event, response.json()["event_id"])
    assert event.data["by"] == str(u.id)
    eid = event.id
    db.rollback()
    groups = client.get("/api/clusters/groups").json()
    assert groups["total"] == 1 and groups["rows"][0]["ads"] == 2
    assert client.get(f"/api/clusters/groups/{response.json()['cluster_id']}").json()["total"] == 2
    assert client.post(f"/api/clusters/events/{eid}/undo", json={}, headers=headers).status_code == 200
    assert client.post("/api/collector/connectors/1/sync", json={}, headers=headers).status_code == 403


def test_pending_total_excludes_missing_product_rows(db, monkeypatch):
    from app.main import app
    a, b = product(db), product(db)
    db.add_all([ProductLink(a_id=a.id, b_id=b.id), ProductLink(a_id=b.id, b_id=999999)])
    db.commit()
    monkeypatch.setenv("ADMIN_TOKEN", "cluster-test-admin")
    client = TestClient(app, headers={"X-Admin-Token": "cluster-test-admin"})
    r = client.get("/api/clusters/candidates").json()
    assert r["total"] == len(r["rows"]) == r["counts"]["pending"] == 1


def test_concurrent_review_commits_only_one_grouping(db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.main import app
    a, b = product(db), product(db)
    ad(db, a, "a", "https://shop.example/item/1"); ad(db, b, "b", "https://shop.example/item/1")
    C.run(db); db.commit()
    lid = db.scalar(select(ProductLink.id))
    db.rollback()
    monkeypatch.setenv("ADMIN_TOKEN", "concurrent-cluster-test")
    def review(_):
        client = TestClient(app, headers={"X-Admin-Token": "concurrent-cluster-test"})
        return client.post(f"/api/clusters/candidates/{lid}/accept", json={}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(review, range(2))) == [200, 400]
    assert db.scalar(select(func.count(Event.id)).where(Event.type == "PRODUCT_CLUSTERED")) == 1


def test_older_undo_is_refused_until_latest_grouping_is_undone(db):
    a, b, x, y = same_product(db)
    C.run(db)
    first = db.scalar(select(Event).where(Event.type == "PRODUCT_CLUSTERED"))
    c = product(db, "Portable water bottle", "AUTO_CONFIRMED")
    z = ad(db, c, "third"); picture(db, z, vec(22), "1234567890abcdef")
    C.run(db)
    second = db.scalar(select(Event).where(Event.type == "PRODUCT_CLUSTERED").order_by(Event.id.desc()))
    assert second.id != first.id
    with pytest.raises(ValueError):
        CM.undo(db, first, "reviewer")
    CM.undo(db, second, "reviewer")
    CM.undo(db, first, "reviewer")
    assert len(roots(db)) == 3


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL pgvector path")
def test_pgvector_neighbors_are_reused_without_inference(db):
    from app.visual.models import ProductImage
    a, b, x, y = same_product(db)
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.execute(text("ALTER TABLE product_images ADD COLUMN embedding_vec vector(512)"))
    for row in db.scalars(select(ProductImage)):
        db.execute(text("UPDATE product_images SET embedding_vec=CAST(:v AS vector) WHERE id=:id"),
                   {"v": embed.vec_literal(embed.from_bytes(row.embedding)), "id": row.id})
    db.execute(text("CREATE INDEX phase1_test_hnsw ON product_images USING hnsw (embedding_vec vector_cosine_ops)"))
    embed._pgv.update(ok=None, at=0)
    assert embed.pgvector_ready(db)
    result = C.run(db)
    assert result["merged"] == 1 and db.scalar(select(ProductLink)).signals["clip"] >= 0.99
