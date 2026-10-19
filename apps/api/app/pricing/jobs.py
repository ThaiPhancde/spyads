"""Durable enrichment queue: the API enqueues subjects that are on screen, the worker climbs the ladder for them.
price_jobs rows survive restarts; a `running` row older than STALE_MIN is picked up again. One worker process runs
PRICE_JOBS_PER_TICK jobs on a small thread pool every scheduler tick (30 s)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import IS_SQLITE, SessionLocal, read_only
from ..models import Ad, Product
from ..visual.lens import _lock
from ..visual.models import ExternalApiCall
from .models import NOT_FOUND, PriceJob, PriceObservation
from .quota import env_int
from .router import Step, fresh, resolve, summary

log = logging.getLogger("app.pricing.jobs")
STALE_MIN = 10
MAX_ATTEMPTS = 3


def enqueue(db: Session, subjects: list[str], priority: int = 1, retry_negative: bool = False) -> dict[str, str]:
    """Subjects with a fresh observation are not queued; a done job whose result expired is queued again.
    Returns {subject: queued | running | fresh}."""
    subjects = list(dict.fromkeys(subjects))
    out: dict[str, str] = {}
    have = summary(db, subjects)
    now = datetime.utcnow()
    if retry_negative:
        retry = [s for s, r in have.items() if r.get("price_type") == NOT_FOUND]
        for observation in db.scalars(select(PriceObservation).where(PriceObservation.subject.in_(retry), PriceObservation.price_type == NOT_FOUND)):
            observation.expires_at = now
        have = {s: r for s, r in have.items() if s not in retry}
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
        elif j.status == "done" or (j.status == "error" and (priority >= 10 or now - (j.finished_at or now) > timedelta(hours=12))):  # an explicit open / click always retries
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


def _auto_paid(subject, prior_steps=()):
    """Only TEST products; 10 distinct clusters/day and 100/month by default, within provider quotas too."""
    import os
    if os.getenv("PRICE_AUTO_TEST_ENABLED", "1").split("#")[0].strip().lower() not in ("1", "true", "yes", "on"):
        return False
    kind, _, sid = subject.partition(":")
    with _lock("price-auto-budget"), SessionLocal() as db:
        pid = int(sid) if kind == "product" else db.scalar(select(Ad.product_id).where(Ad.id == int(sid))) if kind == "ad" else None
        p = db.get(Product, pid) if pid else None
        if not p or p.recommendation not in ("TEST", "TEST_NOW"):
            return False
        now = datetime.utcnow()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = day.replace(day=1)
        base = select(func.count()).select_from(ExternalApiCall).where(ExternalApiCall.provider == "pricing_auto", ExternalApiCall.counted.is_(True))
        already = db.scalar(base.where(ExternalApiCall.search_id == pid, ExternalApiCall.created_at >= day))
        if already:
            return any(s.get("code") == "lens_pending" for s in prior_steps)
        if (db.scalar(base.where(ExternalApiCall.created_at >= day)) >= env_int("PRICE_AUTO_DAILY_LIMIT", 10)
            or db.scalar(base.where(ExternalApiCall.created_at >= month)) >= env_int("PRICE_AUTO_MONTHLY_LIMIT", 100)):
            return False
        db.add(ExternalApiCall(provider="pricing_auto", kind="lookup", counted=True, search_id=pid, ok=True))
        db.commit()
        return True


def run_one(job_id: int) -> dict:
    with SessionLocal() as db, read_only():
        subject = db.get(PriceJob, job_id).subject
        kind, _, sid = subject.partition(":")
        pid = db.scalar(select(Ad.product_id).where(Ad.id == int(sid))) if kind == "ad" else int(sid) if kind == "product" else None
    # Prevent concurrent visible ads of the same product from buying identical lookups.
    with _lock(f"price-cluster:{pid}" if pid else f"price-subject:{subject}"):
        return _run_one(job_id)


def _run_one(job_id: int) -> dict:
    steps = Step()
    with SessionLocal() as db:
        j = db.get(PriceJob, job_id)
        subject = j.subject
        prior_steps = j.steps or []
    try:
        with SessionLocal() as db, read_only():  # the ladder only reads (+ network); writes go in a fresh transaction below
            obs = resolve(db, subject, steps)
        if not obs and not any(s.get("code") in ("lens_pending", "advertorial") for s in steps.rows) and _auto_paid(subject, prior_steps):
            steps.add("auto_budget", True, "test_only")
            with SessionLocal() as db, read_only():
                obs = resolve(db, subject, steps, paid=True)
        with SessionLocal() as db:
            if not obs and any(s.get("code") == "lens_pending" for s in steps.rows):
                j = db.get(PriceJob, job_id)
                j.status, j.steps, j.next_at = "queued", steps.rows, datetime.utcnow() + timedelta(seconds=30)
                db.commit()
                return {"subject": subject, "pending": True}
            if not obs:
                obs = [PriceObservation(subject=subject, price_type=NOT_FOUND, provider="router", match_confidence=0, price_confidence=0,
                                        observed_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=6), evidence={"steps": steps.rows[-6:], "rule_version": 2})]
            db.add_all(obs)
            _absorb_identity(db, subject, obs)
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


def lookup_now(subject: str) -> dict:
    """The "show price" button: one subject, paid rungs allowed (Bright Data / SerpApi), run inline. Stores what it finds;
    an empty answer is returned, not stored, so the button stays available."""
    with SessionLocal() as db, read_only():
        kind, _, sid = subject.partition(":")
        pid = db.scalar(select(Ad.product_id).where(Ad.id == int(sid))) if kind == "ad" else int(sid) if kind == "product" else None
    with _lock(f"price-cluster:{pid}" if pid else f"price-subject:{subject}"):
        steps = Step()
        try:
            with SessionLocal() as db, read_only():
                obs = resolve(db, subject, steps, paid=True)
            with SessionLocal() as db:
                db.add_all(obs)
                _absorb_identity(db, subject, obs)
                db.commit()
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001 - the button must answer with a reason, never a bare HTTP 500
            log.exception("lookup_now %s failed", subject)
            steps.add("resolve", False, "internal_error", detail=f"{type(e).__name__}: {str(e)[:160]}")
            return {"found": False, "steps": steps.rows[-6:], "pending": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {str(e)[:160]}"}}
        return {"found": bool(obs), "steps": steps.rows[-6:], "pending": any(s.get("code") == "lens_pending" for s in steps.rows)}


def _absorb_identity(db, subject, observations):
    """Names from the advertiser's actual product page, never from an unverified search snippet."""
    from .models import VERIFIED_COMPETITOR
    from ..services import naming
    from ..services.product_identity import identity_match
    kind, _, sid = subject.partition(":")
    a = db.get(Ad, int(sid)) if kind == "ad" else None
    pid = a.product_id if a else int(sid) if kind == "product" else None
    p = db.get(Product, pid) if pid else None
    if not p:
        return
    for o in observations:
        md = (o.evidence or {}).get("product_identity")
        if o.price_type != VERIFIED_COMPETITOR or not md or identity_match(p.product_identity, md) is False:
            continue
        p.product_identity = dict(p.product_identity or {}) | md
        if md.get("brand") and not p.brand:
            p.brand = md["brand"]
        if a:
            a.product_metadata = dict(a.product_metadata or {}) | md
        nm = naming.best({"metadata": md})
        naming.absorb(p, nm, [{**e, "ad_id": a.id if a else (o.evidence or {}).get("ad_id")} for e in nm["evidence"]])


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
