"""Durable enrichment queue: the API enqueues subjects that are on screen, the worker climbs the ladder for them.
price_jobs rows survive restarts; a `running` row older than STALE_MIN is picked up again. One worker process runs
PRICE_JOBS_PER_TICK jobs on a small thread pool every scheduler tick (30 s)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import IS_SQLITE, SessionLocal, read_only
from .models import NOT_FOUND, PriceJob, PriceObservation
from .quota import env_int
from .router import Step, fresh, resolve, summary

log = logging.getLogger("app.pricing.jobs")
STALE_MIN = 10
MAX_ATTEMPTS = 3


def enqueue(db: Session, subjects: list[str], priority: int = 1) -> dict[str, str]:
    """Subjects with a fresh observation are not queued; a done job whose result expired is queued again.
    Returns {subject: queued | running | fresh}."""
    subjects = list(dict.fromkeys(subjects))
    out: dict[str, str] = {}
    have = summary(db, subjects)
    now = datetime.utcnow()
    jobs = {j.subject: j for j in db.scalars(select(PriceJob).where(PriceJob.subject.in_(subjects))).all()} if subjects else {}
    for s in subjects:
        if s in have:
            out[s] = "fresh"
            continue
        j = jobs.get(s)
        if j is None:
            db.add(PriceJob(subject=s, priority=priority, next_at=now, requested_at=now))
            out[s] = "queued"
        elif j.status in ("queued", "running"):
            j.priority = max(j.priority, priority)
            out[s] = j.status
        elif j.status == "done" or (j.status == "error" and now - (j.finished_at or now) > timedelta(hours=12)):
            j.status, j.attempts, j.priority, j.next_at, j.requested_at, j.error = "queued", 0, priority, now, now, None
            out[s] = "queued"
        else:
            out[s] = "error"
    try:
        db.commit()
    except IntegrityError:  # two requests queued the same new subject at once: the other one won, same outcome
        db.rollback()
    return out


def _claim(limit: int) -> list[int]:
    now = datetime.utcnow()
    with SessionLocal() as db:
        stale = now - timedelta(minutes=STALE_MIN)
        for j in db.scalars(select(PriceJob).where(PriceJob.status == "running", PriceJob.started_at < stale)):
            j.status, j.next_at = "queued", now  # worker died mid-job
        q = select(PriceJob).where(PriceJob.status == "queued", PriceJob.next_at <= now).order_by(PriceJob.priority.desc(), PriceJob.requested_at).limit(limit)
        if not IS_SQLITE:
            q = q.with_for_update(skip_locked=True)
        rows = db.scalars(q).all()
        for j in rows:
            j.status, j.started_at, j.attempts = "running", now, j.attempts + 1
        ids = [j.id for j in rows]
        db.commit()
    return ids


def run_one(job_id: int) -> dict:
    steps = Step()
    with SessionLocal() as db:
        j = db.get(PriceJob, job_id)
        subject = j.subject
    try:
        with SessionLocal() as db, read_only():  # the ladder only reads (+ network); writes go in a fresh transaction below
            obs = resolve(db, subject, steps)
        with SessionLocal() as db:
            if not obs:
                obs = [PriceObservation(subject=subject, price_type=NOT_FOUND, provider="router", match_confidence=0, price_confidence=0,
                                        observed_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=3), evidence={"steps": steps.rows[-6:]})]
            db.add_all(obs)
            j = db.get(PriceJob, job_id)
            j.status, j.finished_at, j.steps, j.error = "done", datetime.utcnow(), steps.rows, None
            db.commit()
            return {"subject": subject, "found": [o.price_type for o in obs]}
    except Exception as e:  # noqa: BLE001 — one bad subject never stops the queue
        log.warning("price job %s (%s) failed: %s", job_id, subject, str(e)[:200])
        with SessionLocal() as db:
            j = db.get(PriceJob, job_id)
            final = j.attempts >= MAX_ATTEMPTS or isinstance(e, ValueError)
            j.status = "error" if final else "queued"
            j.next_at = datetime.utcnow() + timedelta(minutes=5 * j.attempts)
            j.finished_at = datetime.utcnow() if final else None
            j.error, j.steps = f"{type(e).__name__}: {str(e)[:300]}", steps.rows
            db.commit()
        return {"subject": subject, "error": str(e)[:100]}


def run_pending(limit: int | None = None) -> dict:
    """One tick: claim up to `limit` jobs, run them on PRICE_WORKERS threads. Returns counts for the log."""
    ids = _claim(limit or env_int("PRICE_JOBS_PER_TICK", 6))
    if not ids:
        return {"ran": 0}
    with ThreadPoolExecutor(env_int("PRICE_WORKERS", 3)) as ex:
        res = list(ex.map(run_one, ids))
    found = sum(1 for r in res if r.get("found") and r["found"] != [NOT_FOUND])
    return {"ran": len(res), "priced": found, "errors": sum(1 for r in res if r.get("error"))}


def stats(db: Session) -> dict:
    from sqlalchemy import func

    by_status = dict(db.execute(select(PriceJob.status, func.count()).group_by(PriceJob.status)).all())
    by_type = dict(db.execute(select(PriceObservation.price_type, func.count()).where(PriceObservation.expires_at > datetime.utcnow())
                              .group_by(PriceObservation.price_type)).all())
    return {"jobs": by_status, "fresh_observations": by_type}
