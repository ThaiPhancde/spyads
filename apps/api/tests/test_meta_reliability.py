from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Ad, AdSource, IngestBatch, MetaCollation, SearchJob, Connector
from app.collector.batches import ingest_batch, BatchConflict
from app.collector.adapters.meta import MetaLibraryConnector, map_library_ad
from app.collector.base import FetchParams, ConnectorError
from app import ingest, realtime


@pytest.fixture(autouse=True)
def isolated(clean_catalog, monkeypatch):
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr("app.media.enqueue", lambda *a: None)
    monkeypatch.setattr("app.services.engine.rescore_products", lambda *a: 0)


def record(id="1", group="g"):
    return map_library_ad({"ad_archive_id": id, "page_id": "p", "collation_id": group,
        "snapshot": {"title": "Portable electric blender", "body": {"text": "Buy blender now"}}}, "meta_library", "US")


def test_atomic_failure_and_retry(monkeypatch):
    original = ingest._upsert_ad
    def fail(db, rec, *args):
        if rec.source_ad_id == "2":
            raise RuntimeError("injected write failure")
        return original(db, rec, *args)
    monkeypatch.setattr(ingest, "_upsert_ad", fail)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):
            ingest_batch(db, [record(), record("2", "g2")], "batch")
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Ad)) == 0
        assert db.get(IngestBatch, "batch") is None
        assert db.scalar(select(func.count()).select_from(MetaCollation)) == 0
        monkeypatch.setattr(ingest, "_upsert_ad", original)
        result = ingest_batch(db, [record(), record("2", "g2")], "batch")
        db.commit()
        assert result["new_ads"] == 2
        assert ingest_batch(db, [record(), record("2", "g2")], "batch") == result
        assert db.scalar(select(func.count()).select_from(Ad)) == 2
        with pytest.raises(BatchConflict):
            ingest_batch(db, [record("3")], "batch")


def test_collation_across_sessions_and_jobs():
    for key, rec in [("a", record()), ("b", record("2")), ("c", record("2")), ("d", record("2", None))]:
        with SessionLocal() as db:
            ingest_batch(db, [rec], key)
            db.commit()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Ad)) == 1
        assert db.scalar(select(func.count()).select_from(AdSource)) == 2
        assert db.scalar(select(func.count()).select_from(MetaCollation)) == 1


def connector(monkeypatch, response, cursor):
    obj = MetaLibraryConnector({})
    monkeypatch.setattr(obj, "authenticate", lambda: None)
    obj._col = SimpleNamespace(client=SimpleNamespace(search_ads=lambda **kw: (response, cursor)))
    monkeypatch.setattr(obj.bucket, "take", lambda: None)
    return obj


@pytest.mark.parametrize("response", [{"ads": [], "error": "bad"}, {"ads": [], "rate_limited": True},
    {"ads": [], "session_expired": True}, {}, {"ads": [], "page_info": {"has_next_page": True}}])
def test_source_errors_are_not_end(monkeypatch, response):
    with pytest.raises(ConnectorError):
        connector(monkeypatch, response, None).fetch_page(FetchParams(), "US")


def test_empty_page_is_not_end(monkeypatch):
    c = connector(monkeypatch, {"ads": [], "page_info": {"has_next_page": True}}, "next")
    records, state = c.fetch_page(FetchParams(), "US")
    assert not records and not state["done"] and state["page_status"] == "empty"
    c = connector(monkeypatch, {"ads": [], "page_info": {"has_next_page": False}}, None)
    assert c.fetch_page(FetchParams(), "US")[1]["page_status"] == "end"


def test_limit_never_drops_tail_of_a_page(monkeypatch):
    monkeypatch.setattr(MetaLibraryConnector, "fetch_page", lambda *a: ([record(), record("2")], {"cursor": "next", "done": False}))
    p = FetchParams(limit=1, countries=["US"])
    assert len(list(MetaLibraryConnector({}).fetch_ads(p))) == 2


def test_search_failed_batch_never_advances_checkpoint(monkeypatch):
    class Fake:
        def fetch_page(self, params, country, state):
            n = int(state.get("cursor") or 0) + 1
            return [record(str(n), str(n))], {"cursor": str(n), "done": n == 3}
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    original = ingest._upsert_ad
    def fail(db, rec, *args):
        if rec.source_ad_id == "2":
            raise RuntimeError("disk unavailable")
        return original(db, rec, *args)
    monkeypatch.setattr(ingest, "_upsert_ad", fail)
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True, config={})
        db.add(c); db.flush()
        job = SearchJob(query="blender", countries=["US"], adapters=["meta_library"], limit=3, status="running")
        db.add(job); db.commit()
        jid, cid = job.id, c.id
    realtime.run_search(jid)
    with SessionLocal() as db:
        job = db.get(SearchJob, jid)
        assert job.state[f"{cid}:US"]["cursor"] == "1"
        assert job.status == "error" and job.has_more
        assert list(db.scalars(select(Ad.external_id))) == ["1"]
        assert db.get(Connector, cid).health == "error"
    monkeypatch.setattr(ingest, "_upsert_ad", original)
    realtime.run_search(jid, more=3)
    with SessionLocal() as db:
        job = db.get(SearchJob, jid)
        assert job.state[f"{cid}:US"]["cursor"] == "3"
        assert job.status == "done"
        assert set(db.scalars(select(Ad.external_id))) == {"1", "2", "3"}


def test_migration_repeatable():
    from app.meta_collector_migrate import migrate
    from app.db import engine
    migrate(engine); migrate(engine)


def test_api_retry_and_key_conflict():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import router, require_token
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    body = {"items": [record().raw_source], "idempotency_key": "api-batch"}
    first = client.post("/api/ingest/meta-library", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/ingest/meta-library", json=body).json() == first.json()
    body["items"] = [record("2").raw_source]
    assert client.post("/api/ingest/meta-library", json=body).status_code == 409
    body["items"] = [{}]
    assert client.post("/api/ingest/meta-library", json=body).status_code == 422


def test_commit_failure_rolls_back_receipt_and_ads(monkeypatch):
    from sqlalchemy import event
    with SessionLocal() as db:
        ingest_batch(db, [record()], "commit-failure")
        def fail(session):
            raise RuntimeError("commit unavailable")
        event.listen(db, "before_commit", fail)
        with pytest.raises(RuntimeError):
            db.commit()
        db.rollback()
        event.remove(db, "before_commit", fail)
        assert db.get(IngestBatch, "commit-failure") is None
        assert db.scalar(select(func.count()).select_from(Ad)) == 0
        assert ingest_batch(db, [record()], "commit-failure")["new_ads"] == 1
        db.commit()


def test_scheduled_failure_keeps_tracked_cursor(monkeypatch):
    from app.models import TrackedQuery
    class Fake:
        def fetch_ads(self, params):
            params.extra["resume_out"] = {"US": {"cursor": "later", "done": False}}
            yield record()
            yield record("2", "g2")
    monkeypatch.setattr(realtime.ConnectorFactory, "get", lambda *a: Fake())
    original = ingest._upsert_ad
    def fail(db, rec, *args):
        if rec.source_ad_id == "2": raise RuntimeError("injected")
        return original(db, rec, *args)
    monkeypatch.setattr(ingest, "_upsert_ad", fail)
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", config={})
        db.add(c); db.flush()
        q = TrackedQuery(query="blender", countries=["US"], cursor={"pages": {"US": {"cursor": "before"}}})
        db.add(q); db.commit()
        cid, qid = c.id, q.id
        db.expunge(q)
    assert "error" in realtime.run_connector(cid, query=q)
    with SessionLocal() as db:
        assert db.get(TrackedQuery, qid).cursor["pages"]["US"]["cursor"] == "before"
        assert db.scalar(select(func.count()).select_from(Ad)) == 0


def test_no_internal_commit_or_event_before_checkpoint(monkeypatch):
    from sqlalchemy import event
    from app import events
    delivered = []
    monkeypatch.setattr(events, "_fanout", delivered.append)
    with SessionLocal() as db:
        commits = []
        def committed(session):
            if not session.in_nested_transaction(): commits.append(True)
        event.listen(db, "after_commit", committed)
        ingest_batch(db, [record()], "atomic-boundary")
        assert commits == []
        assert delivered == []
        db.commit()
        assert commits == [True]
        assert delivered


def test_stopped_sibling_never_stops_running_group():
    live, stopped = record(), record("2")
    live.active, stopped.active = True, False
    with SessionLocal() as db:
        ingest_batch(db, [live, stopped], "siblings")
        db.commit()
        assert db.scalar(select(Ad.is_active)) is True
