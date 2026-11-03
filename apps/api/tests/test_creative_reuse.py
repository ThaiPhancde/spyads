"""P4 — creative / video reuse observations and experimental reuse features (never part of TEST / TEST_NOW)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import creative_reuse as R
from app.db import SessionLocal
from app.models import Ad, AdSource, Advertiser, Creative, Product, Store

NOW = datetime(2026, 11, 3, 12, 0, 0)
V2 = {"algorithm": "cellmean-dhash", "version": 2, "frame_hashes": ["a", "b", "c"], "confidence": 0.67}


@pytest.fixture
def db(clean_catalog):
    with SessionLocal() as s:
        yield s


def ad(db, i, page, country="PH", store=None, variants=None, seen=NOW, fam=7, fp2=V2, source="meta_library"):
    adv = db.scalar(select(Advertiser).where(Advertiser.source_advertiser_id == page))
    if adv is None:
        adv = Advertiser(name=f"Shop {page}", source="meta_library", source_advertiser_id=page)
        db.add(adv)
        db.flush()
    a = Ad(external_id=f"r{i}", source=source, platform="facebook", page_id=page, advertiser_id=adv.id, country=country, store_id=store,
           variants=variants, snapshot_url=f"https://www.facebook.com/ads/library/?id=r{i}", first_seen_at=seen - timedelta(days=30))
    db.add(a)
    db.flush()
    db.add(AdSource(ad_id=a.id, source=source, source_ad_id=f"r{i}", platform="facebook", first_collected_at=seen, last_collected_at=seen))
    db.add(Creative(ad_id=a.id, type="video", source_url=f"https://v.example/{i}.mp4", status="stored", family_id=fam, video_fp_v2=fp2))
    db.flush()
    return a


def test_refresh_records_one_observation_per_family_and_ad(db):
    ad(db, 1, "p1"), ad(db, 2, "p2", country="SA"), ad(db, 3, "p1")
    db.commit()
    assert R.refresh(db, now=NOW) == {"observations": 3}
    assert R.refresh(db, now=NOW) == {"observations": 3}          # idempotent
    rows = db.scalars(select(R.CreativeReuseObservation).order_by(R.CreativeReuseObservation.ad_id)).all()
    assert [(r.family_key, r.page_id, r.market) for r in rows] == [("fam:7", "p1", "PH"), ("fam:7", "p2", "SA"), ("fam:7", "p1", "PH")]
    assert rows[0].evidence["kind"] == "video_fp_v2" and rows[0].evidence["confidence"] == 0.67
    assert rows[0].evidence["snapshot_url"].endswith("?id=r1") and rows[0].advertiser == "Shop p1"


def test_a_re_seen_ad_moves_last_seen_only(db):
    a = ad(db, 1, "p1")
    db.commit()
    R.refresh(db, now=NOW)
    src = db.scalar(select(AdSource).where(AdSource.ad_id == a.id))
    src.last_collected_at = NOW + timedelta(days=2)
    db.commit()
    R.refresh(db, now=NOW + timedelta(days=2))
    o = db.scalar(select(R.CreativeReuseObservation))
    assert (o.first_seen_at, o.last_seen_at) == (NOW, NOW + timedelta(days=2))


def test_features_keep_the_four_counts_apart(db):
    """ad_count (our deduplicated ads) ≠ collation_variants (Meta's own variant count) ≠ distinct_pages ≠ distinct_shop_id."""
    s1, s2 = Store(domain="shop-one.example"), Store(domain="shop-two.example")
    db.add_all([s1, s2])
    db.flush()
    ad(db, 1, "p1", store=s1.id, variants=4, seen=NOW - timedelta(days=20))
    ad(db, 2, "p1", store=s1.id, variants=2, seen=NOW - timedelta(days=2))
    ad(db, 3, "p2", store=s2.id, variants=None, seen=NOW - timedelta(days=1))
    db.commit()
    R.refresh(db, now=NOW)
    f = R.features(db, "fam:7", now=NOW)
    assert (f["ad_count"], f["collation_variants"], f["distinct_pages"], f["distinct_shop_id"]) == (3, 7, 2, 2)
    assert f["new_distinct_pages_7d"] == 1 and f["reuse_velocity_7d"] == round(2 / 7, 3)
    assert f["earliest_observed_video_at"] == (NOW - timedelta(days=20)).isoformat() and f["experimental"] is True


def test_reuse_features_never_touch_the_scores(db):
    p = Product(product_code="RU-1", canonical_name="Blender", opportunity_score=61.0, recommendation="TEST", features={"x": 1})
    db.add(p)
    db.flush()
    a = ad(db, 1, "p1")
    a.product_id = p.id
    db.commit()
    R.refresh(db, now=NOW)
    R.features(db, "fam:7", now=NOW)
    db.refresh(p)
    assert (p.opportunity_score, p.recommendation, p.features) == (61.0, "TEST", {"x": 1})


def test_evidence_kind_follows_the_strongest_fingerprint(db):
    ad(db, 1, "p1", fp2=None)
    db.commit()
    c = db.scalar(select(Creative))
    c.video_fp, c.video_fp_confidence = "aa:bb:cc", 1.0
    db.commit()
    R.refresh(db, now=NOW)
    assert db.scalar(select(R.CreativeReuseObservation)).evidence["kind"] == "video_fp_v1"


def test_product_reuse_endpoint(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import router
    p = Product(product_code="RU-2", canonical_name="Blender")
    db.add(p)
    db.flush()
    a = ad(db, 1, "p1")
    a.product_id = p.id
    db.scalar(select(Creative)).product_id = p.id
    ad(db, 2, "p2", country="SA")                                    # another shop's ad, same video: the insight
    db.commit()
    R.refresh(db, now=NOW)
    app = FastAPI()
    app.include_router(router)
    out = TestClient(app).get(f"/api/products/{p.id}/creative-reuse").json()
    fam = out["families"][0]
    assert fam["family_key"] == "fam:7" and fam["features"]["distinct_pages"] == 2 and fam["features"]["experimental"]
    assert {o["page_id"] for o in fam["observations"]} == {"p1", "p2"} and all(o["evidence"]["snapshot_url"] for o in fam["observations"])
