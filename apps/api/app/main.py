"""Market Intelligence OS — API."""
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .markets import ad_scope_filter, product_in_scope
from .models import (ad_in_country, Ad, Advertiser, AdvertiserDailySnapshot, Alert, Comment, Connector, CreativeDailySnapshot, Experiment,
                     LifecycleEvent, MarketDailySnapshot, Order, PipelineRun, Product, ProductDailySnapshot, RawRecord, Store)
from .services import agent, llm
from .services.connectors import (CSV_TEMPLATES, ENTITY_TYPES, PROVIDERS, ensure_default_connectors, manual_url_record,
                                  normalize_pending, parse_csv, store_raw, sync_connector)
from .services.decision import BENCH, FAILURE_TYPES, LIFECYCLE_STATES, TH, TRANSITIONS, experiment_metrics, learning_profile, transition
from .services.engine import is_hidden_winner, is_potential_winner, score_all
from .services.enrichment import ASPECTS, analyze_comments
from .services.entity_resolution import merge_products
from .services.pipeline import run_pipeline
from .services.scoring import confidence_label, win_label

app = FastAPI(title="Market Intelligence OS", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


from .routers.intel import router as intel_router  # noqa: E402

app.include_router(intel_router)


@app.on_event("startup")
async def _startup():
    import asyncio
    import logging

    from .db import SessionLocal, auto_migrate
    from .media import enqueue_pending
    from .realtime import scheduler_loop

    logging.basicConfig(level=logging.INFO)
    Base.metadata.create_all(engine)
    auto_migrate()
    with SessionLocal() as db:
        ensure_default_connectors(db)
        from .services import adsignals

        adsignals.refresh(db)  # per-ad force / variation / target-market flags (cheap; keeps them in sync with .env)
        db.commit()
    enqueue_pending()
    if os.getenv("DISABLE_SCHEDULER", "").lower() not in ("1", "true"):
        app.state.scheduler = asyncio.create_task(scheduler_loop())


# ============================================================ serializers
def product_card(p: Product) -> dict:
    f = p.features or {}
    return {
        "id": p.id, "product_code": p.product_code, "name": p.canonical_name, "category": p.category,
        "country": p.country, "markets": f.get("markets", []), "price": f.get("avg_price") or p.price, "currency": p.currency,
        "lifecycle": p.lifecycle_status, "recommendation": p.recommendation, "reasons": p.recommendation_reasons,
        "win_score": p.win_score, "external_win_score": p.external_win_score, "internal_win_score": p.internal_win_score,
        "rarity_score": p.rarity_score, "rare_winner_score": p.rare_winner_score, "saturation_score": p.saturation_score,
        "saturation_state": p.saturation_state, "opportunity_score": p.opportunity_score, "confidence_score": p.confidence_score,
        "confidence_label": confidence_label(p.confidence_score), "win_label": win_label(p.win_score),
        "customer_rejection_score": p.customer_rejection_score, "sentiment_score": p.sentiment_score,
        "advertisers": f.get("advertiser_count", 0), "active_ads": f.get("active_ads", 0), "new_ads_7d": f.get("new_ads_7d", 0),
        "growth_7d": f.get("creative_growth_7d", 0), "stores": f.get("store_count", 0), "traffic_growth": f.get("traffic_growth"),
        "refusal_rate": f.get("refusal_rate"), "roas": f.get("roas"), "tags": p.tags or [],
        "first_seen_at": p.first_seen_at, "last_seen_at": p.last_seen_at, "hidden_winner": is_hidden_winner(p),
    }


# ============================================================ meta
@app.get("/api/meta")
def meta(db: Session = Depends(get_db)):
    countries = sorted({c for p in db.scalars(select(Product)).all() for c in (p.features or {}).get("markets", [])})
    cats = sorted({c for c in db.scalars(select(Product.category).distinct()).all() if c})
    from .markets import REGION_LABEL, REGIONS, targets as _targets

    countries = _targets()
    return {"countries": countries, "regions": {r: {"label": REGION_LABEL[r], "countries": REGIONS[r]} for r in REGIONS}, "categories": cats, "lifecycle_states": LIFECYCLE_STATES, "transitions": {k: sorted(v) for k, v in TRANSITIONS.items()},
            "failure_types": FAILURE_TYPES, "aspects": ASPECTS, "recommendations": ["TEST", "WATCH", "HOLD", "ITERATE", "SCALE", "STOP"],
            "thresholds": TH, "benchmarks": BENCH, "llm": llm.available(), "llm_model": llm.MODEL if llm.available() else None,
            "entity_types": ENTITY_TYPES}


# ============================================================ §22 Daily Pulse
@app.get("/api/dashboard/pulse")
def pulse(country: str | None = None, db: Session = Depends(get_db)):
    products = [p for p in db.scalars(select(Product)).all() if product_in_scope(p, country)]
    since = datetime.utcnow() - timedelta(days=1)
    ad_q = select(Ad).where(Ad.is_internal.is_(False), Ad.first_seen_at > since)
    _f = ad_scope_filter(country)
    if _f is not None:
        ad_q = ad_q.where(_f)
    new_ads = db.scalars(ad_q).all()
    adv_first = db.execute(select(Ad.advertiser_id, func.min(Ad.first_seen_at)).where(Ad.is_internal.is_(False))
                           .group_by(Ad.advertiser_id)).all()
    new_adv_ids = {a for a, t in adv_first if t and t > since}
    if country:
        new_adv_ids &= {a.advertiser_id for a in new_ads}
    new_products = [p for p in products if p.first_seen_at > since]

    exps = db.scalars(select(Experiment)).all()
    if country:
        exps = [e for e in exps if e.market == country]
    running = [e for e in exps if e.ended_at is None]
    st = Counter(e.status for e in exps)
    orders = db.scalars(select(Order).where(Order.created_at > datetime.utcnow() - timedelta(days=14))).all()
    if country:
        orders = [o for o in orders if o.country == country]
    att = sum(1 for o in orders if o.status in ("delivered", "refused", "failed", "returned"))
    ref = sum(1 for o in orders if o.status == "refused")
    prev_orders = db.scalars(select(Order).where(Order.created_at <= datetime.utcnow() - timedelta(days=14),
                                                 Order.created_at > datetime.utcnow() - timedelta(days=35))).all()
    if country:
        prev_orders = [o for o in prev_orders if o.country == country]
    patt = sum(1 for o in prev_orders if o.status in ("delivered", "refused", "failed", "returned"))
    pref = sum(1 for o in prev_orders if o.status == "refused")

    hidden = sorted([p for p in products if is_hidden_winner(p)], key=lambda p: -p.rare_winner_score)
    alerts = db.scalars(select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc()).limit(8)).all()
    trend = db.execute(select(ProductDailySnapshot.date, func.sum(ProductDailySnapshot.new_ads), func.sum(ProductDailySnapshot.active_ads))
                       .group_by(ProductDailySnapshot.date).order_by(ProductDailySnapshot.date)).all()
    recs = Counter(p.recommendation for p in products)
    return {
        "country": country,
        "kpis": {
            "products_monitored": len(products), "new_products": len(new_products), "new_active_ads": len(new_ads),
            "new_advertisers": len(new_adv_ids), "potential_winners": sum(1 for p in products if is_potential_winner(p)),
            "hidden_winners": len(hidden), "saturated_products": sum(1 for p in products if p.saturation_state in ("Highly Saturated", "Declining")),
        },
        "internal_tests": {"testing": len(running), "winning": st["WIN"], "promising": st["PROMISING"], "failed": st["FAILED"],
                           "waiting_data": st["WAITING"],
                           "cod_refusal": round(ref / att, 3) if att else None, "cod_refusal_prev": round(pref / patt, 3) if patt else None},
        "recommendations": dict(recs),
        "top_hidden_winners": [product_card(p) for p in hidden[:5]],
        "top_opportunities": [product_card(p) for p in sorted(products, key=lambda p: -p.opportunity_score)[:8]],
        "alerts": [alert_dict(a, db) for a in alerts],
        "ads_trend": [{"date": d.isoformat(), "new_ads": n or 0, "active_ads": a or 0} for d, n, a in trend],
    }


# ============================================================ Market Radar
@app.get("/api/markets/radar")
def market_radar(db: Session = Depends(get_db)):
    last = db.scalar(select(func.max(MarketDailySnapshot.date)))
    if not last:
        return {"date": None, "rows": [], "countries": []}
    def at(d):
        return {(s.country, s.category): s for s in db.scalars(select(MarketDailySnapshot).where(MarketDailySnapshot.date == d))}
    now, w1, w4 = at(last), at(last - timedelta(days=7)), at(last - timedelta(days=28))
    rows = []
    for key, s in now.items():
        prev, old = w1.get(key), w4.get(key)
        rows.append({
            "country": s.country, "category": s.category, "products": s.products, "active_ads": s.active_ads,
            "advertisers": s.advertisers, "potential_winners": s.potential_winners, "hidden_winners": s.hidden_winners,
            "avg_opportunity": s.avg_opportunity,
            "ads_growth_7d": round(s.active_ads / prev.active_ads - 1, 3) if prev and prev.active_ads else None,
            "ads_growth_28d": round(s.active_ads / old.active_ads - 1, 3) if old and old.active_ads else None,
            "advertiser_growth_7d": round(s.advertisers / prev.advertisers - 1, 3) if prev and prev.advertisers else None,
        })
    rows.sort(key=lambda r: -(r["ads_growth_7d"] or 0))
    countries = defaultdict(lambda: Counter())
    for r in rows:
        c = countries[r["country"]]
        for k in ("products", "active_ads", "advertisers", "potential_winners", "hidden_winners"):
            c[k] += r[k]
    series = db.execute(select(MarketDailySnapshot.date, MarketDailySnapshot.country, func.sum(MarketDailySnapshot.active_ads))
                        .where(MarketDailySnapshot.date >= last - timedelta(days=30))
                        .group_by(MarketDailySnapshot.date, MarketDailySnapshot.country)).all()
    ts = defaultdict(dict)
    for d, c, n in series:
        ts[d.isoformat()][c] = n
    funnel_by_country: dict[str, Counter] = defaultdict(Counter)
    for countries_json, primary, fn in db.execute(select(Ad.countries, Ad.country, Ad.funnel).where(Ad.is_active.is_(True), Ad.is_internal.is_(False))):
        for c in set((countries_json or []) + ([primary] if primary else [])):
            funnel_by_country[c][fn or "other"] += 1
    return {"date": last.isoformat(), "rows": rows, "funnels": {k: dict(v) for k, v in funnel_by_country.items()},
            "countries": sorted(({"country": k, **v} for k, v in countries.items()), key=lambda x: -x["active_ads"]),
            "series": [{"date": d, **v} for d, v in sorted(ts.items())]}


# ============================================================ Product Explorer
@app.get("/api/products")
def list_products(q: str | None = None, country: str | None = None, category: str | None = None,
                  recommendation: str | None = None, lifecycle: str | None = None, min_win: float | None = None,
                  max_advertisers: int | None = None, min_confidence: float | None = None, saturation_state: str | None = None,
                  hidden_only: bool = False, sort: str = "opportunity_score", order: str = "desc",
                  limit: int = Query(50, le=500), offset: int = 0, db: Session = Depends(get_db)):
    rows = []
    for p in db.scalars(select(Product)).all():
        f = p.features or {}
        if q and q.lower() not in p.canonical_name.lower() and q.upper() != p.product_code and not any(q.lower() in a.name.lower() for a in p.aliases):
            continue
        if not product_in_scope(p, country):
            continue
        if category and p.category != category:
            continue
        if recommendation and p.recommendation != recommendation:
            continue
        if lifecycle and p.lifecycle_status != lifecycle:
            continue
        if min_win is not None and p.win_score < min_win:
            continue
        if max_advertisers is not None and f.get("advertiser_count", 0) > max_advertisers:
            continue
        if min_confidence is not None and p.confidence_score < min_confidence:
            continue
        if saturation_state and p.saturation_state != saturation_state:
            continue
        if hidden_only and not is_hidden_winner(p):
            continue
        rows.append(product_card(p))
    with_val = sorted((r for r in rows if r.get(sort) is not None), key=lambda r: r[sort], reverse=(order == "desc"))
    rows = with_val + [r for r in rows if r.get(sort) is None]
    return {"total": len(rows), "rows": rows[offset: offset + limit]}


@app.get("/api/products/hidden-winners")
def hidden_winners(tag: str | None = None, db: Session = Depends(get_db)):
    rows = [product_card(p) for p in db.scalars(select(Product)).all() if is_hidden_winner(p) and product_in_scope(p, None)]
    if tag:
        rows = [r for r in rows if tag in r["tags"]]
    rows.sort(key=lambda r: -r["rare_winner_score"])
    tags = Counter(t for r in rows for t in r["tags"])
    # also show "near misses" so the team sees what is about to qualify
    near = [product_card(p) for p in db.scalars(select(Product)).all()
            if not is_hidden_winner(p) and p.rare_winner_score >= 45 and (p.features or {}).get("advertiser_count", 99) <= 25]
    near.sort(key=lambda r: -r["rare_winner_score"])
    return {"rows": rows, "tags": dict(tags), "near_misses": near[:10]}


# ============================================================ Product 360
@app.get("/api/products/{pid}")
def product_360(pid: int, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404, "Product not found")
    f = p.features or {}
    ads = db.scalars(select(Ad).where(Ad.product_id == pid).order_by(Ad.first_seen_at.desc())).all()
    advs = {a.id: a for a in db.scalars(select(Advertiser).where(Advertiser.id.in_({a.advertiser_id for a in ads if a.advertiser_id})))}
    by_adv = defaultdict(list)
    for a in ads:
        if a.advertiser_id:
            by_adv[a.advertiser_id].append(a)
    advertisers = sorted(({
        "id": aid, "name": advs[aid].name, "country": advs[aid].country, "platform": advs[aid].platform,
        "ads": len(lst), "active_ads": sum(1 for a in lst if a.is_active),
        "first_seen": min(a.first_seen_at for a in lst), "new_7d": sum(1 for a in lst if a.first_seen_at > datetime.utcnow() - timedelta(days=7)),
        "avg_price": round(sum(a.price for a in lst if a.price) / max(1, sum(1 for a in lst if a.price)), 2),
    } for aid, lst in by_adv.items()), key=lambda r: -r["active_ads"])

    creatives = {}
    for a in ads:
        k = a.creative_fingerprint or a.id
        c = creatives.setdefault(k, {"fingerprint": k, "ad_text": a.ad_text, "media_type": a.media_type, "hook": a.hook,
                                     "angle": a.angle, "offer": a.offer, "count": 0, "active": 0, "first_seen": a.first_seen_at,
                                     "longest_days": 0, "engagement": 0, "platforms": set(), "countries": set()})
        c["count"] += 1
        c["active"] += 1 if a.is_active else 0
        c["first_seen"] = min(c["first_seen"], a.first_seen_at)
        c["longest_days"] = max(c["longest_days"], (a.last_seen_at - a.first_seen_at).days)
        c["engagement"] += a.likes + 3 * a.comments_count + 5 * a.shares
        c["platforms"].add(a.platform)
        if a.country:
            c["countries"].add(a.country)
    creative_lib = sorted(({**c, "platforms": sorted(c["platforms"]), "countries": sorted(c["countries"])} for c in creatives.values()),
                          key=lambda c: (-c["active"], -c["longest_days"]))[:40]

    snaps = db.scalars(select(ProductDailySnapshot).where(ProductDailySnapshot.product_id == pid).order_by(ProductDailySnapshot.date)).all()
    stores = db.scalars(select(Store).where(Store.product_id == pid).order_by(Store.estimated_traffic.desc())).all()
    exps = db.scalars(select(Experiment).where(Experiment.product_id == pid).order_by(Experiment.started_at.desc())).all()
    orders = db.scalars(select(Order).where(Order.product_id == pid)).all()
    events = db.scalars(select(LifecycleEvent).where(LifecycleEvent.product_id == pid).order_by(LifecycleEvent.created_at.desc())).all()
    by_country = Counter(a.country for a in ads if a.is_active and not a.is_internal)
    hooks = Counter(a.hook for a in ads if a.is_active)
    angles = Counter(a.angle for a in ads if a.is_active)
    offers = Counter(o for a in ads if a.is_active and a.offer for o in a.offer.split(","))
    price_points = sorted([{"date": a.first_seen_at.date().isoformat(), "price": a.price, "country": a.country} for a in ads if a.price],
                          key=lambda x: x["date"])
    st = Counter(o.status for o in orders)
    return {
        "product": {**product_card(p), "brand": p.brand, "cost": p.cost, "language": p.language,
                    "search_trend": p.search_trend, "keyword_competition": p.keyword_competition,
                    "aliases": [{"name": a.name, "source": a.source, "match_score": a.match_score} for a in p.aliases]},
        "features": {k: v for k, v in f.items() if not k.startswith("_")},
        "breakdown": f.get("_breakdown", {}), "derived": f.get("_scores", {}),
        "market_performance": [{"country": c, "active_ads": n} for c, n in by_country.most_common()],
        "advertisers": advertisers[:50],
        "creatives": creative_lib,
        "creative_mix": {"hooks": dict(hooks.most_common()), "angles": dict(angles.most_common()), "offers": dict(offers.most_common())},
        "stores": [{"domain": s.domain, "country": s.country, "price": s.price, "traffic": s.estimated_traffic,
                    "traffic_growth": s.traffic_growth} for s in stores],
        "pricing": {"points": price_points[-200:], "avg": f.get("avg_price"), "trend": f.get("price_trend")},
        "comments": comment_summary(db, [pid]),
        "experiments": [experiment_dict(e) for e in exps],
        "orders": {"total": len(orders), "status": dict(st), "refusal_reasons": f.get("refusal_reasons", {})},
        "timeseries": [{"date": s.date.isoformat(), "active_ads": s.active_ads, "new_ads": s.new_ads, "advertisers": s.advertisers,
                        "external_win": s.external_win_score, "saturation": s.saturation_score, "opportunity": s.opportunity_score,
                        "rarity": s.rarity_score, "negative_comments": s.negative_comments, "comments": s.comments} for s in snaps],
        "lifecycle": {"current": p.lifecycle_status, "allowed": sorted(TRANSITIONS.get(p.lifecycle_status, [])),
                      "events": [{"from": e.from_status, "to": e.to_status, "reason": e.reason, "at": e.created_at} for e in events]},
        "recent_ads": [{"id": a.id, "advertiser": advs[a.advertiser_id].name if a.advertiser_id in advs else None, "country": a.country,
                        "platform": a.platform, "text": a.ad_text, "price": a.price, "first_seen": a.first_seen_at,
                        "active": a.is_active, "source": a.source, "landing_url": a.landing_url} for a in ads[:30]],
    }


class LifecycleIn(BaseModel):
    to: str
    reason: str | None = None
    force: bool = False


@app.post("/api/products/{pid}/lifecycle")
def set_lifecycle(pid: int, body: LifecycleIn, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404)
    try:
        transition(db, p, body.to, body.reason or "manual", force=body.force)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True, "lifecycle": p.lifecycle_status}


@app.post("/api/products/{pid}/explain")
def explain(pid: int, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404)
    return {"explanation": agent.explain_product(p), "engine": "claude" if llm.available() else "template"}


class MergeIn(BaseModel):
    source_id: int
    target_id: int


@app.post("/api/products/merge")
def merge(body: MergeIn, db: Session = Depends(get_db)):
    s, t = db.get(Product, body.source_id), db.get(Product, body.target_id)
    if not s or not t or s.id == t.id:
        raise HTTPException(400, "invalid products")
    merge_products(db, s, t)
    score_all(db)
    db.commit()
    return {"ok": True, "target_id": t.id}


# ============================================================ Test Lab (§12-13)
def experiment_dict(e: Experiment) -> dict:
    m = experiment_metrics(e)
    return {
        "id": e.id, "product_id": e.product_id, "name": e.name, "market": e.market, "platform": e.platform, "creative": e.creative,
        "creative_type": e.creative_type, "angle": e.angle, "offer": e.offer, "funnel": e.funnel, "sell_price": e.sell_price,
        "unit_cost": e.unit_cost, "started_at": e.started_at, "ended_at": e.ended_at, "running": e.ended_at is None,
        "spend": e.spend, "impressions": e.impressions, "clicks": e.clicks, "landing_views": e.landing_views, "atc": e.atc,
        "checkout": e.checkout, "purchase": e.purchase, "revenue": e.revenue, "confirmed_orders": e.confirmed_orders,
        "shipped": e.shipped, "delivered": e.delivered, "refused": e.refused, "returned": e.returned,
        "ads_submitted": e.ads_submitted, "ads_rejected": e.ads_rejected,
        "status": e.status, "failure_types": e.failure_types or [], "diagnosis": e.diagnosis or [], "decision": e.decision,
        "notes": e.notes, "metrics": {k: (round(v, 4) if v is not None else None) for k, v in m.items()},
    }


@app.get("/api/experiments")
def list_experiments(status: str | None = None, failure_type: str | None = None, db: Session = Depends(get_db)):
    exps = db.scalars(select(Experiment).order_by(Experiment.started_at.desc())).all()
    products = {p.id: p for p in db.scalars(select(Product)).all()}
    rows = []
    for e in exps:
        if status and e.status != status:
            continue
        if failure_type and failure_type not in (e.failure_types or []):
            continue
        p = products.get(e.product_id)
        rows.append({**experiment_dict(e), "product_name": p.canonical_name if p else None, "product_code": p.product_code if p else None})
    summary = Counter(e.status for e in exps)
    failures = Counter(f for e in exps for f in (e.failure_types or []))
    return {"rows": rows, "summary": dict(summary), "failures": dict(failures.most_common()),
            "running": sum(1 for e in exps if e.ended_at is None)}


class ExperimentIn(BaseModel):
    product_id: int
    name: str | None = None
    market: str | None = None
    platform: str | None = "meta"
    creative: str | None = None
    creative_type: str | None = None
    angle: str | None = None
    offer: str | None = None
    funnel: str | None = "COD"
    sell_price: float | None = None
    unit_cost: float | None = None
    started_at: date | None = None
    ended_at: date | None = None
    spend: float | None = None
    impressions: int | None = None
    clicks: int | None = None
    landing_views: int | None = None
    atc: int | None = None
    checkout: int | None = None
    purchase: int | None = None
    revenue: float | None = None
    confirmed_orders: int | None = None
    shipped: int | None = None
    delivered: int | None = None
    refused: int | None = None
    returned: int | None = None
    ads_submitted: int | None = None
    ads_rejected: int | None = None
    notes: str | None = None


@app.post("/api/experiments")
def create_experiment(body: ExperimentIn, db: Session = Depends(get_db)):
    p = db.get(Product, body.product_id)
    if not p:
        raise HTTPException(404, "product not found")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    data.setdefault("name", f"EXP {p.canonical_name} · {body.market or p.country}")
    data.setdefault("started_at", date.today())
    e = Experiment(**data)
    db.add(e)
    db.flush()
    score_all(db)
    db.commit()
    return experiment_dict(e)


@app.patch("/api/experiments/{eid}")
def update_experiment(eid: int, body: dict, db: Session = Depends(get_db)):
    e = db.get(Experiment, eid)
    if not e:
        raise HTTPException(404)
    allowed = set(ExperimentIn.model_fields) - {"product_id"}
    for k, v in body.items():
        if k in allowed:
            if k in ("started_at", "ended_at") and v:
                v = date.fromisoformat(str(v)[:10])
            setattr(e, k, v if v != "" else None)
    db.flush()
    score_all(db)
    db.commit()
    return experiment_dict(e)


# ============================================================ Comment Intelligence (§17)
def comment_summary(db: Session, product_ids: list[int] | None = None, days: int | None = None) -> dict:
    q = select(Comment).where(Comment.overall.is_not(None))
    if product_ids is not None:
        q = q.where(Comment.product_id.in_(product_ids))
    if days:
        q = q.where(Comment.created_at > datetime.utcnow() - timedelta(days=days))
    cs = db.scalars(q).all()
    n = len(cs)
    overall = Counter(c.overall for c in cs)
    pos_asp = Counter(a for c in cs for a, v in (c.aspects or {}).items() if v == "positive")
    neg_asp = Counter(a for c in cs for a, v in (c.aspects or {}).items() if v == "negative")
    tp, tn = sum(pos_asp.values()) or 1, sum(neg_asp.values()) or 1
    weekly = defaultdict(Counter)
    for c in cs:
        wk = (c.created_at.date() - timedelta(days=c.created_at.weekday())).isoformat()
        weekly[wk][c.overall] += 1
    examples = defaultdict(list)
    for c in sorted(cs, key=lambda c: c.created_at, reverse=True):
        for a, v in (c.aspects or {}).items():
            key = f"{a}:{v}"
            if len(examples[key]) < 3 and c.text not in examples[key]:
                examples[key].append(c.text)
    return {
        "analysed": n,
        "overall": {k: round(overall[k] / n, 3) if n else 0 for k in ("positive", "neutral", "negative")},
        "top_positive": [{"aspect": a, "share": round(c / tp, 3), "count": c} for a, c in pos_asp.most_common(6)],
        "top_complaints": [{"aspect": a, "share": round(c / tn, 3), "count": c} for a, c in neg_asp.most_common(8)],
        "purchase_intent": dict(Counter(c.purchase_intent for c in cs)),
        "questions": sum(1 for c in cs if c.is_question),
        "weekly": [{"week": w, **v} for w, v in sorted(weekly.items())],
        "examples": dict(examples),
        "engines": dict(Counter(c.analyzed_by for c in cs)),
    }


@app.get("/api/comments/intelligence")
def comments_intel(product_id: int | None = None, country: str | None = None, category: str | None = None,
                   days: int | None = None, db: Session = Depends(get_db)):
    ids = None
    if product_id:
        ids = [product_id]
    elif country or category:
        ids = [p.id for p in db.scalars(select(Product)).all()
               if (not country or country in ((p.features or {}).get("markets") or [p.country])) and (not category or p.category == category)]
    summary = comment_summary(db, ids, days)
    # products with worst sentiment / rising negativity
    worst = []
    for p in db.scalars(select(Product)).all():
        f = p.features or {}
        if ids is not None and p.id not in ids:
            continue
        if f.get("comments", 0) >= 20:
            worst.append({"id": p.id, "name": p.canonical_name, "product_code": p.product_code, "comments": f["comments"],
                          "negative_rate": f.get("negative_comment_rate"), "negative_rate_7d": f.get("negative_rate_7d"),
                          "negative_rate_prev": f.get("negative_rate_prev"), "complaint_rate": f.get("complaint_rate")})
    worst.sort(key=lambda r: -(r["negative_rate"] or 0))
    lq = select(Comment).order_by(Comment.created_at.desc()).limit(40)
    if ids is not None:
        lq = lq.where(Comment.product_id.in_(ids))
    latest = db.scalars(lq).all()
    return {**summary, "products": worst[:20],
            "latest": [{"id": c.id, "product_id": c.product_id, "text": c.text, "overall": c.overall, "aspects": c.aspects,
                        "purchase_intent": c.purchase_intent, "source": c.source, "created_at": c.created_at} for c in latest]}


class AnalyzeIn(BaseModel):
    texts: list[str]


@app.post("/api/comments/analyze")
def analyze(body: AnalyzeIn):
    results, eng = analyze_comments(body.texts[:200])
    return {"engine": eng, "results": [{"text": t, **r} for t, r in zip(body.texts, results)]}


# ============================================================ Competitor Radar
@app.get("/api/competitors")
def competitors(country: str | None = None, watched_only: bool = False, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    ads = db.scalars(select(Ad).where(Ad.is_internal.is_(False), Ad.advertiser_id.is_not(None))).all()
    by = defaultdict(list)
    for a in ads:
        by[a.advertiser_id].append(a)
    advs = {a.id: a for a in db.scalars(select(Advertiser)).all()}
    products = {p.id: p for p in db.scalars(select(Product)).all()}
    from .markets import expand
    from .models import ad_markets

    _codes = expand(country)
    _in_scope = lambda a: _codes is None or any(m in _codes for m in ad_markets(a))
    rows = []
    for aid, lst in by.items():
        adv = advs[aid]
        if not any(_in_scope(a) for a in lst):
            continue
        if watched_only and not adv.is_competitor_watched:
            continue
        active = [a for a in lst if a.is_active]
        n7 = sum(1 for a in lst if a.first_seen_at > now - timedelta(days=7))
        p7 = sum(1 for a in lst if now - timedelta(days=14) < a.first_seen_at <= now - timedelta(days=7))
        prods = Counter(a.product_id for a in active)
        rows.append({
            "id": aid, "name": adv.name, "country": adv.country, "platform": adv.platform, "page_url": adv.page_url,
            "watched": adv.is_competitor_watched, "total_ads": len(lst), "active_ads": len(active), "new_ads_7d": n7, "prev_7d": p7,
            "growth_7d": round(n7 / p7 - 1, 2) if p7 else (None if not n7 else 9.99),
            "products": len(prods), "countries": sorted({a.country for a in active if a.country}),
            "top_products": [{"id": pid, "name": products[pid].canonical_name, "ads": n} for pid, n in prods.most_common(3) if pid in products],
            "scaling": n7 >= 8 and n7 >= 2 * max(p7, 1),
        })
    rows.sort(key=lambda r: (-int(r["scaling"]), -r["new_ads_7d"], -r["active_ads"]))
    return {"rows": rows[:200], "total": len(rows), "scaling": sum(1 for r in rows if r["scaling"])}


@app.get("/api/competitors/{aid}")
def competitor_detail(aid: int, db: Session = Depends(get_db)):
    adv = db.get(Advertiser, aid)
    if not adv:
        raise HTTPException(404)
    snaps = db.scalars(select(AdvertiserDailySnapshot).where(AdvertiserDailySnapshot.advertiser_id == aid).order_by(AdvertiserDailySnapshot.date)).all()
    ads = db.scalars(select(Ad).where(Ad.advertiser_id == aid).order_by(Ad.first_seen_at.desc()).limit(50)).all()
    stores = db.scalars(select(Store).where(Store.advertiser_id == aid)).all()
    return {"advertiser": {"id": adv.id, "name": adv.name, "country": adv.country, "platform": adv.platform, "page_url": adv.page_url,
                           "watched": adv.is_competitor_watched},
            "timeseries": [{"date": s.date.isoformat(), "active_ads": s.active_ads, "new_ads": s.new_ads, "products": s.products} for s in snaps],
            "ads": [{"id": a.id, "product_id": a.product_id, "text": a.ad_text, "country": a.country, "first_seen": a.first_seen_at,
                     "active": a.is_active, "price": a.price} for a in ads],
            "stores": [{"domain": s.domain, "traffic": s.estimated_traffic, "growth": s.traffic_growth} for s in stores]}


@app.post("/api/competitors/{aid}/watch")
def watch(aid: int, watched: bool = True, db: Session = Depends(get_db)):
    adv = db.get(Advertiser, aid)
    if not adv:
        raise HTTPException(404)
    adv.is_competitor_watched = watched
    db.commit()
    return {"ok": True}


# ============================================================ Logistics / COD (§16) + Ad rejection (§15)
@app.get("/api/logistics")
def logistics(country: str | None = None, days: int = 60, db: Session = Depends(get_db)):
    q = select(Order).where(Order.created_at > datetime.utcnow() - timedelta(days=days))
    if country:
        q = q.where(Order.country == country)
    orders = db.scalars(q).all()
    st = Counter(o.status for o in orders)
    n = len(orders)
    confirmed = n - st["pending"] - st["cancelled"]
    shipped = st["shipped"] + st["delivered"] + st["refused"] + st["failed"] + st["returned"]
    delivered = st["delivered"] + st["returned"]
    attempts = delivered + st["refused"] + st["failed"]
    reasons = Counter(o.refusal_reason for o in orders if o.refusal_reason)
    products = {p.id: p for p in db.scalars(select(Product)).all()}
    per = defaultdict(Counter)
    for o in orders:
        per[o.product_id][o.status] += 1
    prod_rows = []
    for pid, c in per.items():
        att = c["delivered"] + c["returned"] + c["refused"] + c["failed"]
        sh = att + c["shipped"]
        p = products.get(pid)
        prod_rows.append({"id": pid, "name": p.canonical_name if p else pid, "orders": sum(c.values()),
                          "confirm_rate": round(1 - (c["pending"] + c["cancelled"]) / sum(c.values()), 3),
                          "delivery_rate": round((c["delivered"] + c["returned"]) / sh, 3) if sh else None,
                          "refusal_rate": round(c["refused"] / att, 3) if att else None,
                          "return_rate": round(c["returned"] / (c["delivered"] + c["returned"]), 3) if c["delivered"] + c["returned"] else None,
                          "customer_rejection_score": p.customer_rejection_score if p else None})
    prod_rows.sort(key=lambda r: -(r["refusal_rate"] or 0))
    weekly = defaultdict(Counter)
    for o in orders:
        weekly[(o.created_at.date() - timedelta(days=o.created_at.weekday())).isoformat()][o.status] += 1
    wk = []
    for w, c in sorted(weekly.items()):
        att = c["delivered"] + c["returned"] + c["refused"] + c["failed"]
        wk.append({"week": w, "orders": sum(c.values()), "refusal_rate": round(c["refused"] / att, 3) if att else None,
                   "delivery_rate": round((c["delivered"] + c["returned"]) / (att + c["shipped"]), 3) if att + c["shipped"] else None})
    exps = db.scalars(select(Experiment)).all()
    sub, rej = sum(e.ads_submitted for e in exps), sum(e.ads_rejected for e in exps)
    rejected_ads = db.scalars(select(Ad).where(Ad.rejected.is_(True))).all()
    return {
        "funnel": {"orders": n, "confirmed": confirmed, "shipped": shipped, "delivered": delivered, "refused": st["refused"],
                   "other_failed": st["failed"], "returned": st["returned"], "cancelled": st["cancelled"], "pending": st["pending"],
                   "refusal_rate": round(st["refused"] / attempts, 3) if attempts else None,
                   "delivery_rate": round(delivered / shipped, 3) if shipped else None,
                   "confirm_rate": round(confirmed / n, 3) if n else None},
        "refusal_reasons": [{"reason": r, "count": c, "share": round(c / max(1, sum(reasons.values())), 3)} for r, c in reasons.most_common()],
        "products": prod_rows, "weekly": wk,
        "ad_rejection": {"submitted": sub, "rejected": rej, "rate": round(rej / sub, 3) if sub else None,
                         "by_product": [{"product": products[e.product_id].canonical_name if e.product_id in products else e.product_id,
                                         "experiment": e.name, "submitted": e.ads_submitted, "rejected": e.ads_rejected,
                                         "rate": round(e.ads_rejected / e.ads_submitted, 3) if e.ads_submitted else None}
                                        for e in sorted(exps, key=lambda e: -(e.ads_rejected / e.ads_submitted if e.ads_submitted else 0))[:10]],
                         "reasons": dict(Counter(a.rejection_reason for a in rejected_ads if a.rejection_reason))},
    }


# ============================================================ Alerts
def alert_dict(a: Alert, db: Session | None = None) -> dict:
    return {"id": a.id, "type": a.type, "severity": a.severity, "title": a.title, "message": a.message, "product_id": a.product_id,
            "advertiser_id": a.advertiser_id, "data": a.data, "created_at": a.created_at, "is_read": a.is_read}


@app.get("/api/alerts")
def alerts(type: str | None = None, unread: bool = False, limit: int = 200, db: Session = Depends(get_db)):
    q = select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc())
    if type:
        q = q.where(Alert.type == type)
    if unread:
        q = q.where(Alert.is_read.is_(False))
    rows = db.scalars(q.limit(limit)).all()
    counts = dict(db.execute(select(Alert.type, func.count(Alert.id)).group_by(Alert.type)).all())
    return {"rows": [alert_dict(a) for a in rows], "counts": counts,
            "unread": db.scalar(select(func.count(Alert.id)).where(Alert.is_read.is_(False)))}


@app.post("/api/alerts/{aid}/read")
def read_alert(aid: int, db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if a:
        a.is_read = True
        db.commit()
    return {"ok": True}


@app.post("/api/alerts/read-all")
def read_all(db: Session = Depends(get_db)):
    for a in db.scalars(select(Alert).where(Alert.is_read.is_(False))).all():
        a.is_read = True
    db.commit()
    return {"ok": True}


# ============================================================ Learning loop (§30)
@app.get("/api/learning/profile")
def learning(db: Session = Depends(get_db)):
    return learning_profile(db)


# ============================================================ Connectors & data import (§3)
def connector_dict(c: Connector) -> dict:
    safe_cfg = {k: ("••••" if any(s in k.lower() for s in ("token", "key", "secret", "password")) and v else v) for k, v in (c.config or {}).items()}
    return {"id": c.id, "name": c.name, "provider": c.provider, "kind": c.kind, "group": c.group, "enabled": c.enabled,
            "status": c.status, "last_sync_at": c.last_sync_at, "last_sync_count": c.last_sync_count, "last_error": c.last_error,
            "config": safe_cfg}


@app.get("/api/connectors")
def connectors(db: Session = Depends(get_db)):
    raw_stats = dict(db.execute(select(RawRecord.entity_type, func.count(RawRecord.id)).group_by(RawRecord.entity_type)).all())
    pending = db.scalar(select(func.count(RawRecord.id)).where(RawRecord.processed.is_(False)))
    errors = db.scalars(select(RawRecord).where(RawRecord.error.is_not(None)).order_by(RawRecord.id.desc()).limit(10)).all()
    return {"rows": [connector_dict(c) for c in db.scalars(select(Connector).order_by(Connector.group, Connector.name)).all()],
            "providers": {k: {"name": v[0], "kind": v[1], "group": v[2]} for k, v in PROVIDERS.items()},
            "raw_lake": {"by_type": raw_stats, "pending": pending,
                         "recent_errors": [{"id": r.id, "entity_type": r.entity_type, "error": r.error} for r in errors]}}


class ConnectorIn(BaseModel):
    name: str | None = None
    provider: str
    config: dict = {}
    enabled: bool = True


@app.post("/api/connectors")
def create_connector(body: ConnectorIn, db: Session = Depends(get_db)):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "unknown provider")
    name, kind, group = PROVIDERS[body.provider]
    c = Connector(name=body.name or name, provider=body.provider, kind=kind, group=group, config=body.config,
                  enabled=body.enabled, status="ready" if body.config else "not_configured")
    db.add(c)
    db.commit()
    return connector_dict(c)


class ConnectorPatch(BaseModel):
    config: dict | None = None
    enabled: bool | None = None
    name: str | None = None


@app.patch("/api/connectors/{cid}")
def update_connector(cid: int, body: ConnectorPatch, db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    if body.config is not None:
        merged = dict(c.config or {})
        for k, v in body.config.items():
            if v != "••••":
                merged[k] = v
        c.config = merged
        if c.status == "not_configured":
            c.status = "ready"
    if body.enabled is not None:
        c.enabled = body.enabled
    if body.name:
        c.name = body.name
    db.commit()
    return connector_dict(c)


@app.post("/api/connectors/{cid}/sync")
def sync(cid: int, normalize: bool = True, db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    n = sync_connector(db, c)
    stats = normalize_pending(db) if normalize and n else None
    db.commit()
    return {"connector": connector_dict(c), "fetched": n, "normalized": stats}


@app.post("/api/connectors/{cid}/webhook")
def webhook(cid: int, payload: dict, db: Session = Depends(get_db)):
    """Body: {"entity_type": "order", "records": [...]} — CRM / Pancake / carrier / comment push."""
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    et = payload.get("entity_type")
    recs = payload.get("records") or []
    if et not in ENTITY_TYPES or not isinstance(recs, list):
        raise HTTPException(400, f"entity_type ∈ {ENTITY_TYPES}, records: list")
    n = store_raw(db, et, recs, c.id)
    c.last_sync_at, c.last_sync_count, c.status = datetime.utcnow(), n, "ok"
    stats = normalize_pending(db)
    db.commit()
    return {"received": n, "normalized": stats}


@app.get("/api/import/templates")
def templates():
    return CSV_TEMPLATES


@app.get("/api/import/templates/{entity_type}.csv", response_class=PlainTextResponse)
def template_csv(entity_type: str):
    if entity_type not in CSV_TEMPLATES:
        raise HTTPException(404)
    return CSV_TEMPLATES[entity_type] + "\n"


@app.post("/api/import/csv")
async def import_csv(entity_type: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(400, f"entity_type must be one of {ENTITY_TYPES}")
    rows = parse_csv(await file.read())
    csv_conn = db.scalar(select(Connector).where(Connector.provider == "csv"))
    n = store_raw(db, entity_type, rows, csv_conn.id if csv_conn else None)
    stats = normalize_pending(db)
    score_all(db)
    db.commit()
    return {"rows": n, "normalized": stats}


class ManualIn(BaseModel):
    url: str
    product_name: str
    country: str | None = None
    price: float | None = None
    advertiser: str | None = None
    ad_text: str | None = None


@app.post("/api/import/manual")
def import_manual(body: ManualIn, db: Session = Depends(get_db)):
    conn = db.scalar(select(Connector).where(Connector.provider == "manual"))
    store_raw(db, "ad", [manual_url_record(body.url, body.product_name, body.country, body.price, body.advertiser, body.ad_text)],
              conn.id if conn else None)
    stats = normalize_pending(db)
    score_all(db)
    db.commit()
    ad = db.scalar(select(Ad).order_by(Ad.id.desc()))
    return {"normalized": stats, "product_id": ad.product_id if ad else None}


# ============================================================ Pipeline
@app.post("/api/pipeline/run")
def pipeline(pull: bool = True, db: Session = Depends(get_db)):
    run = run_pipeline(db, pull_connectors=pull)
    return {"id": run.id, "status": run.status, "steps": run.steps, "started_at": run.started_at, "finished_at": run.finished_at}


@app.get("/api/pipeline/runs")
def runs(db: Session = Depends(get_db)):
    return [{"id": r.id, "status": r.status, "steps": r.steps, "started_at": r.started_at, "finished_at": r.finished_at}
            for r in db.scalars(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(20)).all()]


@app.post("/api/admin/demo-data")
def demo_data():
    """Simulated data for UI testing only — refused unless ALLOW_DEMO_DATA=1. Demo products are flagged is_demo."""
    if os.getenv("ALLOW_DEMO_DATA") != "1":
        raise HTTPException(403, "Demo data disabled (ALLOW_DEMO_DATA=1 to enable)")
    from .seed import seed

    seed(reset=True)
    return {"ok": True}


# ============================================================ AI Agent
class AskIn(BaseModel):
    question: str


@app.post("/api/agent/ask")
def ask(body: AskIn, db: Session = Depends(get_db)):
    return agent.ask(db, body.question)


@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.available()}
