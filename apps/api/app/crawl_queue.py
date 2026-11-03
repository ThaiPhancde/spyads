"""Persistent crawl jobs (Meta collector upgrade, Phase 3): scheduled connector syncs and tracked queries.

One crawl_jobs row per scheduler key ("c:<connector>:" / "q:<connector>:<query>"). A worker claims it with a lease
(row lock FOR UPDATE SKIP LOCKED + a fresh run_token), a heartbeat thread extends the lease while the crawl runs, and
finish / fail only apply with the token that claimed it — a worker whose lease expired (crash, restart, stall) can no
longer write the job. An expired lease is recovered: its run is marked "lost" and the job retried at once.
Failures retry with exponential backoff (retry_count, next_retry_at) before the regular schedule takes over again.
Every run is kept in crawl_runs with the checkpoint (tracked-query cursor) before and after.

The scheduler still decides *when* a crawl is due (last_sync_at + every_minutes); this table decides *who* runs it.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, Integer, String, Text, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, SessionLocal

log = logging.getLogger(__name__)

LEASE_S = int(os.getenv("CRAWL_LEASE_S", "120"))
HEARTBEAT_S = max(1, LEASE_S // 4)
MAX_RETRIES = int(os.getenv("CRAWL_MAX_RETRIES", "3"))
RETRY_BASE_S, RETRY_CAP_S = 60, 3600
WORKER = f"{socket.gethostname()}:{os.getpid()}"


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # c = connector sync, q = tracked query
    connector_id: Mapped[int | None] = mapped_column(Integer, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="idle", index=True)  # idle / running / retry_wait / error / blocked
    run_token: Mapped[str | None] = mapped_column(String(48))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict | None] = mapped_column(JSON)  # last committed resume state (TrackedQuery.cursor)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(64), index=True)
    run_token: Mapped[str] = mapped_column(String(48), index=True)
    worker: Mapped[str] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(Integer, default=0)  # retry_count when claimed: 0 = scheduled run
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / ok / error / lost
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pages: Mapped[int | None] = mapped_column(Integer)  # source pages requested (paging sources only)
    empty_pages: Mapped[int | None] = mapped_column(Integer)
    fetched: Mapped[int | None] = mapped_column(Integer)  # records the source returned
    new_ads: Mapped[int | None] = mapped_column(Integer)
    accepted: Mapped[int | None] = mapped_column(Integer)  # stored: new + refreshed
    duplicates: Mapped[int | None] = mapped_column(Integer)  # valid records of an ad already in the library
    invalid: Mapped[int | None] = mapped_column(Integer)  # rejected by ingest validation
    excluded: Mapped[int | None] = mapped_column(Integer)  # dropped by the content filter (drama / movie / podcast…)
    error: Mapped[str | None] = mapped_column(Text)
    checkpoint_before: Mapped[dict | None] = mapped_column(JSON)
    checkpoint_after: Mapped[dict | None] = mapped_column(JSON)


def backoff(retry_count: int) -> timedelta:
    return timedelta(seconds=min(RETRY_CAP_S, RETRY_BASE_S * 2 ** max(0, retry_count - 1)))


LOST = "lease expired (worker stopped or stalled)"


def _lose(db, job: CrawlJob, now: datetime):
    """The run whose lease expired is lost. A lost run counts as a failed attempt: a crawl that kills or hangs its worker
    backs off like any failure, and after MAX_RETRIES waits RETRY_CAP_S — its last_sync_at never moved, so the regular
    schedule alone would pick it again on every tick."""
    db.execute(update(CrawlRun).where(CrawlRun.run_token == job.run_token, CrawlRun.status == "running")
               .values(status="lost", finished_at=now, error=LOST))
    job.retry_count += 1
    job.last_error = LOST
    if job.retry_count <= MAX_RETRIES:
        job.status, job.next_retry_at = "retry_wait", now + backoff(job.retry_count)
    else:
        job.status, job.next_retry_at, job.retry_count = "error", now + timedelta(seconds=RETRY_CAP_S), 0
    job.run_token, job.lease_owner, job.lease_until, job.updated_at = None, None, None, now


class LeaseLost(Exception):
    """This worker no longer holds the crawl: its writes must not land."""


def holds(db, key: str, token: str) -> bool:
    """Inside the caller's transaction, lock the job row and check the token: commit right after (the row lock keeps a
    recovering / claiming worker out until then), so a stale worker can never commit data or a checkpoint."""
    return db.scalar(select(CrawlJob.run_token).where(CrawlJob.key == key, CrawlJob.status == "running").with_for_update()) == token


def recover(now: datetime | None = None) -> int:
    """Running jobs whose lease ran out: the run is lost and retried after backoff. Called on worker start and every tick."""
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        jobs = db.scalars(select(CrawlJob).where(CrawlJob.status == "running", CrawlJob.lease_until < now)
                          .with_for_update(skip_locked=True)).all()
        for job in jobs:
            _lose(db, job, now)
    if jobs:
        log.warning("crawl queue: recovered %d job(s) with an expired lease", len(jobs))
    return len(jobs)


def retry_due(now: datetime | None = None) -> set[str]:
    """Keys waiting for a retry whose backoff is over: due even before their regular schedule."""
    now = now or datetime.utcnow()
    with SessionLocal() as db:
        return set(db.scalars(select(CrawlJob.key).where(CrawlJob.status == "retry_wait", CrawlJob.next_retry_at <= now)))


def claim(key: str, kind: str, connector_id: int | None, query_id: int | None = None, checkpoint: dict | None = None,
          worker: str = WORKER, now: datetime | None = None) -> str | None:
    """Lease the job for this worker. None when another worker holds a live lease, holds the row lock right now, or the
    job is still in its retry backoff."""
    now = now or datetime.utcnow()
    try:
        with SessionLocal.begin() as db:
            job = db.scalar(select(CrawlJob).where(CrawlJob.key == key).with_for_update(skip_locked=True))
            if job is None:
                if db.get(CrawlJob, key) is not None:  # exists but locked by a concurrent claimer (SKIP LOCKED)
                    return None
                job = CrawlJob(key=key, kind=kind, connector_id=connector_id, query_id=query_id, status="idle", retry_count=0)
                db.add(job)
                db.flush()
            if job.status == "running" and job.lease_until and job.lease_until > now:
                return None
            if job.status == "running":  # lease ran out and recover() has not seen it yet: same as recover()
                _lose(db, job, now)
            if job.status in ("retry_wait", "error") and job.next_retry_at and job.next_retry_at > now:
                return None
            token = secrets.token_hex(24)
            job.status, job.run_token, job.lease_owner = "running", token, worker
            job.lease_until, job.heartbeat_at, job.updated_at = now + timedelta(seconds=LEASE_S), now, now
            db.add(CrawlRun(job_key=key, run_token=token, worker=worker, attempt=job.retry_count, status="running",
                            started_at=now, checkpoint_before=checkpoint if checkpoint is not None else job.checkpoint))
            return token
    except IntegrityError:  # two workers inserted the first row at once: the other one won
        return None


def heartbeat(key: str, token: str, now: datetime | None = None) -> bool:
    """Extend the lease. False = this worker lost it (expired and recovered / re-claimed elsewhere)."""
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        n = db.execute(update(CrawlJob).where(CrawlJob.key == key, CrawlJob.run_token == token, CrawlJob.status == "running")
                       .values(lease_until=now + timedelta(seconds=LEASE_S), heartbeat_at=now)).rowcount
    return bool(n)


@contextmanager
def leased(key: str, token: str):
    """Heartbeat in the background while the body runs."""
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_S):
            try:
                if not heartbeat(key, token):
                    log.warning("crawl queue: %s lost its lease; its result will not be recorded", key)
                    return
            except Exception:
                log.exception("crawl queue: heartbeat for %s failed", key)

    t = threading.Thread(target=beat, daemon=True, name=f"lease-{key}")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)


def finish(key: str, token: str, result: dict | None = None, checkpoint: dict | None = None, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        job = db.scalar(select(CrawlJob).where(CrawlJob.key == key).with_for_update())
        if not job or job.run_token != token or job.status != "running":
            return False  # fenced: the lease moved on, this worker's result is not the job's
        result = result or {}
        job.status, job.retry_count, job.next_retry_at, job.last_error = "idle", 0, None, None
        job.run_token, job.lease_owner, job.lease_until, job.updated_at = None, None, None, now
        if checkpoint is not None:
            job.checkpoint = checkpoint
        db.execute(update(CrawlRun).where(CrawlRun.run_token == token).values(
            status="ok", finished_at=now, checkpoint_after=checkpoint if checkpoint is not None else job.checkpoint, **_counts(result)))
    return True


def _counts(result: dict) -> dict:
    """crawl_runs columns from a run_connector result (Data & Connectors KPIs)."""
    return dict(fetched=result.get("fetched"), new_ads=result.get("new_ads"), pages=result.get("pages"), empty_pages=result.get("empty_pages"),
                invalid=result.get("invalid"), excluded=result.get("excluded_entertainment"), duplicates=result.get("updated_ads"),
                accepted=(result.get("new_ads") or 0) + (result.get("updated_ads") or 0) if "new_ads" in result else None)


def fail(key: str, token: str, error: str, now: datetime | None = None, blocked: bool = False, result: dict | None = None,
         checkpoint: dict | None = None) -> bool:
    """Retry with backoff up to MAX_RETRIES, then `error` (the regular schedule runs it again; a success resets the count).
    `blocked` (access challenge): no fast retries — hammering a challenge never clears it; the regular schedule tries again.
    `result` / `checkpoint`: a partial run that committed its earlier pages — the history keeps what it stored and where it stopped."""
    now = now or datetime.utcnow()
    with SessionLocal.begin() as db:
        job = db.scalar(select(CrawlJob).where(CrawlJob.key == key).with_for_update())
        if not job or job.run_token != token or job.status != "running":
            return False
        job.retry_count += 1
        job.last_error = (error or "")[:1000]
        if blocked:
            job.status, job.next_retry_at = "blocked", None
        elif job.retry_count <= MAX_RETRIES:
            job.status, job.next_retry_at = "retry_wait", now + backoff(job.retry_count)
        else:
            job.status, job.next_retry_at, job.retry_count = "error", None, 0
        job.run_token, job.lease_owner, job.lease_until, job.updated_at = None, None, None, now
        if checkpoint is not None:
            job.checkpoint = checkpoint
        db.execute(update(CrawlRun).where(CrawlRun.run_token == token).values(
            status="error", finished_at=now, error=job.last_error, checkpoint_after=job.checkpoint, **(_counts(result) if result else {})))
    return True
