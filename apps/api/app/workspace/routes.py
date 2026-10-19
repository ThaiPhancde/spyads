from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session

from .. import behavior
from ..db import get_db
from ..models import Ad, Advertiser, Product
from .accounts import Input
from .auth import current_user, rate_limit
from .filters import ad_filter
from .models import (ActivityEvent, AdInteraction, AdSnapshot, AdvertiserFilter, Follow, Notification, Profile,
                     SavedSearch, ScanJob, TrackedAd, UserActivity)
from .sources import CAPABILITIES, capability
from .tracking import baseline
from .worker import interval, lock_scheduler

router = APIRouter(prefix="/api", tags=["workspace"], dependencies=[Depends(current_user)])


def require_advertiser(db, aid):
    adv = db.get(Advertiser, aid)
    if not adv:
        raise HTTPException(404, "Không tìm thấy advertiser")
    return adv


def require_follow(db, uid, aid):
    follow = db.get(Follow, (uid, aid))
    if not follow:
        raise HTTPException(404, "Advertiser chưa nằm trong danh sách theo dõi của bạn")
    return follow


def adv_dict(a):
    return {"id": a.id, "name": a.name, "platform": a.platform, "country": a.country,
            "page_url": a.page_url, "avatar_url": a.avatar_url, "source": a.source,
            "source_advertiser_id": a.source_advertiser_id, "capability": capability(a)}


def job_dict(j):
    if not j:
        return {"source_status": "not_scheduled", "last_checked_at": None, "next_check_at": None, "last_success_at": None}
    return {k: getattr(j, k) for k in ("source_status", "last_checked_at", "next_check_at", "last_success_at", "error", "last_changes", "attempts")}


@router.get("/workspace/state")
def state(user=Depends(current_user), db: Session = Depends(get_db)):
    uid = user["id"]
    return {"followed_ids": list(db.scalars(select(Follow.advertiser_id).where(Follow.user_id == uid))),
            "interactions": {a.ad_id: {"saved": a.saved, "favorite": a.favorite, "tested": a.tested, "note": a.note}
                             for a in db.scalars(select(AdInteraction).where(AdInteraction.user_id == uid))}}


@router.get("/advertisers")
def advertisers(q: str = "", limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
                user=Depends(current_user), db: Session = Depends(get_db)):
    query = select(Advertiser).where(exists(select(Ad.id).where(Ad.advertiser_id == Advertiser.id)))
    if q.strip():
        query = query.where(or_(Advertiser.name.ilike(f"%{q.strip()[:100]}%"), Advertiser.source_advertiser_id == q.strip()))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    follows = set(db.scalars(select(Follow.advertiser_id).where(Follow.user_id == user["id"])))
    return {"total": total, "rows": [adv_dict(a) | {"followed": a.id in follows} for a in
                                    db.scalars(query.order_by(Advertiser.name, Advertiser.id).offset(offset).limit(limit))]}


class FollowInput(Input):
    interval_hours: Literal[6, 12, 24] = 24


@router.post("/competitors/{aid}/follow")
def follow_advertiser(aid: int, body: FollowInput, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"follow:{user['id']}", 120, 3600)
    lock_scheduler(db)
    adv = require_advertiser(db, aid)
    follow = db.get(Follow, (user["id"], aid))
    if not follow:
        follow = Follow(user_id=user["id"], advertiser_id=aid, interval_hours=body.interval_hours)
        db.add(follow)
        db.add(UserActivity(user_id=user["id"], kind="follow", data={"advertiser_id": aid, "name": adv.name}))
        behavior.track(db, user["id"], "advertiser_follow", advertiser_id=aid)
        baseline(db, adv)
    else:
        follow.interval_hours = body.interval_hours
    db.flush()
    job = db.get(ScanJob, aid)
    if not job:
        db.add(ScanJob(advertiser_id=aid, source=adv.source or "unknown", next_check_at=datetime.utcnow()))
    elif not job.lease_until:
        job.next_check_at = min(job.next_check_at, datetime.utcnow() + timedelta(hours=interval(db, aid)))
    db.commit()
    return {"followed": True, "capability": capability(adv)}


@router.delete("/competitors/{aid}/follow")
def unfollow(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    lock_scheduler(db)
    f = db.get(Follow, (user["id"], aid))
    if f:
        db.delete(f)
        db.add(UserActivity(user_id=user["id"], kind="unfollow", data={"advertiser_id": aid}))
        behavior.track(db, user["id"], "advertiser_unfollow", advertiser_id=aid)
    db.commit()
    return {"followed": False}


@router.get("/competitors")
def competitors(limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
                user=Depends(current_user), db: Session = Depends(get_db)):
    query = select(Advertiser, Follow, ScanJob).join(Follow, Follow.advertiser_id == Advertiser.id)
    query = query.outerjoin(ScanJob, ScanJob.advertiser_id == Advertiser.id).where(Follow.user_id == user["id"])
    total = db.scalar(select(func.count()).select_from(Follow).where(Follow.user_id == user["id"]))
    entries = db.execute(query.order_by(Follow.followed_at.desc(), Advertiser.id).offset(offset).limit(limit)).all()
    aids = [a.id for a, _, _ in entries]
    counts = dict(db.execute(select(TrackedAd.advertiser_id, func.count()).where(TrackedAd.advertiser_id.in_(aids))
                             .group_by(TrackedAd.advertiser_id)).all())
    new_counts = dict(db.execute(select(TrackedAd.advertiser_id, func.count()).join(Follow, Follow.advertiser_id == TrackedAd.advertiser_id)
        .where(Follow.user_id == user["id"], TrackedAd.advertiser_id.in_(aids), TrackedAd.baseline.is_(False),
               TrackedAd.detected_at > Follow.seen_at).group_by(TrackedAd.advertiser_id)).all())
    return {"total": total, "rows": [adv_dict(a) | job_dict(j) | {"followed": True, "followed_at": f.followed_at,
            "interval_hours": f.interval_hours, "total_ads": counts.get(a.id, 0), "new_ads": new_counts.get(a.id, 0)} for a, f, j in entries]}


@router.get("/competitors/{aid}")
def detail(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    adv = require_advertiser(db, aid)
    follow = db.get(Follow, (user["id"], aid))
    counts = dict(db.execute(select(TrackedAd.status, func.count()).where(TrackedAd.advertiser_id == aid).group_by(TrackedAd.status)).all())
    return {"advertiser": adv_dict(adv), "followed": bool(follow), "followed_at": follow.followed_at if follow else None,
            "interval_hours": follow.interval_hours if follow else 24, "counts": counts,
            "scan": job_dict(db.get(ScanJob, aid)), "seen_at": follow.seen_at if follow else None}


@router.get("/competitors/{aid}/status")
def scan_status(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    require_follow(db, user["id"], aid)
    return job_dict(db.get(ScanJob, aid))


@router.post("/competitors/{aid}/refresh", status_code=202)
def refresh(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"refresh:{user['id']}", 30, 3600)
    lock_scheduler(db)
    require_follow(db, user["id"], aid)
    cap = capability(require_advertiser(db, aid))
    if not cap["supported"]:
        raise HTTPException(409, cap["detail"])
    j = db.get(ScanJob, aid)
    now = datetime.utcnow()
    if j.lease_until and j.lease_until > now:
        return {"queued": False, "source_status": "running"}
    if j.last_manual_at and j.last_manual_at > now - timedelta(minutes=5):
        raise HTTPException(429, "Advertiser đã được yêu cầu kiểm tra trong 5 phút gần đây")
    j.next_check_at, j.last_manual_at, j.source_status = now, now, "pending"
    db.commit()
    return {"queued": True, "source_status": "pending"}


@router.post("/competitors/{aid}/seen")
def seen(aid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    f = require_follow(db, user["id"], aid)
    f.seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/competitors/{aid}/ads")
def competitor_ads(aid: int, tab: Literal["all", "active", "new"] = "all", limit: int = Query(40, ge=1, le=200),
                   offset: int = Query(0, ge=0), user=Depends(current_user), db: Session = Depends(get_db)):
    f = require_follow(db, user["id"], aid)
    q = select(TrackedAd).where(TrackedAd.advertiser_id == aid)
    if tab == "active":
        q = q.where(TrackedAd.status == "active")
    elif tab == "new":
        q = q.where(TrackedAd.baseline.is_(False), TrackedAd.detected_at > f.seen_at)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    return {"total": total, "rows": [{"id": a.id, "ad_id": a.ad_id, "source_ad_id": a.source_ad_id, "status": a.status,
            "data": a.data, "detected_at": a.detected_at, "last_seen_at": a.last_seen_at, "baseline": a.baseline}
            for a in db.scalars(q.order_by(TrackedAd.detected_at.desc(), TrackedAd.id.desc()).offset(offset).limit(limit))]}


@router.get("/competitors/{aid}/timeline")
def timeline(aid: int, limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
             user=Depends(current_user), db: Session = Depends(get_db)):
    f = require_follow(db, user["id"], aid)
    q = select(ActivityEvent).where(ActivityEvent.advertiser_id == aid, ActivityEvent.created_at >= f.followed_at)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = [{"id": e.id, "kind": e.kind, "data": e.data, "at": e.created_at} for e in
            db.scalars(q.order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc()).offset(offset).limit(limit))]
    if offset <= total < offset + limit:
        rows.append({"id": "follow", "kind": "follow", "data": {}, "at": f.followed_at})
    return {"total": total + 1, "rows": rows}


@router.get("/competitors/{aid}/products")
def competitor_products(aid: int, limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
                        user=Depends(current_user), db: Session = Depends(get_db)):
    require_follow(db, user["id"], aid)
    q = select(Product).where(Product.id.in_(select(Ad.product_id).where(Ad.advertiser_id == aid)))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    return {"total": total, "rows": [{"id": p.id, "name": p.name_vi or p.canonical_name, "image_url": p.image_url} for p in
                                    db.scalars(q.order_by(Product.id.desc()).offset(offset).limit(limit))]}


@router.get("/competitors/{aid}/ads/{tid}/snapshots")
def snapshots(aid: int, tid: int, limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
              user=Depends(current_user), db: Session = Depends(get_db)):
    require_follow(db, user["id"], aid)
    row = db.get(TrackedAd, tid)
    if not row or row.advertiser_id != aid:
        raise HTTPException(404)
    q = select(AdSnapshot).where(AdSnapshot.tracked_ad_id == tid)
    return {"total": db.scalar(select(func.count()).select_from(q.subquery())), "rows": [
        {"id": s.id, "source": s.source, "observed_at": s.observed_at, "data": s.data} for s in
        db.scalars(q.order_by(AdSnapshot.id.desc()).offset(offset).limit(limit))]}


@router.get("/workspace/filters")
def get_filters(user=Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Profile, user["id"])
    rows = db.execute(select(AdvertiserFilter, Advertiser.name).join(Advertiser, Advertiser.id == AdvertiserFilter.advertiser_id)
                      .where(AdvertiserFilter.user_id == user["id"]))
    return {"mode": p.advertiser_mode if p else "all", "rows": [{"advertiser_id": f.advertiser_id, "name": name, "mode": f.mode} for f, name in rows]}


class FilterMode(Input):
    mode: Literal["all", "followed", "whitelist"]


@router.put("/workspace/filters")
def set_filter_mode(body: FilterMode, user=Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Profile, user["id"])
    if not p:
        p = Profile(user_id=user["id"])
        db.add(p)
    p.advertiser_mode = body.mode
    db.commit()
    return {"ok": True}


class FilterEntry(Input):
    mode: Literal["whitelist", "blacklist", "none"]


@router.put("/workspace/filters/{aid}")
def set_filter(aid: int, body: FilterEntry, user=Depends(current_user), db: Session = Depends(get_db)):
    lock_scheduler(db)
    require_advertiser(db, aid)
    entry = db.get(AdvertiserFilter, (user["id"], aid))
    if body.mode == "none":
        if entry:
            db.delete(entry)
    elif entry:
        entry.mode = body.mode
    else:
        db.add(AdvertiserFilter(user_id=user["id"], advertiser_id=aid, mode=body.mode))
    db.commit()
    return {"ok": True}


class Interaction(Input):
    saved: bool | None = None
    favorite: bool | None = None
    tested: bool | None = None
    note: str | None = Field(None, max_length=3000)


@router.put("/workspace/ads/{aid}")
def interact(aid: int, body: Interaction, user=Depends(current_user), db: Session = Depends(get_db)):
    set_interaction(db, user["id"], aid, **body.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True}


def set_interaction(db, uid, aid, **fields):
    """The one write path for save / favorite / tested / note (also used by the Library): keeps the behavior history
    (Phase 3) and the reservation → ownership promotion (Phase 2) in step. Caller commits."""
    lock_scheduler(db)
    ad = db.get(Ad, aid)
    if not ad:
        raise HTTPException(404)
    row = db.get(AdInteraction, (uid, aid))
    if not row:
        row = AdInteraction(user_id=uid, ad_id=aid)
        db.add(row)
    before = {k: getattr(row, k) for k in ("saved", "favorite", "tested")}
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = datetime.utcnow()
    from ..allocation import on_interaction
    allocation = on_interaction(db, uid, aid, fields.get("saved"), fields.get("favorite"), fields.get("tested"))
    behavior.interaction(db, uid, ad, before, row, allocation)
    db.add(UserActivity(user_id=uid, kind="ad_interaction", data={"ad_id": aid, **fields}))
    return row


@router.get("/workspace/ads")
def library(kind: Literal["all", "saved", "favorite", "tested"] = "all", limit: int = Query(40, ge=1, le=200),
            offset: int = Query(0, ge=0), user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(Ad, AdInteraction, Advertiser).join(AdInteraction, AdInteraction.ad_id == Ad.id).outerjoin(Advertiser, Ad.advertiser_id == Advertiser.id)
    q = q.where(AdInteraction.user_id == user["id"], ad_filter(db))
    if kind != "all":
        q = q.where(getattr(AdInteraction, kind).is_(True))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    return {"total": total, "rows": [{"id": a.id, "text": a.ad_text, "title": a.title, "advertiser": adv.name if adv else None,
        "advertiser_id": a.advertiser_id, "landing_url": a.landing_url, "snapshot_url": a.snapshot_url,
        "saved": i.saved, "favorite": i.favorite, "tested": i.tested, "note": i.note} for a, i, adv in
        db.execute(q.order_by(AdInteraction.updated_at.desc(), Ad.id.desc()).offset(offset).limit(limit))]}


@router.get("/workspace/dashboard")
def dashboard(user=Depends(current_user), db: Session = Depends(get_db)):
    aids = select(Follow.advertiser_id).where(Follow.user_id == user["id"])
    since = datetime.utcnow() - timedelta(hours=24)
    events = select(ActivityEvent).join(Follow, Follow.advertiser_id == ActivityEvent.advertiser_id).where(
        Follow.user_id == user["id"], ActivityEvent.created_at >= Follow.followed_at, ActivityEvent.created_at >= since)
    rows = db.scalars(events.where(ActivityEvent.kind.in_(["new_ad", "ad_changed", "ad_inactive"])).order_by(ActivityEvent.id.desc()).limit(20)).all()
    fresh = events.where(ActivityEvent.kind == "new_ad").subquery()
    return {"followed": db.scalar(select(func.count()).select_from(Follow).where(Follow.user_id == user["id"])),
        "new_ads_24h": db.scalar(select(func.count()).select_from(fresh)),
        "active_competitors": db.scalar(select(func.count(func.distinct(ActivityEvent.advertiser_id))).where(
            ActivityEvent.id.in_(events.with_only_columns(ActivityEvent.id).where(ActivityEvent.kind.in_(["new_ad", "ad_changed", "ad_inactive"]))))),
        "unread": db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user["id"], Notification.read_at.is_(None))),
        "recent": [{"id": e.id, "advertiser_id": e.advertiser_id, "kind": e.kind, "at": e.created_at, "data": e.data} for e in rows],
        "sources": [{"advertiser_id": j.advertiser_id, **job_dict(j)} for j in db.scalars(select(ScanJob).where(ScanJob.advertiser_id.in_(aids)).order_by(ScanJob.last_checked_at.desc().nullslast()).limit(20))]}


@router.get("/workspace/notifications")
def notifications(unread: bool = False, limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0),
                  user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(Notification, ActivityEvent, Advertiser.name).join(ActivityEvent, ActivityEvent.id == Notification.event_id)
    q = q.join(Advertiser, Advertiser.id == ActivityEvent.advertiser_id).where(Notification.user_id == user["id"])
    if unread:
        q = q.where(Notification.read_at.is_(None))
    return {"total": db.scalar(select(func.count()).select_from(q.subquery())), "rows": [
        {"id": n.id, "read": n.read_at is not None, "kind": e.kind, "data": e.data, "at": n.created_at,
         "advertiser_id": e.advertiser_id, "advertiser": name} for n, e, name in
        db.execute(q.order_by(Notification.id.desc()).offset(offset).limit(limit))]}


class ReadInput(Input):
    read: bool = True


@router.patch("/workspace/notifications/{nid}")
def notification_read(nid: int, body: ReadInput, user=Depends(current_user), db: Session = Depends(get_db)):
    n = db.scalar(select(Notification).where(Notification.id == nid, Notification.user_id == user["id"]))
    if not n:
        raise HTTPException(404)
    n.read_at = datetime.utcnow() if body.read else None
    db.commit()
    return {"ok": True}


class SaveSearch(Input):
    name: str = Field(min_length=1, max_length=100)
    filters: dict


@router.get("/workspace/searches")
def searches(user=Depends(current_user), db: Session = Depends(get_db)):
    return {"rows": [{"id": s.id, "name": s.name, "filters": s.filters} for s in db.scalars(
        select(SavedSearch).where(SavedSearch.user_id == user["id"]).order_by(SavedSearch.id.desc()))]}


@router.post("/workspace/searches")
def save_search(body: SaveSearch, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(f"save-search:{user['id']}", 100, 3600)
    allowed = {"q", "country", "funnel", "sort", "exclude", "network", "has_video", "min_days", "max_advertisers",
               "min_networks", "name_status", "expand"}
    filters = {k: (str(v).lower() if isinstance(v, bool) else str(v))[:500] for k, v in body.filters.items() if k in allowed}
    s = SavedSearch(user_id=user["id"], name=body.name, filters=filters)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.delete("/workspace/searches/{sid}")
def delete_search(sid: int, user=Depends(current_user), db: Session = Depends(get_db)):
    s = db.scalar(select(SavedSearch).where(SavedSearch.id == sid, SavedSearch.user_id == user["id"]))
    if not s:
        raise HTTPException(404)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/workspace/activity")
def activity(limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0), user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(UserActivity).where(UserActivity.user_id == user["id"])
    return {"total": db.scalar(select(func.count()).select_from(q.subquery())), "rows": [
        {"id": a.id, "kind": a.kind, "data": a.data, "at": a.created_at} for a in
        db.scalars(q.order_by(UserActivity.id.desc()).offset(offset).limit(limit))]}
