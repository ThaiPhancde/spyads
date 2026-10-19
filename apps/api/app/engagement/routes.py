"""Engagement API — called by ad cards, never by a page of its own.

  GET  /api/engagement?ads=1,2,3        ad-scope + post-scope numbers per ad (null = N/A) + why
  POST /api/engagement/enrich {ad_ids}  queue the ads on screen (dedupe, free pass; provider only if ENGAGEMENT_AUTO_ENABLED)
  POST /api/engagement/lookup {ad_id}   explicit "Lấy tương tác": may spend one Bright Data Posts record (own budget)
  GET  /api/engagement/history?ad_id=   every observation
  GET  /api/engagement/quota            provider availability + budget + queue stats
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..pricing import quota
from . import brightdata_posts as bd
from . import jobs, service
from .models import AdEngagement

router = APIRouter()
MAX_ADS = 200


def _ids(raw) -> list[int]:
    items = raw if isinstance(raw, list) else str(raw).split(",")
    out = [int(i) for i in items if str(i).strip().isdigit()]
    if len(out) > MAX_ADS:
        raise HTTPException(400, {"code": "too_many_ads", "message": f"≤ {MAX_ADS} ads per call"})
    return list(dict.fromkeys(out))


@router.get("/api/engagement")
def get_engagement(ads: str = "", db: Session = Depends(get_db)):
    return {"engagement": service.summary(db, _ids(ads))}


class EnrichIn(BaseModel):
    ad_ids: list[int]
    priority: int = 1


@router.post("/api/engagement/enrich")
def enrich(body: EnrichIn, db: Session = Depends(get_db)):
    ids = _ids(body.ad_ids)
    jobs.enqueue(db, ids, max(0, min(10, body.priority)))
    return {"engagement": service.summary(db, ids)}


class LookupIn(BaseModel):
    ad_id: int


@router.post("/api/engagement/lookup")
def lookup(body: LookupIn, db: Session = Depends(get_db)):
    try:
        r = service.resolve(db, body.ad_id, allow_provider=True)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(404, {"code": "ad_not_found", "message": str(e)})
    except Exception as e:  # noqa: BLE001 - answer with a reason, never a bare 500
        db.rollback()
        return {"engagement": {}, "status": "error", "reason": "internal_error", "steps": [], "error": {"code": "internal_error", "message": f"{type(e).__name__}: {str(e)[:160]}"}}
    return {"engagement": service.summary(db, [body.ad_id]), "status": r["status"], "reason": r["reason"], "steps": r["steps"]}


@router.get("/api/engagement/history")
def history(ad_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(AdEngagement).where(AdEngagement.ad_id == ad_id).order_by(AdEngagement.observed_at.desc(), AdEngagement.id.desc()).limit(100)).all()
    return {"ad_id": ad_id, "rows": [{"id": r.id, "scope": r.scope, "provider": r.provider, "status": r.status, "reason": r.reason, "platform": r.platform,
                                       "post_id": r.post_id, "url": r.post_url, "likes": r.likes, "comments": r.comments, "shares": r.shares, "views": r.views,
                                       "observed_at": r.observed_at, "expires_at": r.expires_at, "evidence": r.evidence} for r in rows]}


@router.get("/api/engagement/quota")
def engagement_quota(db: Session = Depends(get_db)):
    return {"brightdata_posts": {**quota.remaining(db, bd.PROVIDER), "unavailable": bd.unavailable(), "datasets": bd.DATASETS,
                                 "auto_enabled": jobs.auto_provider()}, **jobs.stats(db)}
