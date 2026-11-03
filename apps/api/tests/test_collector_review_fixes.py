"""Regression tests for the acceptance review of the Meta collector upgrade (docs/META_COLLECTOR_UPGRADE.md, Nghiệm thu)."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import crawl_queue as cq, ingest, realtime
from app.collector.adapters import meta as M
from app.collector.adapters.meta import MetaLibraryConnector, map_library_ad
from app.collector.base import Blocked, ConnectorError, FetchParams, RateLimited
from app.collector.batches import ingest_batch
from app.db import SessionLocal
from app.models import Ad, Connector, IngestBatch, TrackedQuery


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def raw(i):
    return {"ad_archive_id": str(i), "page_id": "p", "collation_id": f"g{i}",
            "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}


def rec(i):
    return map_library_ad(raw(i), "meta_library", "US")


def ads():
    with SessionLocal() as db:
        return sorted(db.scalars(select(Ad.external_id)))


# #1 — a record the DB rejects is skipped; it never blocks the batch (and its cursor) forever
def test_poison_record_is_skipped_not_fatal(monkeypatch):
    original = ingest._upsert_ad
    def poison(db, r, *a):
        if r.source_ad_id == "2":
            raise IntegrityError("INSERT", {}, Exception("value too long"))
        return original(db, r, *a)
    monkeypatch.setattr(ingest, "_upsert_ad", poison)
    with SessionLocal() as db:
        res = ingest_batch(db, [rec(1), rec(2), rec(3)], "poison")
        db.commit()
    assert res["new_ads"] == 2 and len(res["errors"]) == 1 and ads() == ["1", "3"]


# #2 / #3 — SSR fallback is page 1 only (partial), never the end; Blocked / RateLimited stay typed
def connector(monkeypatch, search, ssr=None):
    c = MetaLibraryConnector({})
    monkeypatch.setattr(c, "authenticate", lambda: None)
    monkeypatch.setattr(c.bucket, "take", lambda: None)
    c._col = SimpleNamespace(client=SimpleNamespace(search_ads=search))
    if ssr:
        monkeypatch.setattr(M, "ssr_search", ssr)
    return c


def graphql_down(**kw):
    raise RuntimeError("graphql 500")


def test_ssr_fallback_is_partial_and_pauses(monkeypatch):
    calls = []
    def ssr(*a):
        calls.append(a)
        return [raw(1), raw(2)]
    c = connector(monkeypatch, graphql_down, ssr)
    recs, st = c.fetch_page(FetchParams(), "US")
    assert len(recs) == 2 and st["page_status"] == "partial" and not st["done"] and st["cursor"] is None
    p = FetchParams(countries=["US"], limit=100)
    assert len(list(c.fetch_ads(p))) == 2 and len(calls) == 2  # one SSR page, then the loop pauses
    assert p.extra["resume_out"]["US"]["done"] is False  # the next run tries GraphQL again


def test_ssr_access_challenge_is_blocked(monkeypatch):
    def challenge(*a):
        raise Blocked("Meta access challenge; user action required")
    with pytest.raises(Blocked):
        connector(monkeypatch, graphql_down, challenge).fetch_page(FetchParams(), "US")


def test_rate_limit_keeps_the_stored_cursor(monkeypatch):
    seen = []
    def limited(**kw):
        seen.append(kw["cursor"])
        return {"ads": [], "page_info": {}, "rate_limited": True}, None
    p = FetchParams(countries=["US"], extra={"resume": {"US": {"cursor": "deep-cursor"}}})
    with pytest.raises(RateLimited):
        list(connector(monkeypatch, limited).fetch_ads(p))
    assert seen == ["deep-cursor"]  # not retried from page 1 as if the cursor had expired


# #5 — a worker that lost its lease commits neither ads nor the cursor nor the connector status
def test_stale_lease_commits_nothing(monkeypatch):
    class Fake:
        def fetch_ads(self, params):
            params.extra["resume_out"] = {"US": {"cursor": "stale", "done": False}}
            yield rec(1)
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True,
                      config={}, health="healthy")
        db.add(c); db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US"], cursor={"pages": {"US": {"cursor": "fresh"}}})
        db.add(q); db.commit(); db.expunge(q)
        cid = c.id
    key = realtime.job_key(cid, q)
    old = realtime.claim_crawl(cid, q)
    with SessionLocal.begin() as db:  # the lease moved on (expired, recovered, re-claimed by another worker)
        db.get(cq.CrawlJob, key).run_token = "someone-else"
    r = realtime.run_connector(cid, query=q, lease=(key, old))
    assert r["lease_lost"] and ads() == []
    with SessionLocal() as db:
        assert db.get(TrackedQuery, q.id).cursor["pages"]["US"]["cursor"] == "fresh"
        assert db.get(Connector, cid).health == "healthy"


# #8 — server-side crawls write no receipts; receipts and crawl history have a TTL
def test_crawl_paths_write_no_receipts(monkeypatch):
    class Fake:
        def fetch_ads(self, params):
            yield rec(1)
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True,
                      config={"keywords": "blender"})
        db.add(c); db.commit()
        cid = c.id
    assert "error" not in realtime.run_connector(cid)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IngestBatch)) == 0
    assert ads() == ["1"]
    from app.retention import table_ttls
    assert table_ttls()["collector_ingest_batches"] == ("created_at", 30) and table_ttls()["crawl_runs"][1] == 90


# #10 — a re-score failure after the data committed is logged, not a failed crawl
def test_rescore_failure_is_not_a_crawl_failure(monkeypatch):
    class Fake:
        def fetch_ads(self, params):
            yield rec(1)
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    def boom(*a):
        raise RuntimeError("rescore bug")
    monkeypatch.setattr(realtime, "after_business_events", boom)
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True,
                      config={"keywords": "blender"})
        db.add(c); db.commit()
        cid = c.id
    r = realtime.run_connector(cid)
    assert "error" not in r and r["new_ads"] == 1
    with SessionLocal() as db:
        assert db.get(Connector, cid).health == "healthy"


# #11 — one TikTok country failing keeps the others' ads; all failing surfaces the source state
def test_tiktok_country_failure_is_isolated(monkeypatch):
    from app.collector.adapters.ad_libraries import TikTokTopAdsHeadlessConnector as T
    page = {"data": {"materials": [{"id": "m1", "objective_key": "campaign_objective_conversion", "ad_title": "Blender",
                                    "video_info": {}, "country_code": ["PH"]}]}}
    t = T({})
    monkeypatch.setattr(t.bucket, "take", lambda: None)
    monkeypatch.setattr(t, "_scrape", lambda country, period: (_ for _ in ()).throw(Blocked("CAPTCHA")) if country == "US" else [page])
    assert len(list(t.fetch_ads(FetchParams(countries=["US", "PH"])))) == 1
    monkeypatch.setattr(t, "_scrape", lambda country, period: (_ for _ in ()).throw(Blocked("CAPTCHA")))
    with pytest.raises(Blocked):
        list(t.fetch_ads(FetchParams(countries=["US", "PH"])))


# #6 — the extension endpoint stores the valid items of a mixed batch; only an all-invalid batch is refused
def test_extension_batch_with_some_items_missing_ids():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import require_token, router
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    r = client.post("/api/ingest/meta-library", json={"items": [raw(1), {"no": "id"}], "idempotency_key": "mixed"})
    assert r.status_code == 200 and r.json()["new_ads"] == 1 and r.json()["invalid"] == 1
    assert client.post("/api/ingest/meta-library", json={"items": [{}], "idempotency_key": "none"}).status_code == 422
