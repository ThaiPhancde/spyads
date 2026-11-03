"""Phase 5: Data & Connectors KPIs measured from real crawl runs, search jobs and creatives."""
from datetime import datetime, timedelta

import pytest

from app import ingest, realtime
from app.collector.adapters.meta import map_library_ad
from app.collector.kpis import collector_kpis, cursor_summary
from app.db import SessionLocal
from app.models import Connector, Creative, SearchJob, TrackedQuery


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def rec(i):
    return map_library_ad({"ad_archive_id": str(i), "page_id": "p", "collation_id": f"g{i}",
                           "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}, "meta_library", "US")


class FakeMeta:
    """Two source pages (one empty) of three ads, the way MetaLibraryConnector counts them."""
    def fetch_ads(self, params):
        self.pages_fetched, self.empty_pages = 2, 1
        params.extra["resume_out"] = {"US": {"cursor": "next", "done": False}, "GB": {"cursor": None, "done": True}}
        yield from (rec(i) for i in (1, 2, 3))


def test_scheduled_runs_report_pages_accepted_duplicates_cursor(monkeypatch):
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: FakeMeta())
    with SessionLocal() as db:
        c = Connector(name="Meta Ad Library", provider="meta", kind="browser", group="transparency", adapter="meta_library",
                      enabled=True, config={})
        db.add(c); db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US", "GB"])
        db.add(q); db.commit(); db.expunge(q)
        cid = c.id
    for _ in range(2):  # the second run sees the same three ads: all duplicates
        realtime._job_wrapper(realtime.job_key(cid, q), cid, q, realtime.claim_crawl(cid, q))
    with SessionLocal() as db:
        k = collector_kpis(db, 24)
    row = next(r for r in k["connectors"] if r["id"] == cid)
    s = row["scheduled"]
    assert (s["runs"], s["ok"], s["pages"], s["empty_pages"], s["fetched"]) == (2, 2, 4, 2, 6)
    assert (s["new_ads"], s["accepted"], s["duplicates"], s["invalid"]) == (3, 6, 3, 0)
    assert row["health"] == "healthy" and row["network"] == "meta"
    [job] = row["jobs"]
    assert job["status"] == "idle" and job["retry_count"] == 0
    assert job["cursor"] == {"countries": 2, "resumable": 1, "done": 1, "at": job["cursor"]["at"]}
    assert k["totals"]["accepted"] == 6 and k["totals"]["unhealthy"] == 0


def test_retry_and_health_show_up(monkeypatch):
    class Broken:
        def fetch_ads(self, params):
            raise RuntimeError("source down")
            yield
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Broken())
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True,
                      config={"keywords": "blender"})
        db.add(c); db.commit()
        cid = c.id
    realtime._job_wrapper(realtime.job_key(cid), cid, None, realtime.claim_crawl(cid))
    with SessionLocal() as db:
        k = collector_kpis(db, 24)
    row = next(r for r in k["connectors"] if r["id"] == cid)
    assert row["health"] == "error" and "source down" in row["last_error"]
    assert row["scheduled"]["error"] == 1 and row["retry_wait"] == 1
    assert row["jobs"][0]["retry_count"] == 1 and row["jobs"][0]["next_retry_at"]
    assert k["totals"]["unhealthy"] == 1 and k["totals"]["retry_wait"] == 1


def test_live_search_window_and_media_availability():
    now = datetime.utcnow()
    with SessionLocal() as db:
        db.add(Connector(name="Meta Ad Library", provider="meta", kind="browser", group="transparency", adapter="meta_library", config={}))
        db.add_all([SearchJob(query="a", sources={"Meta Ad Library": {"fetched": 40, "new": 7, "failed": 1}}, created_at=now),
                    SearchJob(query="b", sources={"Meta Ad Library": {"fetched": 10, "new": 0, "failed": 0}}, created_at=now),
                    SearchJob(query="old", sources={"Meta Ad Library": {"fetched": 999}}, created_at=now - timedelta(days=3))])
        db.add_all([Creative(type="video", source_url=f"u{i}", network="meta", status=st, thumb_key=tk)
                    for i, (st, tk) in enumerate([("stored", "t"), ("pending", "t"), ("pending", None), ("expired", None)])])
        db.commit()
        k = collector_kpis(db, 24)
    [row] = k["connectors"]
    assert row["live_search"] == {"jobs": 2, "fetched": 50, "new": 7, "failed": 1}
    m = k["media"]["meta"]
    assert (m["total"], m["stored"], m["pending"], m["expired"], m["thumb"], m["available_pct"]) == (4, 1, 2, 1, 2, 50.0)


def test_cursor_summary_shapes():
    assert cursor_summary(None) is None and cursor_summary({"pages": "x"}) is None
    assert cursor_summary({"at": "t", "pages": {"US": {"cursor": "c"}, "VN": {"done": True}}}) == \
        {"countries": 2, "resumable": 1, "done": 1, "at": "t"}


def test_kpi_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import router
    app = FastAPI(); app.include_router(router)
    r = TestClient(app).get("/api/collector/kpis?hours=168")
    assert r.status_code == 200 and r.json()["window_hours"] == 168 and r.json()["browser_capacity"] >= 1
    assert TestClient(app).get("/api/collector/kpis?hours=0").status_code == 422
