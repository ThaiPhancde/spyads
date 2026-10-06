"""Product Fit: MKT vote (taste), Opportunity Radar, AI creative analysis, Attribution chain (đơn → creative)."""
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import realtime
from ..db import get_db
from ..models import MktVote, Order, Product
from ..services import creative_ai
from ..services.potential import VOTE_VALUE

router = APIRouter(prefix="/api")


class VoteIn(BaseModel):
    user: str
    decision: str
    reason: str | None = None
    market: str | None = None


@router.post("/products/{pid}/votes")
def vote(pid: int, body: VoteIn, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404)
    d = body.decision.upper()
    if d not in VOTE_VALUE:
        raise HTTPException(400, f"decision ∈ {list(VOTE_VALUE)}")
    if not body.user.strip():
        raise HTTPException(400, "cần tên người vote")
    db.add(MktVote(product_id=pid, user=body.user.strip()[:64], decision=d, reason=body.reason, market=body.market or p.country))
    db.commit()
    realtime.recompute_products(db, [pid], reason="mkt_vote")
    return votes(pid, db)


@router.get("/products/{pid}/votes")
def votes(pid: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(MktVote).where(MktVote.product_id == pid).order_by(MktVote.created_at.desc())).all()
    p = db.get(Product, pid)
    return {"mkt_appeal": p.mkt_appeal if p else None,
            "rows": [{"id": v.id, "user": v.user, "decision": v.decision, "reason": v.reason, "market": v.market, "created_at": v.created_at} for v in rows]}


@router.post("/products/{pid}/analyze-creative")
def analyze_creative(pid: int, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404)
    res = creative_ai.analyze_product(db, p)
    if not res["ok"]:
        raise HTTPException(400, res["reason"])
    db.commit()
    realtime.recompute_products(db, [pid], reason="creative_ai")
    return res["analysis"]


@router.get("/radar")
def radar(country: str | None = None, category: str | None = None, db: Session = Depends(get_db)):
    """Opportunity Radar: X = Market Validation (External Win), Y = Wave (AI) nếu có, ngược lại Novelty."""
    pts = []
    for p in db.scalars(select(Product)).all():
        f = p.features or {}
        if country and country not in (f.get("markets") or [p.country]):
            continue
        if category and p.category != category:
            continue
        has_wave = p.wave_potential is not None
        pts.append({"id": p.id, "name": p.canonical_name, "code": p.product_code, "x": p.external_win_score,
                    "y": p.wave_potential if has_wave else p.novelty_score, "y_kind": "wave" if has_wave else "novelty",
                    "classification": p.classification, "recommendation": p.recommendation, "opportunity": p.opportunity_score,
                    "confidence": p.confidence_score, "advertisers": f.get("advertiser_count", 0), "compliance_risk": p.compliance_risk})
    counts = defaultdict(int)
    for x in pts:
        counts[x["classification"]] += 1
    return {"points": pts, "counts": dict(counts), "thresholds": {"x": 55, "y": 60}}


@router.get("/attribution")
def attribution(group: str = "ad", product_id: int | None = None, days: int = 90, db: Session = Depends(get_db)):
    """Truy ngược đơn về creative/campaign: Delivered chưa chắc là Win — xem tỷ lệ giao/từ chối và lợi nhuận gộp theo từng ad."""
    q = select(Order).where(Order.created_at > datetime.utcnow() - timedelta(days=days))
    if product_id:
        q = q.where(Order.product_id == product_id)
    agg: dict = defaultdict(lambda: defaultdict(float))
    for o in db.scalars(q).all():
        key = (o.ad_ref if group == "ad" else o.campaign_ref) or "(không có)"
        a = agg[(key, o.product_id)]
        a["orders"] += 1
        a["confirmed"] += o.status not in ("pending", "cancelled")
        a["delivered"] += o.status in ("delivered", "returned")
        a["refused"] += o.status == "refused"
        a["returned"] += o.status == "returned"
        if o.status == "delivered" and not o.refunded:
            a["revenue"] += o.amount
            a["cost"] += o.cogs + o.shipping_cost + o.cod_fee + o.sales_commission
        if o.status in ("returned", "refused"):
            a["cost"] += o.return_cost + o.shipping_cost
    products = {p.id: p.canonical_name for p in db.scalars(select(Product)).all()}
    rows = []
    for (key, pid), a in agg.items():
        n = a["orders"]
        closed = a["delivered"] + a["refused"]
        rows.append({"ref": key, "product_id": pid, "product": products.get(pid), "orders": int(n), "confirm_rate": round(a["confirmed"] / n, 3),
                     "delivery_rate": round(a["delivered"] / a["confirmed"], 3) if a["confirmed"] else None,
                     "refusal_rate": round(a["refused"] / closed, 3) if closed else None, "revenue_delivered": round(a["revenue"], 2),
                     "gross_profit_before_ads": round(a["revenue"] - a["cost"], 2)})
    rows.sort(key=lambda r: -r["orders"])
    return {"group": group, "rows": rows[:200]}
