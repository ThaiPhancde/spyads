"""Ad freshness (design §2.1): running days stop at the last source confirmation + ACTIVE_GRACE_DAYS, a verified ad keeps
counting, ingest moves first_seen earlier when a source reports an earlier start, scheduled Meta syncs fetch stopped ads."""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app import ingest, media
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.models import Ad, Product
from app.realtime import _params_from
from app.services.adsignals import ACTIVE_GRACE_DAYS, days_running, verified_live
from app.services.features import ProductData, compute_features

RUN = uuid.uuid4().hex[:6]


def _ad(i, first, verified, **kw):
    return Ad(id=i, external_id=f"fr{i}", source="meta_library", platform="facebook", network="meta", channel="ads",
              is_internal=False, is_active=True, first_seen_at=first, last_seen_at=verified, last_verified_at=verified,
              page_id=f"page{i}", countries=["PH"], country="PH", **kw)


def test_unverified_ad_is_capped_and_verified_ad_keeps_counting():
    now = datetime.utcnow()
    stale = _ad(1, now - timedelta(days=60), now - timedelta(days=20))   # source has not re-confirmed it for 20 days
    fresh = _ad(2, now - timedelta(days=60), now - timedelta(hours=1))   # Meta liveness saw it an hour ago
    assert days_running(stale, now) == 60 - 20 + int(ACTIVE_GRACE_DAYS)  # stops at last_verified + grace, not now
    assert days_running(fresh, now) == 60
    assert not verified_live(stale, now) and verified_live(fresh, now)
    assert stale.is_active  # never flipped without source evidence

    p = Product(id=1, canonical_name="x", first_seen_at=now - timedelta(days=60))
    only_stale = compute_features(ProductData(product=p, ads=[stale]))
    assert only_stale["active_ads"] == 0 and only_stale["longevity_days"] == 0
    both = compute_features(ProductData(product=p, ads=[stale, fresh]))
    assert both["active_ads"] == 1 and both["longevity_days"] == 60


def test_refresh_moves_first_seen_earlier(monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)

    def rec(first=None):
        return AdRecord(source="tiktok_top_ads", source_ad_id=f"fs{RUN}", platform="tiktok", country="PH", advertiser="Shop",
                        ad_text="Portable neck fan for summer", active=True, first_seen=first,
                        media=[MediaItem(type="image", url=f"https://cdn.example/fs{RUN}.jpg")])

    with SessionLocal() as db:
        ingest.ingest_ads(db, [rec()])                      # Top Ads: no start date, first_seen = collection day
        ad = db.scalar(select(Ad).where(Ad.external_id == f"fs{RUN}"))
        assert ad.first_seen_at.date() == datetime.utcnow().date()
        ingest.ingest_ads(db, [rec("2026-09-01")])          # a source with a real start date
        p = db.get(Product, ad.product_id)
        assert ad.first_seen_at == datetime(2026, 9, 1) and p.first_seen_at == datetime(2026, 9, 1)
        ingest.ingest_ads(db, [rec("2026-09-20")])          # a later date never moves it forward
        assert ad.first_seen_at == datetime(2026, 9, 1)
        db.rollback()


def test_scheduled_meta_sync_asks_for_stopped_ads():
    assert _params_from(SimpleNamespace(config={}, adapter="meta_library")).active_only is False
    assert _params_from(SimpleNamespace(config={}, adapter="tiktok_top_ads")).active_only is True


def test_collection_dated_tiktok_page_is_never_a_new_entrant():
    now = datetime.utcnow()
    meta = _ad(3, now - timedelta(days=2), now - timedelta(hours=1))  # a Meta page that really started 2 days ago
    tt = _ad(4, now - timedelta(days=1), now - timedelta(hours=1), )   # TikTok Top Ads: first_seen = day we collected it
    tt.source, tt.network, tt.platform = "tiktok_top_ads", "tiktok", "tiktok"
    f = compute_features(ProductData(product=Product(id=2, canonical_name="y", first_seen_at=now - timedelta(days=2)), ads=[meta, tt]))
    assert f["new_advertisers_7d"] == 1 and f["new_ads_30d"] == 1
