"""Phase 3: persistent crawl jobs — lease, heartbeat, retry backoff, recovery after a worker dies, history + checkpoints."""
import threading
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import crawl_queue as cq, realtime
from app.crawl_queue import CrawlJob, CrawlRun
from app.db import IS_SQLITE, SessionLocal
from app.models import Connector, TrackedQuery

T0 = datetime(2026, 11, 3, 12, 0, 0)


@pytest.fixture(autouse=True)
def isolated(clean_catalog):
    yield


def job(key="c:1:"):
    with SessionLocal() as db:
        return db.get(CrawlJob, key)


def runs(key="c:1:"):
    with SessionLocal() as db:
        return list(db.scalars(select(CrawlRun).where(CrawlRun.job_key == key).order_by(CrawlRun.id)))


def test_one_lease_at_a_time_and_history_with_checkpoints():
    t = cq.claim("q:1:7", "q", 1, 7, checkpoint={"pages": {"US": {"cursor": "a"}}}, worker="w1", now=T0)
    assert t and cq.claim("q:1:7", "q", 1, 7, worker="w2", now=T0 + timedelta(seconds=30)) is None
    assert cq.heartbeat("q:1:7", t, now=T0 + timedelta(seconds=100))
    # the lease was extended: still held after the original LEASE_S
    assert cq.claim("q:1:7", "q", 1, 7, worker="w2", now=T0 + timedelta(seconds=cq.LEASE_S + 10)) is None
    assert cq.finish("q:1:7", t, {"fetched": 12, "new_ads": 3}, checkpoint={"pages": {"US": {"cursor": "b"}}}, now=T0 + timedelta(seconds=150))
    j, [r] = job("q:1:7"), runs("q:1:7")
    assert j.status == "idle" and j.run_token is None and j.checkpoint["pages"]["US"]["cursor"] == "b"
    assert (r.status, r.worker, r.fetched, r.new_ads) == ("ok", "w1", 12, 3)
    assert r.checkpoint_before["pages"]["US"]["cursor"] == "a" and r.checkpoint_after["pages"]["US"]["cursor"] == "b"


def test_dead_worker_is_recovered_and_fenced():
    old = cq.claim("c:1:", "c", 1, worker="w1", now=T0)  # w1 then dies: no heartbeat, no finish
    later = T0 + timedelta(seconds=cq.LEASE_S + 1)
    assert cq.recover(later) == 1
    j = job()
    assert (j.status, j.retry_count, j.last_error) == ("retry_wait", 1, cq.LOST) and runs()[0].status == "lost"
    assert cq.claim("c:1:", "c", 1, worker="w2", now=later) is None  # a lost run backs off like any failure
    later = j.next_retry_at
    assert "c:1:" in cq.retry_due(later)  # then due ahead of its regular schedule
    new = cq.claim("c:1:", "c", 1, worker="w2", now=later)
    assert new and new != old
    # the stale worker wakes up: none of its writes reach the job
    assert not cq.heartbeat("c:1:", old, now=later) and not cq.finish("c:1:", old, now=later) and not cq.fail("c:1:", old, "x", now=later)
    assert cq.finish("c:1:", new, now=later)
    assert [r.status for r in runs()] == ["lost", "ok"]


def test_expired_lease_seen_by_claim_counts_as_lost():
    cq.claim("c:1:", "c", 1, worker="w1", now=T0)
    later = T0 + timedelta(seconds=cq.LEASE_S + 1)
    assert cq.claim("c:1:", "c", 1, worker="w2", now=later) is None  # same as recover(): lost + backoff
    assert [r.status for r in runs()] == ["lost"] and job().status == "retry_wait"
    assert cq.claim("c:1:", "c", 1, worker="w2", now=job().next_retry_at)


def test_crash_loop_is_capped():
    """A crawl that kills its worker every time: backoff, then `error` with a cool-down the schedule cannot skip."""
    now = T0
    for n in range(1, cq.MAX_RETRIES + 2):
        assert cq.claim("c:1:", "c", 1, now=now)
        now += timedelta(seconds=cq.LEASE_S + 1)
        cq.recover(now)
        j = job()
        if n <= cq.MAX_RETRIES:
            assert (j.status, j.retry_count) == ("retry_wait", n) and j.next_retry_at == now + cq.backoff(n)
            now = j.next_retry_at
    assert (j.status, j.retry_count) == ("error", 0) and j.next_retry_at == now + timedelta(seconds=cq.RETRY_CAP_S)
    assert cq.claim("c:1:", "c", 1, now=now + timedelta(minutes=30)) is None  # due by schedule, still cooling down
    assert cq.claim("c:1:", "c", 1, now=j.next_retry_at)
    assert [r.status for r in runs()].count("lost") == cq.MAX_RETRIES + 1


def test_retry_backoff_then_error_then_success_resets():
    now = T0
    for n in range(1, cq.MAX_RETRIES + 1):
        t = cq.claim("c:1:", "c", 1, now=now)
        assert cq.fail("c:1:", t, f"boom {n}", now=now)
        j = job()
        assert (j.status, j.retry_count) == ("retry_wait", n) and j.next_retry_at == now + cq.backoff(n)
        assert cq.claim("c:1:", "c", 1, now=j.next_retry_at - timedelta(seconds=1)) is None  # still backing off
        now = j.next_retry_at
    assert cq.backoff(2) == 2 * cq.backoff(1) and cq.backoff(50).total_seconds() == cq.RETRY_CAP_S
    t = cq.claim("c:1:", "c", 1, now=now)
    cq.fail("c:1:", t, "final", now=now)
    j = job()
    assert (j.status, j.retry_count, j.next_retry_at, j.last_error) == ("error", 0, None, "final")
    assert "c:1:" not in cq.retry_due(now + timedelta(days=1))  # the regular schedule takes over
    t = cq.claim("c:1:", "c", 1, now=now)
    assert [r.attempt for r in runs()] == [0, 1, 2, 3, 0]
    cq.finish("c:1:", t, now=now)
    assert (job().status, job().retry_count, job().last_error) == ("idle", 0, None)


def test_leased_context_heartbeats(monkeypatch):
    monkeypatch.setattr(cq, "HEARTBEAT_S", 0.05)
    t = cq.claim("c:1:", "c", 1)
    first = job().heartbeat_at
    with cq.leased("c:1:", t):
        time.sleep(0.3)
    assert job().heartbeat_at > first and job().lease_until > datetime.utcnow()


@pytest.mark.skipif(IS_SQLITE, reason="row locks / concurrent inserts need PostgreSQL")
def test_concurrent_claims_on_postgres():
    # a brand-new key claimed by 8 workers at once: exactly one wins (insert race → IntegrityError → None)
    out, go = [], threading.Barrier(8)
    def worker(i):
        go.wait()
        out.append(cq.claim("c:9:", "c", 9, worker=f"w{i}"))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for t in out if t) == 1
    # a row locked by another claimer's open transaction is skipped, never waited on (SKIP LOCKED)
    cq.finish("c:9:", next(t for t in out if t))
    with SessionLocal.begin() as db:
        db.scalar(select(CrawlJob).where(CrawlJob.key == "c:9:").with_for_update())
        res = []
        th = threading.Thread(target=lambda: res.append(cq.claim("c:9:", "c", 9, worker="other")))
        th.start(); th.join(timeout=10)
        assert not th.is_alive() and res == [None]


def setup_query():
    with SessionLocal() as db:
        c = Connector(name="Meta", provider="meta", kind="browser", group="transparency", adapter="meta_library", enabled=True, config={})
        db.add(c); db.flush()
        q = TrackedQuery(connector_id=c.id, query="blender", countries=["US"], cursor={"pages": {"US": {"cursor": "p1"}}})
        db.add(q); db.commit()
        db.expunge(q)
        return c.id, q


def test_scheduler_wrapper_records_failure_then_cursor(monkeypatch):
    cid, q = setup_query()
    key = realtime.job_key(cid, q)
    monkeypatch.setattr(realtime, "run_connector", lambda cid, query=None, **kw: {"error": "Meta access challenge"})
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    assert (job(key).status, job(key).last_error) == ("retry_wait", "Meta access challenge")
    def ok(cid, query=None, **kw):
        with SessionLocal() as db:
            db.get(TrackedQuery, query.id).cursor = {"pages": {"US": {"cursor": "p2"}}}
            db.commit()
        return {"fetched": 5, "new_ads": 2}
    monkeypatch.setattr(realtime, "run_connector", ok)
    with SessionLocal.begin() as db:  # skip the backoff
        db.get(CrawlJob, key).next_retry_at = datetime.utcnow()
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    j, rs = job(key), runs(key)
    assert j.status == "idle" and j.checkpoint["pages"]["US"]["cursor"] == "p2"
    assert [(r.status, r.attempt) for r in rs] == [("error", 0), ("ok", 1)]
    assert rs[1].checkpoint_before["pages"]["US"]["cursor"] == "p1"
    assert key not in realtime._running


def test_wrapper_exception_is_a_failure_not_a_stuck_lease(monkeypatch):
    cid, q = setup_query()
    key = realtime.job_key(cid, q)
    def crash(cid, query=None, **kw):
        raise RuntimeError("worker bug")
    monkeypatch.setattr(realtime, "run_connector", crash)
    realtime._job_wrapper(key, cid, q, realtime.claim_crawl(cid, q))
    assert job(key).status == "retry_wait" and job(key).run_token is None and "worker bug" in job(key).last_error


def test_manual_run_never_duplicates_a_running_crawl(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.intel import router
    cid, q = setup_query()
    started = threading.Event()
    monkeypatch.setattr(realtime, "run_connector", lambda cid, query=None, **kw: started.set() or {"fetched": 0})
    assert realtime.claim_crawl(cid)  # the worker holds the connector sync
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    assert client.post(f"/api/collector/connectors/{cid}/sync").json() == {"queued": False, "already_running": True}
    r = client.post(f"/api/collector/queries/{q.id}/run").json()  # another key: runs
    assert r == {"queued": True, "already_running": False} and started.wait(5)
