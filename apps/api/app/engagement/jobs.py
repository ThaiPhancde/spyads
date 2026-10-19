"""Engagement queue: the API enqueues the ads on screen, the worker resolves them (service.resolve).

Same shape as pricing/jobs.py: one engagement_jobs row per ad (dedupe), survives restarts, a `running` row older than STALE_MIN is
re-queued. The worker pass is free (ad scope + mapping + memory). It calls Bright Data only when ENGAGEMENT_AUTO_ENABLED is on and the
ad names a public post, inside brightdata_posts' budget; otherwise the explicit "Lấy tương tác" click does (routes.lookup).
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import IS_SQLITE, SessionLocal
from ..pricing.quota import env_int
from . import service
from .models import AdEngagement, EngagementJob

log = logging.getLogger("app.engagement.jobs")
STALE_MIN = 10
MAX_ATTEMPTS = 3


def auto_provider() -> bool:
    return os.getenv("ENGAGEMENT_AUTO_ENABLED", "false").split("#")[0].strip().lower() in ("1", "true", "yes", "on")


def enqueue(db: Session, ad_ids: list[int], priority: int = 1) -> dict[int, str]:
    """Ads with a fresh observation (any status inside its TTL) are not queued. Returns {ad_id: queued | running | fresh}."""
    ad_ids = list(dict.fromkeys(ad_ids))
    now = datetime.utcnow()
    fresh = set(db.scalars(select(AdEngagement.ad_id).where(AdEngagement.ad_id.in_(ad_ids), AdEngagement.expires_at > now)))
    jobs = {j.ad_id: j for j in db.scalars(select(EngagementJob).where(EngagementJob.ad_id.in_(ad_ids)))} if ad_ids else {}
    out: dict[int, str] = {}
    for aid in ad_ids:
        j = jobs.get(aid)
        if aid in fresh:
            out[aid] = "fresh"
        elif j is None:
            db.add(EngagementJob(ad_id=aid, priority=priority, next_at=now, requested_at=now))
            out[aid] = "queued"
        elif j.status in ("queued", "running"):
            j.priority = max(j.priority, priority)
            out[aid] = j.status
        else:  # done / error and the observation expired: look again
            j.status, j.attempts, j.priority, j.next_at, j.requested_at, j.error = "queued", 0, priority, now, now, None
            out[aid] = "queued"
    try:
        db.commit()
    except IntegrityError:  # two requests queued the same new ad at once: the other one won
        db.rollback()
    return out


def _claim(limit: int) -> list[int]:
    now = datetime.utcnow()
    with SessionLocal() as db:
        for j in db.scalars(select(EngagementJob).where(EngagementJob.status == "running", EngagementJob.started_at < now - timedelta(minutes=STALE_MIN))):
            j.status, j.next_at = "queued", now
        q = select(EngagementJob).where(EngagementJob.status == "queued", EngagementJob.next_at <= now).order_by(
            EngagementJob.priority.desc(), EngagementJob.requested_at).limit(limit)
        if not IS_SQLITE:
            q = q.with_for_update(skip_locked=True)
        rows = db.scalars(q).all()
        for j in rows:
            j.status, j.started_at, j.attempts = "running", now, j.attempts + 1
        ids = [j.id for j in rows]
        db.commit()
    return ids


def run_one(job_id: int) -> dict:
    with SessionLocal() as db:
        j = db.get(EngagementJob, job_id)
        ad_id = j.ad_id
    try:
        with SessionLocal() as db:
            r = service.resolve(db, ad_id, allow_provider=auto_provider())
            j = db.get(EngagementJob, job_id)
            j.status, j.finished_at, j.steps, j.error = "done", datetime.utcnow(), r["steps"], None
            db.commit()
        return {"ad_id": ad_id, "status": r["status"]}
    except Exception as e:  # noqa: BLE001 - one bad ad never stops the queue
        log.warning("engagement job %s (ad %s) failed: %s", job_id, ad_id, str(e)[:200])
        with SessionLocal() as db:
            j = db.get(EngagementJob, job_id)
            final = j.attempts >= MAX_ATTEMPTS or isinstance(e, ValueError)
            j.status = "error" if final else "queued"
            j.next_at = datetime.utcnow() + timedelta(minutes=5 * j.attempts)
            j.finished_at = datetime.utcnow() if final else None
            j.error = f"{type(e).__name__}: {str(e)[:300]}"
            db.commit()
        return {"ad_id": ad_id, "error": str(e)[:100]}


def run_pending(limit: int | None = None) -> dict:
    try:
        with SessionLocal() as db:
            service.collect_pending(db)  # free: finish page-post snapshots that were still running
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("engagement snapshot collection failed")
    ids = _claim(limit or env_int("ENGAGEMENT_JOBS_PER_TICK", 20))
    if not ids:
        return {"ran": 0}
    with ThreadPoolExecutor(env_int("ENGAGEMENT_WORKERS", 2)) as ex:
        res = list(ex.map(run_one, ids))
    return {"ran": len(res), "ok": sum(1 for r in res if r.get("status") == "ok"), "errors": sum(1 for r in res if r.get("error"))}


def stats(db: Session) -> dict:
    from sqlalchemy import func

    return {"jobs": dict(db.execute(select(EngagementJob.status, func.count()).group_by(EngagementJob.status)).all()),
            "observations": dict(db.execute(select(AdEngagement.status, func.count()).group_by(AdEngagement.status)).all())}
