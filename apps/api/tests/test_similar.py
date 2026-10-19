"""Phase 6 similar products / ads: accuracy on constructed cases (multilingual concepts, CLIP, hash), similar vs
duplicate, history and followed-advertiser lists with ownership + MKT filters, response time. Disposable DB only."""
from datetime import datetime, timedelta
import time
import uuid

import pytest
from sqlalchemy import select

from app import similar as S
from app.cluster_models import DuplicateAdLink, ProductCluster
from app.db import SessionLocal
from app.models import Ad, Advertiser, Product, ProductLink
from app.services import cluster_membership as CM
from app.workspace.models import AdvertiserFilter, Profile
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_clustering import picture, vec
from test_workspace import client, write


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    from app.services import llm
    from app.visual import embed
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI / inference in similarity"))
    monkeypatch.setattr(llm, "complete_json", boom)
    monkeypatch.setattr(embed, "embed_image", boom)
    monkeypatch.setattr(embed, "embed_batch", boom)
    S._TEXT.update(at=0.0, v={})  # ids restart per test: never reuse another test's text features
    embed._pgv.update(ok=None, at=0)  # another file may have cached "pgvector ready" for a table that no longer has the column
    yield
    embed._pgv.update(ok=None, at=0)


def item(name, category=None, text="", landing=None, advertiser=None, page=None, image=None, phash=None, language="en"):
    with SessionLocal.begin() as db:
        i = uuid.uuid4().hex[:8]
        p = Product(product_code=f"SM-{i}", canonical_name=name, category=category, name_status="AUTO_CONFIRMED",
                    in_target=True, country="PH", first_seen_at=datetime.utcnow() - timedelta(days=5), last_seen_at=datetime.utcnow())
        db.add(p); db.flush()
        a = Ad(external_id=f"sm-{i}", source="meta_library", platform="facebook", product_id=p.id, advertiser_id=advertiser,
               page_id=page, title=name, ad_text=text, search_text=f"{name} {text}".lower(), landing_url=landing,
               language=language, country="PH", is_active=True, first_seen_at=datetime.utcnow() - timedelta(days=5))
        db.add(a); db.flush()
        if image is not None:
            picture(db, a, image, phash)
        CM.sync(db, [a.id])
        return p.id, a.id


def pids(rows):
    return [r["product_id"] for r in rows]


def test_similar_products_by_use_across_languages():
    users()
    src, _ = item("Polarized sunglasses for men", "accessories", "UV400 driving shades")
    vi, _ = item("Kính mát phân cực nam", "accessories", language="vi")
    ar, _ = item("نظارة شمسية للرجال", language="ar")
    glasses, _ = item("Blue frame eyeglasses", "accessories")
    hose, _ = item("Garden hose 30m", "accessories")
    with SessionLocal() as db:
        r = S.similar_products(db, src, 1)
    got = pids(r["similar"])
    assert got[:2] == [vi, ar] and hose not in got and (glasses not in got or got.index(glasses) > 1)
    top = r["similar"][0]
    assert top["relation"] == "similar_product" and top["signals"]["concepts"] >= 0.8
    assert any("sunglasses" in x for x in top["reasons"]) and r["duplicates"] == []
    assert set(r["signals_used"]) == {"concepts", "category", "tokens"}  # no image on the source: weight renormalised


def test_duplicates_are_separated_from_similar_and_cluster_members_hidden():
    users()
    src, _ = item("Pet leash nylon", "pets", landing="https://shop.example/products/leash-1?utm_source=fb")
    same_url, _ = item("Dog lead rope", "pets", landing="https://www.shop.example/products/leash-1")
    linked, _ = item("Pet leash nylon strong", "pets")
    alike, _ = item("Retractable pet leash", "pets")
    member, member_ad = item("Pet leash nylon v2", "pets")
    with SessionLocal.begin() as db:
        a, b = sorted((src, linked))
        db.add(ProductLink(a_id=a, b_id=b, score=0.8, relation_kind="duplicate_product", status="pending", signals={}, blockers=[]))
        db.get(ProductCluster, member).merged_into_id = src
    with SessionLocal() as db:
        r = S.similar_products(db, src, 1)
    dups = {d["product_id"]: d["duplicate_evidence"] for d in r["duplicates"]}
    assert dups == {same_url: "same_product_url", linked: "phase1_duplicate_link"}
    assert alike in pids(r["similar"]) and not ({same_url, linked} & set(pids(r["similar"])))
    assert member not in pids(r["similar"]) + list(dups)
    with SessionLocal() as db:  # asking from the merged member answers for the cluster root
        assert S.similar_products(db, member, 1)["product_id"] == src


def test_clip_vectors_and_image_hash():
    users()
    src, _ = item("Ceramic mug", image=vec(3), phash="0f0f0f0f0f0f0f0f")
    look, _ = item("Coffee cup gift", image=vec(3) + 0.02, phash="f0f0f0f0f0f0f0f0")
    copy, _ = item("Ceramic mug", image=vec(3), phash="0f0f0f0f0f0f0f0e")
    other, _ = item("Phone holder", image=vec(80), phash="123456789abcdef0")
    with SessionLocal() as db:
        r = S.similar_products(db, src, 1)
    sim = {x["product_id"]: x for x in r["similar"]}
    assert "image" in r["signals_used"] and look not in sim  # no shared word/category: only pgvector can surface it
    assert [d["product_id"] for d in r["duplicates"]] == [copy] and r["duplicates"][0]["duplicate_evidence"] == "same_image_and_name"
    assert other not in sim


@pytest.mark.skipif(not __import__("app.db", fromlist=["engine"]).engine.dialect.name == "postgresql", reason="pgvector path")
def test_pgvector_prefilter_finds_look_alikes_without_shared_words():
    from sqlalchemy import text
    from app.visual import embed
    from app.visual.models import ProductImage
    users()
    src, src_ad = item("Ceramic mug", image=vec(3), phash="0f0f0f0f0f0f0f0f")
    look, look_ad = item("Coffee cup gift", image=vec(3) + 0.02, phash="f0f0f0f0f0f0f0f0")
    other, _ = item("Phone holder", image=vec(80), phash="123456789abcdef0")
    with SessionLocal() as db:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.execute(text("ALTER TABLE product_images ADD COLUMN embedding_vec vector(512)"))
        for row in db.scalars(select(ProductImage)):
            db.execute(text("UPDATE product_images SET embedding_vec=CAST(:v AS vector) WHERE id=:id"),
                       {"v": embed.vec_literal(embed.from_bytes(row.embedding)), "id": row.id})
        db.execute(text("CREATE INDEX phase6_test_hnsw ON product_images USING hnsw (embedding_vec vector_cosine_ops)"))
        embed._pgv.update(ok=None, at=0)
        assert embed.pgvector_ready(db)
        r = S.similar_products(db, src, 1)
        sim = {x["product_id"]: x for x in r["similar"]}
        assert look in sim and sim[look]["signals"]["image"] > 0.9 and other not in sim
        ads = {x["ad_id"] for x in S.similar_ads(db, src_ad)["similar"]}
        assert look_ad in ads
        db.commit()


def test_similar_ads_creative_text_duplicates_and_mkt_filter():
    users()
    with SessionLocal.begin() as db:
        advs = [Advertiser(name=f"A{i}", source="meta_library", source_advertiser_id=f"9{i}", platform="facebook") for i in range(2)]
        db.add_all(advs); db.flush(); adv, blocked = advs[0].id, advs[1].id
    _, src = item("Sunglasses sale", text="Polarized sunglasses 50% off", advertiser=adv, page="p1", image=vec(7), phash="aaaaaaaaaaaaaaaa")
    _, twin = item("Sunglasses sale", text="Polarized sunglasses 50% off", advertiser=adv, page="p1", image=vec(7), phash="aaaaaaaaaaaaaaab")
    _, alike = item("Kính mát thời trang", text="kính mát phân cực", advertiser=None, image=vec(7) + 0.03, phash="5555555555555555")
    _, linked = item("Shades", text="shades for summer sunglasses", image=vec(40))
    _, hidden = item("Sunglasses deal", text="polarized sunglasses deal", advertiser=blocked, image=vec(7))
    _, noise = item("Garden hose", text="garden hose 30m")
    with SessionLocal.begin() as db:
        a, b = sorted((src, linked))
        db.add(DuplicateAdLink(a_id=a, b_id=b, confidence=1.0, evidence={}))
    ca = client("mkt-a@example.test")
    r = ca.get(f"/api/similar/ads/{src}").json()
    dups = {d["ad_id"]: d["duplicate_evidence"] for d in r["duplicates"]}
    assert dups == {twin: "same_creative_and_copy", linked: "phase1_duplicate_link"}
    sim = {x["ad_id"]: x for x in r["similar"]}
    assert alike in sim and sim[alike]["relation"] == "similar_ad" and noise not in sim
    assert hidden in sim  # no MKT filter yet
    with SessionLocal.begin() as db:
        db.add(AdvertiserFilter(user_id=1, advertiser_id=blocked, mode="blacklist"))
    r = ca.get(f"/api/similar/ads/{src}").json()
    assert hidden not in {x["ad_id"] for x in r["similar"]}  # MKT blacklist applies
    assert r["took_ms"] >= 0 and r["candidates"] >= 3


def test_for_me_uses_history_ownership_and_failed_tests():
    a, b, _ = users()
    _, seed_ad = item("Polarized sunglasses", "accessories")
    vi, _ = item("Kính mát phân cực", "accessories")
    ar, _ = item("نظارة شمسية", "accessories")
    taken, _ = item("Sunglasses for women", "accessories")
    fail, _ = item("Dog leash nylon", "pets")
    leash_like, _ = item("Dog leash strong", "pets")
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    assert ca.get("/api/similar/for-me").json()["seeds"] == 0
    write(ca, "PUT", f"/workspace/ads/{seed_ad}", {"saved": True})
    write(cb, "POST", "/allocations/claim", {"product_id": taken})
    aid = write(ca, "POST", "/allocations/claim", {"product_id": fail}).json()["id"]
    write(ca, "PATCH", f"/allocations/{aid}/status", {"status": "testing"})
    write(ca, "POST", f"/allocations/{aid}/results", {"outcome": "loser"})
    r = ca.get("/api/similar/for-me").json()
    got = pids(r["rows"])
    assert {vi, ar} <= set(got) and taken not in got and fail not in got and leash_like not in got
    assert r["rows"][0]["because_of"][0]["name"] == "Polarized sunglasses"
    # research view still shows the taken one, labelled; exclusive view drops it
    research = ca.get(f"/api/similar/products/{vi}").json()["similar"]
    assert {x["product_id"]: x["allocation"] for x in research}.get(taken) == "taken"
    assert taken not in pids(ca.get(f"/api/similar/products/{vi}?exclusive=true").json()["similar"])


def test_more_from_followed_advertisers():
    a, b, _ = users()
    with SessionLocal.begin() as db:
        adv = Advertiser(name="Rival", source="meta_library", source_advertiser_id="501", platform="facebook")
        db.add(adv); db.flush(); adv_id = adv.id
    p1, _ = item("Rival lamp", advertiser=adv_id)
    p2, ad2 = item("Rival fan", advertiser=adv_id)
    p3, _ = item("Rival heater", advertiser=adv_id)
    item("Somebody else", advertiser=None)
    ca, cb = client("mkt-a@example.test"), client("mkt-b@example.test")
    assert ca.get("/api/similar/followed").json()["rows"] == []
    write(ca, "POST", f"/competitors/{adv_id}/follow", {"interval_hours": 24})
    write(cb, "POST", "/allocations/claim", {"product_id": p1})
    write(ca, "PUT", f"/workspace/ads/{ad2}", {"saved": True})
    r = ca.get("/api/similar/followed").json()
    assert pids(r["rows"]) == [p3] and r["rows"][0]["advertiser"] == "Rival"
    assert "Follow" in r["rows"][0]["reasons"][0]


def test_response_time_on_bounded_candidates():
    users()
    with SessionLocal.begin() as db:
        for i in range(600):
            p = Product(product_code=f"BULK-{i}", canonical_name=f"sunglasses model {i}", category="accessories",
                        name_status="AUTO_CONFIRMED", in_target=True)
            db.add(p); db.flush()
            db.add(Ad(external_id=f"bulk-{i}", source="t", platform="facebook", product_id=p.id, title=p.canonical_name,
                      search_text=p.canonical_name, is_active=True))
    src, src_ad = item("Polarized sunglasses", "accessories")
    with SessionLocal() as db:
        t = time.perf_counter()
        r = S.similar_products(db, src, 1)
        took = time.perf_counter() - t
        assert r["candidates"] <= 600 and len(r["similar"]) == 20
        assert took < 3.0, took
        t = time.perf_counter()
        S.similar_ads(db, src_ad)
        assert time.perf_counter() - t < 3.0


def test_access():
    users()
    pid, aid = item("Sunglasses")
    anon = client()
    assert anon.get(f"/api/similar/products/{pid}").status_code == 401
    ca = client("mkt-a@example.test")
    assert ca.get("/api/similar/products/999999").status_code == 404
    assert ca.get("/api/similar/ads/999999").status_code == 404
    assert ca.get(f"/api/similar/products/{pid}").status_code == 200
