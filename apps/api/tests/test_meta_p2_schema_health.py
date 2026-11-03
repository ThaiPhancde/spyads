"""P2 — GraphQL schema health and pagination diagnostics (docs/META_RELIABILITY_GAP_AUDIT.md §P2).
A changed Meta response shape must become an alert, never a silent zero; runs record requests / cycles / restarts / drift."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import ingest, realtime
from app.collector.adapters import meta as M
from app.collector.adapters.meta import MetaLibraryConnector, map_library_ad
from app.collector.base import ConnectorError, FetchParams, SchemaChanged
from app.collector.batches import ingest_batch
from app.crawl_queue import CrawlRun
from app.db import SessionLocal
from app.models import Ad, Connector, Event, TrackedQuery


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def raw(i, collation=True):
    r = {"ad_archive_id": str(i), "page_id": "p", "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}
    if collation:
        r["collation_id"] = f"g{i}"
    return r


def library(monkeypatch, reply):
    """A Meta connector whose GraphQL client returns `reply(cursor)` = (resp, next_cursor) or raises."""
    c = MetaLibraryConnector({})
    monkeypatch.setattr(c, "authenticate", lambda: None)
    monkeypatch.setattr(c.bucket, "take", lambda: None)
    c._col = SimpleNamespace(client=SimpleNamespace(search_ads=lambda **kw: reply(kw["cursor"])))
    return c


def resp(ads, raw_data, has_next=True):
    return {"ads": ads, "page_info": {"has_next_page": has_next}, "raw": raw_data}


# ---------------------------------------------------------------- shape change → alert (+ salvage), never a silent zero
def test_renamed_results_field_is_salvaged_and_flagged(monkeypatch):
    """`collated_results` renamed: the library returns ads=[] with a valid page_info — the old code read it as an empty page."""
    data = {"data": {"ad_library_main": {"search_results_connection": {"edges": [{"node": {"collated_results_v2": [raw(1), raw(2)]}}]}}}}
    c = library(monkeypatch, lambda cur: (resp([], data), "A"))
    recs, st = c.fetch_page(FetchParams(), "US", {})
    assert [r.source_ad_id for r in recs] == ["1", "2"] and st["page_status"] == "page" and c.schema_drift == 1


def test_results_without_any_known_ad_are_schema_changed_not_empty(monkeypatch):
    data = {"data": {"ad_library_main": {"search_results_connection": {"edges": [{"node": {"archive": {"id": "1"}}}]}}}}
    c = library(monkeypatch, lambda cur: (resp([], data), "A"))
    with pytest.raises(SchemaChanged):
        c.fetch_page(FetchParams(), "US", {})
    assert c.schema_drift == 1


def test_a_truly_empty_page_with_a_next_cursor_stays_empty(monkeypatch):
    data = {"data": {"ad_library_main": {"search_results_connection": {"edges": []}}}}
    c = library(monkeypatch, lambda cur: (resp([], data), "A"))
    recs, st = c.fetch_page(FetchParams(), "US", {})
    assert recs == [] and st["page_status"] == "empty" and not st["done"] and getattr(c, "schema_drift", 0) == 0


def test_a_non_json_response_counts_as_a_schema_change(monkeypatch):
    """Meta streaming newline-delimited JSON makes the library's json.loads fail: deeper pages error, page 1 falls back to SSR."""
    def bad(cur):
        raise json.JSONDecodeError("Extra data", '{"a":1}\n{"b":2}', 8)
    c = library(monkeypatch, bad)
    with pytest.raises(ConnectorError):
        c.fetch_page(FetchParams(), "US", {"cursor": "A"})
    monkeypatch.setattr(M, "ssr_search", lambda *a: [raw(1)])
    recs, st = c.fetch_page(FetchParams(), "US", {})
    assert len(recs) == 1 and st["page_status"] == "partial" and c.schema_drift == 2


def test_schema_change_is_never_mistaken_for_an_expired_cursor(monkeypatch):
    """fetch_ads restarts from page 1 when Meta refuses a stored cursor; a schema change must surface instead."""
    data = {"data": {"edges": [{"node": {"x": 1}}]}}
    c = library(monkeypatch, lambda cur: (resp([], data), "B"))
    with pytest.raises(SchemaChanged):
        list(c.fetch_ads(FetchParams(countries=["US"], extra={"resume": {"US": {"cursor": "A"}}})))
    assert getattr(c, "cursor_restarts", 0) == 0


def test_record_without_collation_id_is_stored():
    with SessionLocal() as db:
        res = ingest_batch(db, [map_library_ad(raw(7, collation=False), "meta_library", "US")])
        db.commit()
    assert res["new_ads"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Ad)) == 1


# ---------------------------------------------------------------- diagnostics: requests, cycles, restarts, drift
def test_requests_count_failed_attempts_and_restarts_are_counted(monkeypatch):
    pages = {"A": None, None: (resp([raw(1)], {}, has_next=False), None)}
    def reply(cur):
        if pages[cur] is None:
            raise RuntimeError("cursor refused")   # an expired stored cursor
        return pages[cur]
    c = library(monkeypatch, reply)
    got = list(c.fetch_ads(FetchParams(countries=["US"], extra={"resume": {"US": {"cursor": "A"}}})))
    assert [r.source_ad_id for r in got] == ["1"] and c.requests == 2 and c.pages_fetched == 1 and c.cursor_restarts == 1


def tracked():
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True, config={})
        db.add(c)
        db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US"])
        db.add(q)
        db.commit()
        db.expunge(q)
        return c.id, q


class Drifted:
    """Pages read with the fallback parser: data is fine, the source is not."""
    requests, pages_fetched, empty_pages, cursor_cycles, cursor_restarts, schema_drift = 3, 2, 0, 1, 0, 1

    def fetch_ads(self, params):
        params.extra.setdefault("resume_out", {})["US"] = {"cursor": "c2", "done": False}
        yield map_library_ad(raw(1), "meta_library", "US")


def test_salvaged_run_stores_data_but_flags_the_source_and_records_diagnostics(monkeypatch):
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Drifted())
    cid, q = tracked()
    key = realtime.job_key(cid, q)
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    with SessionLocal() as db:
        c = db.get(Connector, cid)
        assert c.health == "schema_changed" and "cấu trúc" in c.last_error
        assert db.scalar(select(func.count()).select_from(Ad)) == 1
        run = db.scalar(select(CrawlRun).where(CrawlRun.job_key == key))
        assert (run.status, run.requests, run.pages, run.cursor_cycles, run.cursor_restarts, run.schema_drift) == ("ok", 3, 2, 1, 0, 1)
        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == "SOURCE_SCHEMA_CHANGED")) == 1


def test_schema_change_failure_sets_its_own_health(monkeypatch):
    class Broken:
        def fetch_ads(self, params):
            raise SchemaChanged("Meta response shape changed")
            yield  # noqa
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Broken())
    cid, q = tracked()
    r = realtime.run_connector(cid, query=q)
    assert r["health"] == "schema_changed"


def test_kpis_sum_the_diagnostics(monkeypatch):
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Drifted())
    cid, q = tracked()
    realtime._job_wrapper(realtime.job_key(cid, q), cid, q, realtime.claim_crawl(cid, q))
    from app.collector.kpis import collector_kpis
    with SessionLocal() as db:
        t = collector_kpis(db, 24)["totals"]
    assert (t["requests"], t["cursor_cycles"], t["schema_drift"]) == (3, 1, 1) and t["unhealthy"] == 1


# ---------------------------------------------------------------- extension: a drift report becomes an alert
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import require_token, router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_extension_drift_report_flags_the_source():
    cid, _ = tracked()
    r = client().post("/api/ingest/meta-library/drift", json={"keys": ["ad_archive_id", "creative_v2", "page"], "path": "/ads/library/"})
    assert r.status_code == 200
    with SessionLocal() as db:
        c = db.get(Connector, cid)
        assert c.health == "schema_changed" and "creative_v2" in c.last_error
        ev = db.scalar(select(Event).where(Event.type == "SOURCE_SCHEMA_CHANGED"))
        assert ev.data["origin"] == "extension"


def test_extension_drift_report_is_bounded():
    big = {"keys": [f"k{i}" for i in range(500)], "path": "/x" * 500}
    assert client().post("/api/ingest/meta-library/drift", json=big).status_code == 422
