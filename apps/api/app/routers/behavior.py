"""Phase 3 API: client-reported signals (view / detail / skip / not interested) and per-MKT behavior history.
Save / favorite / test / follow are recorded by their existing endpoints (app/behavior.py)."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import behavior as B
from ..db import get_db
from ..workspace.auth import current_user

router = APIRouter(prefix="/api/behavior", tags=["behavior"], dependencies=[Depends(current_user)])


class SignalIn(BaseModel):
    kind: Literal["ad_view", "ad_detail_view", "ad_skip", "ad_not_interested"]
    ad_ids: list[int] = Field(min_length=1, max_length=100)


@router.post("/events")
def record(body: SignalIn, user=Depends(current_user), db: Session = Depends(get_db)):
    n = B.track_client(db, user["id"], body.kind, body.ad_ids)
    db.commit()
    return {"recorded": n}


def scoped(user, user_id):
    # Every account reads only its own history; a user_id parameter never widens it (no ADMIN role exists).
    return user["id"]


@router.get("/events")
def history(kind: str | None = None, ad_id: int | None = None, product_id: int | None = None,
            advertiser_id: int | None = None, since: datetime | None = None, user_id: int | None = None,
            limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
            user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(B.BehaviorEvent)
    uid = scoped(user, user_id)
    if uid is not None:
        q = q.where(B.BehaviorEvent.user_id == uid)
    for col, value in (("kind", kind), ("ad_id", ad_id), ("product_id", product_id), ("advertiser_id", advertiser_id)):
        if value is not None:
            q = q.where(getattr(B.BehaviorEvent, col) == value)
    if since:
        q = q.where(B.BehaviorEvent.created_at >= since)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(B.BehaviorEvent.created_at.desc(), B.BehaviorEvent.id.desc()).offset(offset).limit(limit))
    return {"total": total, "rows": [B.view(e) for e in rows]}


@router.get("/summary")
def summary(user_id: int | None = None, user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(B.BehaviorEvent.kind, func.count()).group_by(B.BehaviorEvent.kind)
    uid = scoped(user, user_id)
    if uid is not None:
        q = q.where(B.BehaviorEvent.user_id == uid)
    counts = dict(db.execute(q).all())
    return {"counts": {k: counts.get(k, 0) for k in B.KINDS}}
