"""Price enrichment API — called by product / ad cards, never by a page of its own.

  GET  /api/prices?subjects=ad:1,product:2   what memory has now (+ queue status for the rest)
  POST /api/prices/enrich {subjects, priority} queue the ones that are on screen (dedupe server-side) → same shape
  GET  /api/prices/quota                       both providers' budgets + queue stats
  GET  /api/prices/history?subject=ad:1        every observation (monitoring)
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..visual import lens
from . import brightdata, jobs, quota, shopping
from .models import PriceJob, PriceObservation
from .router import summary

router = APIRouter()
MAX_SUBJECTS = 200


def _subjects(raw: str | list[str]) -> list[str]:
    items = raw if isinstance(raw, list) else raw.split(",")
    out = []
    for s in items:
        s = s.strip()
        kind, _, sid = s.partition(":")
        if kind in ("ad", "product", "listing") and sid.isdigit() and s not in out:
            out.append(s)
    if len(out) > MAX_SUBJECTS:
        raise HTTPException(400, {"code": "too_many_subjects", "message": f"≤ {MAX_SUBJECTS} subjects per call"})
    return out


def _view(db: Session, subjects: list[str]) -> dict:
    have = summary(db, subjects)
    pending = {j.subject: j for j in db.scalars(select(PriceJob).where(PriceJob.subject.in_([s for s in subjects if s not in have]))).all()} if subjects else {}
    for s in subjects:
        if s not in have:
            j = pending.get(s)
            have[s] = {"subject": s, "status": j.status if j else "unknown", "error": j.error if j and j.status == "error" else None}
    return {"prices": have}


@router.get("/api/prices")
def get_prices(subjects: str = "", db: Session = Depends(get_db)):
    return _view(db, _subjects(subjects))


class EnrichIn(BaseModel):
    subjects: list[str]
    priority: int = 1  # 10 = the marketer opened this one


@router.post("/api/prices/enrich")
def enrich(body: EnrichIn, db: Session = Depends(get_db)):
    subs = _subjects(body.subjects)
    jobs.enqueue(db, subs, max(0, min(10, body.priority)))
    return _view(db, subs)


@router.get("/api/prices/quota")
def prices_quota(db: Session = Depends(get_db)):
    return {"serpapi": {**quota.remaining(db, "serpapi_shopping"), "lens_day": quota.remaining(db, "serpapi_lens")["day_used"],
                        "shopping_unavailable": shopping.unavailable(), "lens_unavailable": lens.live_unavailable(), "account": lens.account()},
            "brightdata": {**quota.remaining(db, "brightdata"), "unavailable": brightdata.unavailable("search"),
                           "scrape_unavailable": brightdata.unavailable("scrape"), "reserve": quota.env_int("BRIGHTDATA_RESERVE", 200)},
            **jobs.stats(db)}


@router.get("/api/prices/history")
def history(subject: str, db: Session = Depends(get_db)):
    subs = _subjects(subject)
    if not subs:
        raise HTTPException(400, {"code": "bad_subject", "message": "subject = ad:<id> | product:<id> | listing:<id>"})
    rows = db.scalars(select(PriceObservation).where(PriceObservation.subject == subs[0]).order_by(PriceObservation.observed_at.desc()).limit(100)).all()
    job = db.scalar(select(PriceJob).where(PriceJob.subject == subs[0]))
    return {"subject": subs[0], "job": {"status": job.status, "steps": job.steps, "error": job.error, "finished_at": job.finished_at} if job else None,
            "rows": [{"id": r.id, "price_type": r.price_type, "price": r.price_amount, "low": r.price_low, "high": r.price_high, "currency": r.currency,
                      "original_price": r.original_price, "seller": r.seller, "provider": r.provider, "method": r.extraction_method, "url": r.source_url,
                      "confidence": r.price_confidence, "match_confidence": r.match_confidence, "observed_at": r.observed_at,
                      "expired": bool(r.expires_at and r.expires_at < datetime.utcnow()), "evidence": r.evidence} for r in rows]}
