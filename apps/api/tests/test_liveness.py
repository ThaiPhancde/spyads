"""Meta liveness (design §2.1): a page scan in ALL statuses marks ads Meta reports stopped as inactive with Meta's end
date; an ad the page scan missed is looked up by its Library ID instead of staying 'active' forever."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app import ingest, media, realtime
from app.collector.adapters.meta import MetaLibraryConnector
from app.collector.contract import AdRecord, MediaItem
from app.db import SessionLocal
from app.models import Ad, Connector

RUN = uuid.uuid4().hex[:6]
PAGE = f"pg{RUN}"


def rec(n, active, last=None):
    return AdRecord(source="meta_library", source_ad_id=f"{n}{RUN}", platform="facebook", country="PH", advertiser="Shop",
                    page_id=PAGE, ad_text=f"Neck fan offer {n}", active=active, first_seen="2026-09-01", last_seen=last,
                    media=[MediaItem(type="image", url=f"https://cdn.example/{n}{RUN}.jpg")])


def test_page_scan_marks_stopped_and_id_lookup_verifies_the_rest(monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a, **k: None)
    monkeypatch.setattr(media, "enqueue", lambda *a, **k: None)
    stale = datetime.utcnow() - timedelta(days=8)
    with SessionLocal() as db:
        db.add(Connector(name="meta", provider="meta", adapter="meta_library", kind="browser", group="transparency", enabled=True))
        ingest.ingest_ads(db, [rec("stopped", True), rec("missed", True)])
        for ad in db.scalars(select(Ad).where(Ad.page_id == PAGE)):
            ad.last_verified_at = stale  # not confirmed for 8 days
        db.commit()

    calls = []

    def fake_fetch(self, params, country, state=None):
        calls.append(params)
        if params.page_ids:  # page scan: Meta says one ad has stopped, the other is beyond the scanned result pages
            assert params.active_only is False
            return [rec("stopped", False, "2026-10-20")], {"done": False}
        return ([rec("missed", True)] if params.query == f"missed{RUN}" else []), {"done": True}

    monkeypatch.setattr(MetaLibraryConnector, "fetch_page", fake_fetch)
    monkeypatch.setattr(realtime, "LIVENESS_MAX_PAGES", 1)
    # the page scan is incomplete (big page), so the missed ad never collects a miss — it is looked up because it is stale
    out = realtime.verify_liveness()
    with SessionLocal() as db:
        stopped = db.scalar(select(Ad).where(Ad.external_id == f"stopped{RUN}"))
        missed = db.scalar(select(Ad).where(Ad.external_id == f"missed{RUN}"))
        assert not stopped.is_active and stopped.inactive_at == datetime(2026, 10, 20)  # Meta's end date, not "now"
        assert missed.is_active and missed.verify_misses == 0 and missed.last_verified_at > stale
    assert out["looked_up"] == 1 and any(p.query == f"missed{RUN}" for p in calls)
