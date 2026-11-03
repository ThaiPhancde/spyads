"""P1 (addendum) — batch envelope, database-level uniqueness, explicit run outcomes (docs/META_RELIABILITY_GAP_AUDIT.md §7)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app import crawl_queue as cq, ingest, realtime
from app.collector.adapters.meta import map_library_ad
from app.crawl_queue import CrawlRun
from app.db import SessionLocal
from app.meta_collector_migrate import unique_index
from app.models import Ad, AdSource, Connector, IngestBatch, TrackedQuery


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def raw(i):
    return {"ad_archive_id": str(i), "page_id": "p", "collation_id": f"g{i}",
            "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}


# ---------------------------------------------------------------- UNIQUE(source, source_ad_id) in the database
def test_the_database_refuses_a_second_ad_with_the_same_source_id():
    """Dedup was only the lookup under ingest_lock: a writer outside it (a script, a second API) could insert a twin."""
    with SessionLocal() as db:
        db.add(Ad(external_id="x1", source="meta_library", platform="facebook"))
        db.commit()
        db.add(Ad(external_id="x1", source="meta_library", platform="facebook"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        a = db.scalar(select(Ad))
        db.add(AdSource(ad_id=a.id, source="meta_library", source_ad_id="x1", platform="facebook"))
        db.commit()
        db.add(AdSource(ad_id=a.id, source="meta_library", source_ad_id="x1", platform="facebook"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_unique_index_is_skipped_never_forced_when_old_duplicates_exist(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'dups.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE ads (id INTEGER PRIMARY KEY, source TEXT, external_id TEXT)"))
        c.execute(text("INSERT INTO ads (source, external_id) VALUES ('s','1'), ('s','1'), ('s','2')"))
        assert unique_index(c, "uq_ads_source_external", "ads", ("source", "external_id")) == "skipped_duplicates"
        assert c.execute(text("SELECT count(*) FROM ads")).scalar() == 3          # nothing deleted
        c.execute(text("DELETE FROM ads WHERE id = 2"))
        assert unique_index(c, "uq_ads_source_external", "ads", ("source", "external_id")) == "created"
        assert unique_index(c, "uq_ads_source_external", "ads", ("source", "external_id")) == "exists"


# ---------------------------------------------------------------- batch envelope on the receipt
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import require_token, router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


ENVELOPE = {"job_id": "tab-7f3", "batch_id": "b-0001", "fetched_at": "2026-11-03T09:00:00Z", "schema_version": "adlib-capture-1",
            "cursor_before": "AQH-prev", "cursor_after": "AQH-next"}


def test_extension_batch_envelope_is_stored_with_its_receipt():
    r = client().post("/api/ingest/meta-library", json={"items": [raw(1)], "idempotency_key": "b-0001", "country": "US", **ENVELOPE})
    assert r.status_code == 200
    with SessionLocal() as db:
        b = db.get(IngestBatch, "extension:b-0001")
        assert (b.source, b.job_id, b.batch_id, b.schema_version, b.cursor_before, b.cursor_after) == (
            "extension", "tab-7f3", "b-0001", "adlib-capture-1", "AQH-prev", "AQH-next")
        assert b.fetched_at == datetime(2026, 11, 3, 9, 0, 0)


def test_the_same_job_batch_under_another_key_is_refused():
    """UNIQUE(job_id, batch_id): a batch is one delivery even if the client lost its idempotency key."""
    c = client()
    assert c.post("/api/ingest/meta-library", json={"items": [raw(1)], "idempotency_key": "k1", **ENVELOPE}).status_code == 200
    assert c.post("/api/ingest/meta-library", json={"items": [raw(2)], "idempotency_key": "k2", **ENVELOPE}).status_code == 409


def test_old_extension_without_envelope_still_works():
    assert client().post("/api/ingest/meta-library", json={"items": [raw(3)], "idempotency_key": "legacy"}).status_code == 200


def test_envelope_fields_are_bounded():
    big = {**ENVELOPE, "cursor_after": "x" * 5000}
    assert client().post("/api/ingest/meta-library", json={"items": [raw(4)], "idempotency_key": "big", **big}).status_code == 422


# ---------------------------------------------------------------- explicit run outcomes
T0 = datetime(2026, 11, 3, 12, 0, 0)


def outcome(key):
    with SessionLocal() as db:
        return [r.outcome for r in db.scalars(select(CrawlRun).where(CrawlRun.job_key == key).order_by(CrawlRun.id))]


@pytest.mark.parametrize("health,expected", [("rate_limited", "rate_limited"), ("schema_changed", "schema_changed"), ("auth_expired", "auth_required"),
                                             ("blocked", "blocked"), ("error", "failed"), (None, "failed")])
def test_failure_outcome_follows_the_source_health(health, expected):
    t = cq.claim("c:9:", "c", 9, now=T0)
    cq.fail("c:9:", t, "x", now=T0, blocked=health == "blocked", health=health)
    assert outcome("c:9:") == [expected]


def test_success_outcome_is_complete_only_when_the_source_said_the_end():
    t = cq.claim("c:9:", "c", 9, now=T0)
    cq.finish("c:9:", t, {"coverage": "partial"}, now=T0)
    t = cq.claim("c:9:", "c", 9, now=T0 + timedelta(hours=1))
    cq.finish("c:9:", t, {"coverage": "complete"}, now=T0 + timedelta(hours=1))
    assert outcome("c:9:") == ["partial", "complete"]


def test_a_run_whose_lease_expired_is_cancelled():
    cq.claim("c:9:", "c", 9, now=T0)
    cq.recover(T0 + timedelta(seconds=cq.LEASE_S + 1))
    assert outcome("c:9:") == ["cancelled"]


def tracked():
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True, config={})
        db.add(c)
        db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US", "PH"])
        db.add(q)
        db.commit()
        db.expunge(q)
        return c.id, q


@pytest.mark.parametrize("done,coverage", [((True, True), "complete"), ((True, False), "partial")])
def test_run_coverage_comes_from_every_country_cursor(monkeypatch, done, coverage):
    class Src:
        schema_version = "adlib-graphql-1"

        def fetch_ads(self, params):
            out = params.extra.setdefault("resume_out", {})
            for country, d in zip(("US", "PH"), done):
                out[country] = {"cursor": None if d else "c1", "done": d}
            yield map_library_ad(raw(1), "meta_library", "US")
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Src())
    cid, q = tracked()
    key = realtime.job_key(cid, q)
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    with SessionLocal() as db:
        run = db.scalar(select(CrawlRun).where(CrawlRun.job_key == key))
        assert (run.outcome, run.schema_version) == (coverage, "adlib-graphql-1")
