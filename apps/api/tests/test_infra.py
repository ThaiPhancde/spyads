"""Ops package: the worker's heartbeat reaches the API through app_settings, and retention prunes only old rows."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app import realtime
from app.behavior import BehaviorEvent
from app.db import SessionLocal
from app.models import SearchJob
from app.retention import prune_tables
from app.visual.models import ProductImage
from app.workspace.models import UserSession
from test_allocation import fresh, users  # noqa: F401 — fresh: autouse disposable DB per test
from test_workspace import client


def test_heartbeat_round_trip_feeds_health_and_scheduler_status(monkeypatch):
    users()
    liveness = datetime(2026, 10, 26, 8, 0)
    monkeypatch.setattr(realtime, "_running", {"spy", "c:1:"})
    monkeypatch.setattr(realtime, "_last_liveness", liveness)
    realtime.write_heartbeat()
    realtime.write_heartbeat()  # second tick overwrites the same row
    with SessionLocal() as db:
        hb = realtime.read_heartbeat(db)
    assert hb["running"] == ["c:1:", "spy"] and hb["last"]["liveness"] == liveness.isoformat() and 0 <= hb["age_s"] < 60

    monkeypatch.setattr(realtime, "_running", set())  # this (API) process's RAM must not be what the endpoint reports
    assert 0 <= client("mkt-a@example.test").get("/api/health").json()["worker_heartbeat_age_s"] < 60
    monkeypatch.setenv("ADMIN_TOKEN", "svc")  # /api/collector/ is a system area: service token only, never an MKT session
    s = client().get("/api/collector/scheduler", headers={"x-admin-token": "svc"}).json()  # conftest: DISABLE_SCHEDULER=1
    assert s["process"] == "worker" and s["running"] == ["c:1:", "spy"] and s["last_liveness"] == liveness.isoformat()


def test_retention_deletes_old_rows_and_keeps_fresh_ones():
    a, b, _ = users()
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        db.add_all([UserSession(token_hash="old", user_id=a, csrf_hash="x", expires_at=now - timedelta(minutes=1)),
                    UserSession(token_hash="live", user_id=b, csrf_hash="x", expires_at=now + timedelta(days=7)),
                    SearchJob(query="old", created_at=now - timedelta(days=61)), SearchJob(query="new", created_at=now - timedelta(days=1)),
                    BehaviorEvent(user_id=a, kind="ad_view", dedupe_key="old", created_at=now - timedelta(days=400)),
                    BehaviorEvent(user_id=a, kind="ad_view", dedupe_key="decay", created_at=now - timedelta(days=300)),
                    ProductImage(source="creative", source_id=987654, processing_status="done")])  # its creative was deleted
    with SessionLocal() as db:
        assert prune_tables(db, now, dry_run=True) == {"user_sessions": 1, "search_jobs": 1, "mkt_behavior_events": 1,
                                                       "product_images_orphan": 1}
        assert len(db.scalars(select(SearchJob)).all()) == 2  # dry run deletes nothing
        prune_tables(db, now)
        db.commit()
        assert db.scalars(select(UserSession.token_hash)).all() == ["live"]
        assert db.scalars(select(SearchJob.query)).all() == ["new"]
        assert db.scalars(select(BehaviorEvent.dedupe_key)).all() == ["decay"]  # inside the preference window
        assert db.scalars(select(ProductImage)).all() == []


def test_light_jobs_do_not_block_scans_or_liveness(monkeypatch):
    # visual / price / engagement run every minute: counting them used to starve liveness and the source syncs
    monkeypatch.setattr(realtime, "_running", {"visual", "price", "engagement", "naming", "q:1:17"})
    assert realtime._heavy() == 1 < realtime.MAX_PARALLEL_JOBS
    monkeypatch.setattr(realtime, "_running", {"c:20:", "q:1:17", "liveness", "visual"})
    assert realtime._heavy() == 3
