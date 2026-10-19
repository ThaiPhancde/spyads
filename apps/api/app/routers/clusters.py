"""MKT review of product clustering (services/clustering.py): candidates, accept / reject, numbers, suspected wrong merges."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Ad, Event, Product, ProductLink
from ..cluster_models import AdClusterMembership, DuplicateAdLink, ProductCluster
from ..services import cluster_membership as CM
from ..services import clustering
from ..services import naming as N

router = APIRouter(prefix="/api/clusters", tags=["clusters"])
_impure: dict = {"at": 0.0, "rows": []}   # detect_impure walks every multi-ad product: cached 15 min


class ByIn(BaseModel):
    user: str | None = None


def visible_links():
    live = and_(ProductLink.a_id.in_(select(Product.id)), ProductLink.b_id.in_(select(Product.id)))
    history = exists(select(Event.id).where(Event.id == ProductLink.event_id,
        Event.type == "PRODUCT_MERGED", Event.product_id.in_(select(Product.id)),
        Event.data["undone"].as_boolean().is_not(True)))
    return or_(live, and_(ProductLink.status.in_(["accepted", "auto_merged"]), history))


def side(p: Product) -> dict:
    f = p.features or {}
    return {"id": p.id, "product_code": p.product_code, "name": N.display_name(p), "name_status": p.name_status, "category": p.category,
            "country": p.country, "ads": f.get("total_ads") or f.get("active_ads", 0), "active_ads": f.get("active_ads", 0),
            "unique_ads": f.get("unique_ads", f.get("active_ads", 0)), "advertisers": f.get("advertiser_count", 0),
            "cover_creative_id": p.cover_creative_id, "opportunity_score": p.opportunity_score, "recommendation": p.recommendation}


@router.get("/candidates")
def candidates(status: str = "pending", product_id: int | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db)):
    q = select(ProductLink).where(ProductLink.status == status, visible_links())
    if product_id:
        q = q.where((ProductLink.a_id == product_id) | (ProductLink.b_id == product_id))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    links = db.scalars(q.order_by(ProductLink.score.desc(), ProductLink.id).limit(limit).offset(offset)).all()
    evs = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_([l.event_id for l in links if l.event_id])))}
    ids = {i for l in links for i in (l.a_id, l.b_id)} | {e.product_id for e in evs.values()}
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}
    rows = []
    for l in links:
        base = {"id": l.id, "score": l.score, "status": l.status, "signals": l.signals, "blockers": l.blockers, "event_id": l.event_id,
                "decided_by": l.decided_by, "decided_at": l.decided_at,
                "relation_kind": l.relation_kind or "legacy", "algorithm_version": l.algorithm_version,
                "can_accept": l.algorithm_version == clustering.ALGORITHM_VERSION and l.status == "pending"}
        ev = evs.get(l.event_id) if l.status in ("auto_merged", "accepted") else None
        if ev is not None and ev.type == "PRODUCT_MERGED":   # historical destructive merge
            tgt, d = prods.get(ev.product_id), ev.data or {}
            if tgt is None or d.get("undone"):
                continue
            gone = {"id": d.get("source_id"), "product_code": d.get("source_code"), "name": d.get("source_name_vi") or d.get("source_name"),
                    "name_status": d.get("source_status"), "category": None, "country": None, "ads": len(d.get("ad_ids") or []),
                    "active_ads": len(d.get("ad_ids") or []), "unique_ads": len(d.get("ad_ids") or []), "advertisers": 0,
                    "cover_creative_id": None, "opportunity_score": 0, "recommendation": "—"}
            rows.append({**base, "keep_id": tgt.id, "a": side(tgt), "b": gone})
            continue
        a, b = prods.get(l.a_id), prods.get(l.b_id)
        if not a or not b:
            continue
        mini = lambda p: {"id": p.id, "status": p.name_status or "SUGGESTED", "manual": bool(p.manual_override), "n_ads": (p.features or {}).get("total_ads", 0)}
        rows.append({**base, "keep_id": clustering.choose_target(mini(a), mini(b))[1]["id"], "a": side(a), "b": side(b)})
    counts = dict(db.execute(select(ProductLink.status, func.count()).where(visible_links()).group_by(ProductLink.status)).all())
    return {"rows": rows, "total": total, "counts": counts}


@router.post("/candidates/{lid}/accept")
def accept(lid: int, body: ByIn, db: Session = Depends(get_db)):
    CM.lock(db)
    link = db.get(ProductLink, lid)
    if not link or link.status != "pending":
        raise HTTPException(400, "Cặp này không còn chờ xác nhận")
    try:
        tgt, ev = clustering.accept(db, link, str(db.info.get("user_id") or "admin-integration"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True, "target_id": tgt, "cluster_id": tgt, "event_id": ev}


@router.post("/candidates/{lid}/reject")
def reject(lid: int, body: ByIn, db: Session = Depends(get_db)):
    CM.lock(db)
    link = db.get(ProductLink, lid)
    if not link or link.status not in ("pending", "blocked", "similar"):
        raise HTTPException(400, "Cặp này không còn chờ xác nhận")
    clustering.record_cannot_link(db, link.a_id, link.b_id, str(db.info.get("user_id") or "admin-integration"))
    db.commit()
    return {"ok": True}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {**clustering.metrics(db), "links": dict(db.execute(select(ProductLink.status, func.count()).where(visible_links()).group_by(ProductLink.status)).all())}


@router.post("/events/{eid}/undo")
def undo(eid: int, body: ByIn, db: Session = Depends(get_db)):
    CM.lock(db)
    event = db.get(Event, eid)
    if not event or event.type != "PRODUCT_CLUSTERED":
        raise HTTPException(404, "Không tìm thấy thao tác gom cluster")
    try:
        CM.undo(db, event, str(db.info.get("user_id") or "admin-integration"))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    return {"ok": True}


@router.get("/groups")
def groups(limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    q = select(AdClusterMembership.cluster_id, func.count().label("ads"),
               func.count(func.distinct(AdClusterMembership.source_product_id)).label("products"),
               func.min(AdClusterMembership.confidence).label("confidence")).group_by(AdClusterMembership.cluster_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(func.count().desc(), AdClusterMembership.cluster_id).offset(offset).limit(limit)).all()
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r.cluster_id for r in rows])))}
    return {"total": total, "rows": [{**dict(r._mapping), "name": N.display_name(prods[r.cluster_id])} for r in rows]}


@router.get("/groups/{cid}")
def group_ads(cid: int, limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    node = db.get(ProductCluster, cid)
    if not node:
        raise HTTPException(404)
    q = select(AdClusterMembership, Ad).join(Ad, Ad.id == AdClusterMembership.ad_id).where(AdClusterMembership.cluster_id == cid)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    return {"cluster_id": cid, "merged_into_id": node.merged_into_id, "total": total, "rows": [
        {"ad_id": a.id, "product_id": a.product_id, "advertiser_id": a.advertiser_id,
         "title": a.title, "source": a.source, "external_id": a.external_id,
         "confidence": m.confidence, "method": m.method, "evidence": m.evidence}
        for m, a in db.execute(q.order_by(Ad.id).offset(offset).limit(limit))]}


@router.get("/duplicate-ads")
def duplicate_ads(limit: int = Query(40, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return {"total": db.scalar(select(func.count()).select_from(DuplicateAdLink)), "rows": [
        {"a_id": r.a_id, "b_id": r.b_id, "relation_kind": "duplicate_ad", "confidence": r.confidence, "evidence": r.evidence}
        for r in db.scalars(select(DuplicateAdLink).order_by(DuplicateAdLink.a_id, DuplicateAdLink.b_id).offset(offset).limit(limit))]}


@router.get("/impure")
def impure(refresh: bool = False, db: Session = Depends(get_db)):
    """Existing products whose ads evidence different products with nothing tying them together — split suggestions."""
    if refresh or time.time() - _impure["at"] > 900:
        _impure.update(at=time.time(), rows=clustering.detect_impure(db))
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r["product_id"] for r in _impure["rows"]])))}
    return {"rows": [{**r, "product": side(prods[r["product_id"]])} for r in _impure["rows"] if r["product_id"] in prods], "total": len(_impure["rows"])}
