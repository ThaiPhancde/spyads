from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app.models import Ad, Creative, Product, ProductDailySnapshot
from app.services.hidden import assess, store_verdict
from app.services.engine import is_hidden_winner, _product_snapshot
from app.services.features import ProductData
from app.services.product_evidence import capture_activity

NOW = datetime(2026, 10, 14, 12)


def product(**kw):
    return Product(id=1, canonical_name="Neck massager", recommendation="TEST", category="health", features={}, **kw)


def ad(i=1, **kw):
    values = dict(id=i, external_id=str(i), source="test", platform="facebook", network="meta", channel="ads", advertiser_id=1,
                  page_id="page1", product_id=1, is_active=True, is_internal=False, first_seen_at=NOW-timedelta(days=90),
                  last_seen_at=NOW, variants=10)
    return Ad(**(values | kw))


def creative(i, phash, **kw):
    return Creative(**(dict(id=i, ad_id=i, product_id=1, type="image", source_url=f"https://cdn.test/{i}.jpg", phash=phash) | kw))


def snapshots(ads, states):
    rows = []
    for days, active_ids, units in states:
        t = NOW-timedelta(days=days)
        before = [ad(a.id, is_active=a.id in active_ids, variants=units, last_seen_at=t, first_seen_at=NOW-timedelta(days=90)) for a in ads]
        rows.append(ProductDailySnapshot(product_id=1, date=t.date(), activity_evidence=capture_activity(before, t)))
    return rows


def test_old_single_ad_long_running_is_not_hidden():
    p = product()
    p.features = {"new_ads_7d": 50, "creative_growth_7d": 10, "hidden_score": 99}
    row = assess(p, [ad()], now=NOW)
    assert row["status"] == "WATCH"
    store_verdict(p, row)
    assert not is_hidden_winner(p) and "hidden_score" not in p.features
    assert "score" not in row


def test_replication_requires_distinct_visuals_and_independent_ads():
    ads = [ad(1), ad(2)]
    same = [creative(1, "aaaaaaaaaaaaaaaa"), creative(2, "aaaaaaaaaaaaaaab")]
    assert assess(product(), ads, creatives=same, now=NOW)["status"] == "WATCH"
    different = [creative(1, "aaaaaaaaaaaaaaaa"), creative(2, "5555555555555555")]
    row = assess(product(), ads, creatives=different, now=NOW)
    assert row["status"] == "CONFIRMED" and row["replication"]["distinct_creatives"] == 2
    different[1].ad_id = 1  # carousel is not ad replication
    assert assess(product(), ads, creatives=different, now=NOW)["status"] == "WATCH"


def test_same_cached_clip_or_family_is_duplicate():
    cs = [creative(1, "aaaaaaaaaaaaaaaa"), creative(2, "5555555555555555")]
    im = lambda c: NS(phash=c.phash, image_sha256=None, embedding=np.array([1, 0], dtype=np.float16).tobytes(), emb_model="clip")
    row = assess(product(), [ad(1), ad(2)], creatives=cs, images={c.id: im(c) for c in cs}, now=NOW)
    assert row["status"] == "WATCH"
    cs[0].family_id = cs[1].family_id = 10
    assert assess(product(), [ad(1), ad(2)], creatives=cs, now=NOW)["status"] == "WATCH"


@pytest.mark.parametrize("case", ["skip", "four_sellers", "short", "missing_seller", "inactive", "no_hash", "internal", "listing"])
def test_gates(case):
    p, ads = product(), [ad(1), ad(2)]
    cs = [creative(1, "aaaaaaaaaaaaaaaa"), creative(2, "5555555555555555")]
    if case == "skip": p.recommendation = "SKIP"
    if case == "four_sellers": ads = [ad(i, advertiser_id=i, page_id=f"page{i}") for i in range(1, 5)]
    if case == "short":
        for a in ads: a.first_seen_at = NOW-timedelta(days=6)
    if case == "missing_seller":
        for a in ads: a.advertiser_id = a.page_id = None
    if case == "inactive":
        for a in ads: a.is_active = False
    if case == "no_hash":
        for c in cs: c.phash = None
    if case == "internal": ads[1].is_internal = True
    if case == "listing": ads[1].channel = "commerce"
    assert assess(p, ads, creatives=cs, now=NOW)["status"] != "CONFIRMED"


def test_advertiser_identity_joins_page_and_id_but_does_not_count_missing_as_one():
    ads = [ad(1), ad(2, page_id=None), ad(3, advertiser_id=2, page_id="page1")]
    assert assess(product(), ads, now=NOW)["advertisers"] == 1


def test_momentum_uses_fixed_cohort_not_crawl_growth():
    ads = [ad(1), ad(2)]
    hs = snapshots(ads, [(8, {1}, 1), (2, {1, 2}, 1), (0, {1, 2}, 1)])
    row = assess(product(), ads, snapshots=hs, now=NOW)
    assert row["status"] == "CONFIRMED" and row["momentum"]["confirmed"]
    # New crawls cannot contribute: remove ID 2 from the baseline known cohort.
    hs[0].activity_evidence["known"] = ["meta:1"]
    assert assess(product(), ads, snapshots=hs, now=NOW)["status"] == "WATCH"


def test_variant_growth_is_sustained_and_history_is_real():
    ads = [ad()]
    hs = snapshots(ads, [(8, {1}, 1), (2, {1}, 2), (0, {1}, 3)])
    assert assess(product(), ads, snapshots=hs, now=NOW)["status"] == "CONFIRMED"
    hs[1].activity_evidence["units"] = {"meta:1": 1}
    assert assess(product(), ads, snapshots=hs, now=NOW)["status"] == "WATCH"
    for s in hs: s.activity_evidence = None  # old/backfilled counts alone prove nothing
    assert assess(product(), ads, snapshots=hs, now=NOW)["status"] == "WATCH"


def test_stale_observation_and_non_independent_snapshot_dates():
    a = ad(last_seen_at=NOW-timedelta(days=10))
    assert capture_activity([a], NOW)["active"] == []
    hs = snapshots([ad()], [(8, {1}, 1), (8, {1}, 2), (0, {1}, 3)])
    assert assess(product(), [ad()], snapshots=hs, now=NOW)["status"] == "WATCH"


def test_snapshot_capture_time_cannot_be_relabelled_as_past():
    hs = snapshots([ad()], [(8, {1}, 1), (2, {1}, 2), (0, {1}, 3)])
    hs[0].activity_evidence["captured_at"] = NOW.isoformat()
    assert assess(product(), [ad()], snapshots=hs, now=NOW)["status"] == "WATCH"


def test_cross_source_duplicate_ad_ids_do_not_create_replication():
    ads = [ad(1), ad(2, external_id="1", source="another_export")]
    cs = [creative(1, "aaaaaaaaaaaaaaaa"), creative(2, "5555555555555555")]
    assert assess(product(), ads, creatives=cs, now=NOW)["status"] == "WATCH"


def test_market_filter_keeps_global_advertiser_count(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.services.hidden import compute
    engine = create_engine(f"sqlite:///{tmp_path}/scope.db")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        p = product(product_code="scope-test")
        db.add(p)
        db.flush()
        ads = [ad(i, advertiser_id=None, page_id=f"page{i}", country="PH" if i == 1 else "US") for i in range(1, 5)]
        db.add_all(ads)
        db.commit()
        rows = compute(db, "PH", include_unconfirmed=True)
        assert len(rows) == 1 and rows[0]["advertisers"] == 4 and rows[0]["status"] == "REJECTED"
        ads[0].is_active = False
        db.commit()
        assert compute(db, "PH", include_unconfirmed=True) == []
    engine.dispose()
