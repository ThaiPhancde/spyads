"""Market Intelligence OS — API."""
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .db import Base, ReadOnlyGets, engine, get_db
from .markets import ad_scope_filter, expand, product_in_scope
from .models import (ad_in_country, Ad, Advertiser, AdvertiserDailySnapshot, Alert, Comment, Connector, Experiment,
                     LifecycleEvent, Order, Product, ProductDailySnapshot, Store)
from .services import agent, llm
from .services.connectors import (CSV_TEMPLATES, ENTITY_TYPES, ensure_default_connectors, manual_url_record, normalize_pending,
                                  parse_csv, store_raw)
from .services.decision import BENCH, FAILURE_TYPES, LIFECYCLE_STATES, TH, TRANSITIONS, experiment_metrics, learning_profile, transition
from .services.engine import is_hidden_winner, is_potential_winner, score_all
from .services.enrichment import ASPECTS, analyze_comments
from .services.entity_resolution import merge_products
from .services.pipeline import run_pipeline
from .services.scoring import confidence_label, win_label

app = FastAPI(title="Market Intelligence OS", version="0.1.0")


class AdminGuard:
    """ASGI middleware: every write (non GET/HEAD/OPTIONS) needs `X-Admin-Token` = ADMIN_TOKEN (.env). Fails closed when
    unset. /api/ingest/* and /api/webhooks/* use X-Ingest-Token instead; /api/health stays open for probes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            path = scope["path"]
            if not path.startswith(("/api/ingest/", "/api/webhooks/")) and path != "/api/health":
                want = os.getenv("ADMIN_TOKEN")
                got = next((v.decode() for k, v in scope["headers"] if k == b"x-admin-token"), None)
                if not want:
                    return await JSONResponse({"detail": "ADMIN_TOKEN not configured"}, status_code=503)(scope, receive, send)
                if got != want:
                    return await JSONResponse({"detail": "invalid admin token (header X-Admin-Token)"}, status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)


app.add_middleware(ReadOnlyGets)
app.add_middleware(AdminGuard)
# CORS last = outermost, so 401/503 from the guard still carry CORS headers for the browser
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.getenv("WEB_ORIGIN", "http://localhost:3000").split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])


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
    for noisy in ("httpx", "httpcore"):  # one INFO line per request, with the full (signed) CDN URL — drowns real logs
        logging.getLogger(noisy).setLevel(logging.WARNING)
    Base.metadata.create_all(engine)
    auto_migrate()
    with SessionLocal() as db:
        ensure_default_connectors(db)
        from sqlalchemy import update

        from .services import adsignals
        from .services.engine import rescore_products

        # repair: worldwide ("ALL") searches used to store country "AL" (Albania). No-op once clean.
        bad = db.scalars(select(Ad).where(Ad.country == "AL", ad_in_country("ALL"))).all()
        for a in bad:
            a.country, a.countries = None, [c for c in a.countries or [] if c != "AL"]
        for m in (Product, Advertiser, Store):
            db.execute(update(m).where(m.country == "AL").values(country=None))
        if bad:
            rescore_products(db, {a.product_id for a in bad if a.product_id})

        from . import platforms

        platforms.backfill(db)  # rows from before network/channel existed; the scheduler's first full re-score picks them up
        adsignals.refresh(db)  # per-ad force / variation / target-market flags (cheap; keeps them in sync with .env)
        db.commit()
    from .media import requeue_lost

    logging.getLogger(__name__).info("media: %s lost creatives re-queued", requeue_lost())
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
        **platform_signals(f),
    }


# ============================================================ meta
from .platforms import catalogue as platforms_catalogue
from .models import ADS_ONLY
from .routers.intel import platform_signals


@app.get("/api/meta")
def meta(db: Session = Depends(get_db)):
    countries = sorted({c for p in db.scalars(select(Product)).all() for c in (p.features or {}).get("markets", [])})
    cats = sorted({c for c in db.scalars(select(Product.category).distinct()).all() if c})
    from .markets import REGION_LABEL, REGIONS, targets as _targets

    countries = _targets()
    return {"countries": countries, "regions": {r: {"label": REGION_LABEL[r], "countries": REGIONS[r]} for r in REGIONS}, "categories": cats, "lifecycle_states": LIFECYCLE_STATES, "transitions": {k: sorted(v) for k, v in TRANSITIONS.items()},
            "failure_types": FAILURE_TYPES, "aspects": ASPECTS, "recommendations": ["TEST_NOW", "TEST", "WATCH", "REVIEW", "SKIP", "SCALE", "HOLD", "ITERATE", "STOP"],
            "thresholds": TH, "benchmarks": BENCH, "llm": llm.available(), "llm_model": llm.model() if llm.available() else None,
            "entity_types": ENTITY_TYPES, "platforms": platforms_catalogue()}


# ============================================================ §22 Daily Pulse
@app.get("/api/dashboard/pulse")
def pulse(country: str | None = None, db: Session = Depends(get_db)):
    from .routers.intel import cached

    return cached(("pulse", country), lambda: _pulse(country, db))


def _pulse(country: str | None, db: Session) -> dict:
    products = [p for p in db.scalars(select(Product)).all() if p.category != "non_product" and product_in_scope(p, country)]
    since = datetime.utcnow() - timedelta(days=1)
    ad_q = select(Ad).where(Ad.is_internal.is_(False), ADS_ONLY, Ad.first_seen_at > since)
    _f = ad_scope_filter(country)
    if _f is not None:
        ad_q = ad_q.where(_f)
    new_ads = db.scalars(ad_q).all()
    adv_first = db.execute(select(Ad.advertiser_id, func.min(Ad.first_seen_at)).where(Ad.is_internal.is_(False), ADS_ONLY)
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

    from .services import hidden as _hidden

    hidden = [r["product"] for r in _hidden.compute(db, country)]
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


# ============================================================ Product Explorer
@app.get("/api/products")
def list_products(q: str | None = None, country: str | None = None, category: str | None = None,
                  recommendation: str | None = None, lifecycle: str | None = None, min_win: float | None = None,
                  max_advertisers: int | None = None, min_confidence: float | None = None, saturation_state: str | None = None,
                  hidden_only: bool = False, sort: str = "opportunity_score", order: str = "desc",
                  limit: int = Query(50, le=500), offset: int = 0, db: Session = Depends(get_db)):
    from .models import ProductAlias

    pq = select(Product)
    if q:
        pq = pq.where(or_(Product.canonical_name.ilike(f"%{q}%"), Product.product_code == q.upper(),
                          Product.id.in_(select(ProductAlias.product_id).where(ProductAlias.name.ilike(f"%{q}%")))))
    if not country:  # ponytail: in_target NULL (never scored) counts as out of scope
        pq = pq.where(Product.in_target.is_(True))
    elif (codes := expand(country)) is not None:  # superset pre-filter on the JSON text; product_in_scope() below is exact
        pq = pq.where(or_(Product.country.in_(codes), *[cast(Product.features, String).like(f'%"{c}"%') for c in codes]))
    pq = pq.where(Product.category == category) if category else pq.where(or_(Product.category.is_(None), Product.category != "non_product"))
    for col, val in ((Product.recommendation, recommendation), (Product.lifecycle_status, lifecycle), (Product.saturation_state, saturation_state)):
        if val:
            pq = pq.where(col == val)
    if min_win is not None:
        pq = pq.where(Product.win_score >= min_win)
    if min_confidence is not None:
        pq = pq.where(Product.confidence_score >= min_confidence)
    col = Product.__table__.c.get({"name": "canonical_name"}.get(sort, sort))
    if col is not None and not country and max_advertisers is None and not hidden_only:  # fast path: everything in SQL
        total = db.scalar(select(func.count()).select_from(pq.subquery()))
        page = db.scalars(pq.order_by((col.desc() if order == "desc" else col.asc()).nullslast(), Product.id).offset(offset).limit(limit)).all()
        return {"total": total, "rows": [product_card(p) for p in page]}
    rows = [product_card(p) for p in db.scalars(pq)
            if product_in_scope(p, country) and (max_advertisers is None or (p.features or {}).get("advertiser_count", 0) <= max_advertisers)
            and (not hidden_only or is_hidden_winner(p))]
    with_val = sorted((r for r in rows if r.get(sort) is not None), key=lambda r: r[sort], reverse=(order == "desc"))
    rows = with_val + [r for r in rows if r.get(sort) is None]
    return {"total": len(rows), "rows": rows[offset: offset + limit]}


@app.get("/api/products/hidden-winners")
def hidden_winners(tag: str | None = None, scope: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
                   db: Session = Depends(get_db)):
    """Few sellers + real winning signals (long-running / duplicated / multiplying ads), computed live from the ads."""
    from .routers.intel import covers, landing_links
    from .routers.intel import cached
    from .routers.intel import product_card as media_card
    from .services import hidden

    allrows = cached(("hidden", scope), lambda: hidden.compute(db, scope))
    tags = Counter(t for r in allrows for t in r["tags"])  # counts before the tag filter → every chip shows its number
    tagged = [r for r in allrows if not tag or tag in r["tags"]]
    rows = tagged[offset: offset + limit]
    prods = [r["product"] for r in rows]
    cov, lp = covers(db, prods), landing_links(db, prods)
    out = [media_card(r["product"], cov.get(r["product"].id)) | {k: v for k, v in r.items() if k != "product"}
           | {"landing_url": lp.get(r["product"].id)} for r in rows]
    return {"rows": out, "total": len(allrows), "has_more": offset + len(rows) < len(tagged), "tags": dict(tags), "tag_labels": hidden.TAGS}


# ============================================================ Product 360
def mkt_summary(ads: list[Ad], advs: dict) -> dict:
    """What a marketer checks first: floor price per marketplace, supplier price → margin, engagement on the ads,
    marketplace proof (reviews / sold / rank), how long the ads have run and where they send traffic."""
    from urllib.parse import urlparse

    from .services.enrichment import to_usd

    listings = [a for a in ads if a.channel == "commerce"]
    spy = [a for a in ads if a.channel != "commerce" and not a.is_internal]
    usd = lambda a: to_usd(a.price, a.currency) if a.price else None
    floor: dict[str, dict] = {}
    for a in listings:
        u = usd(a)
        if u and (a.network not in floor or u < floor[a.network]["usd"]):
            floor[a.network] = {"network": a.network, "price": a.price, "currency": a.currency, "usd": round(u, 2),
                                "title": a.title or a.raw_product_name, "url": a.landing_url, "rating": a.rating,
                                "reviews": a.review_count, "sold": a.sold_count}
    retail = sorted(u for a in listings if a.network != "aliexpress" and (u := usd(a)))
    ad_prices = sorted(u for a in spy if (u := usd(a)))
    supplier = floor.get("aliexpress", {}).get("usd")
    sell = (ad_prices or retail or [None])[len(ad_prices or retail or [None]) // 2]
    eng = lambda a: (a.likes or 0) + 3 * (a.comments_count or 0) + 5 * (a.shares or 0)
    top = sorted((a for a in spy if eng(a) or a.views), key=lambda a: (eng(a), a.views or 0), reverse=True)[:5]
    now = datetime.utcnow()
    days = [((a.last_seen_at if not a.is_active else now) - a.first_seen_at).days for a in spy if a.first_seen_at]
    domains = Counter(urlparse(a.landing_url).netloc.removeprefix("www.") for a in spy if a.landing_url)
    return {
        "floor": sorted(floor.values(), key=lambda r: r["usd"]),
        "floor_usd": min((r["usd"] for r in floor.values() if r["network"] != "aliexpress"), default=None),
        "supplier_usd": supplier,
        "sell_usd": round(sell, 2) if sell else None,  # median price competitors advertise (else retail median)
        "ad_price_range_usd": [round(ad_prices[0], 2), round(ad_prices[-1], 2)] if ad_prices else None,
        "margin_est": round(1 - supplier * 1.6 / sell, 3) if supplier and sell else None,  # landed ≈ supplier × 1.6
        "engagement": {"likes": sum(a.likes or 0 for a in spy), "comments": sum(a.comments_count or 0 for a in spy),
                       "shares": sum(a.shares or 0 for a in spy), "views": sum(a.views or 0 for a in ads),
                       "page_likes": max((a.page_likes or 0 for a in spy), default=0) or None},
        "marketplace": {"listings": len(listings), "reviews": sum(a.review_count or 0 for a in listings),
                        "sold": sum(a.sold_count or 0 for a in listings),
                        "rating": round(sum(a.rating for a in listings if a.rating) / max(1, sum(1 for a in listings if a.rating)), 2)
                        if any(a.rating for a in listings) else None},
        "ads": {"total": len(spy), "active": sum(1 for a in spy if a.is_active),
                "advertisers": len({a.advertiser_id for a in spy} - {None}),
                "longest_days": max(days, default=None), "median_days": sorted(days)[len(days) // 2] if days else None,
                "networks": dict(Counter(a.network or "meta" for a in spy)), "funnels": dict(Counter(a.funnel or "other" for a in spy)),
                "reach": sum(a.reach or 0 for a in spy) or None},
        "landing_domains": [{"domain": d, "ads": n} for d, n in domains.most_common(8)],
        "top_ads": [{"id": a.id, "advertiser": advs[a.advertiser_id].name if a.advertiser_id in advs else None,
                     "text": (a.ad_text or a.title or "")[:200], "likes": a.likes, "comments": a.comments_count, "shares": a.shares,
                     "views": a.views, "network": a.network, "url": a.snapshot_url or a.landing_url, "active": a.is_active,
                     "days": ((now if a.is_active else a.last_seen_at) - a.first_seen_at).days if a.first_seen_at else None}
                    for a in top],
    }


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
        "mkt": mkt_summary(ads, advs),
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
                        "active": a.is_active, "source": a.source, "landing_url": a.landing_url}
                       for a in [x for x in ads if x.channel != "commerce"][:30]],  # listings: own card (/ads?channel=commerce)
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
    return {"explanation": agent.explain_product(p), "engine": llm.engine() if llm.available() else "template"}


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
def competitors(country: str | None = None, watched_only: bool = False, offset: int = 0, limit: int = Query(50, le=500),
                db: Session = Depends(get_db)):
    from .routers.intel import cached

    rows = cached(("competitors", country, watched_only), lambda: _competitor_rows(country, watched_only, db))
    return {"rows": rows[offset: offset + limit], "total": len(rows), "has_more": offset + limit < len(rows),
            "scaling": sum(1 for r in rows if r["scaling"])}


def _competitor_rows(country: str | None, watched_only: bool, db: Session) -> list[dict]:
    now = datetime.utcnow()
    ads = db.scalars(select(Ad).where(Ad.is_internal.is_(False), ADS_ONLY, Ad.advertiser_id.is_not(None))).all()
    by = defaultdict(list)
    for a in ads:
        by[a.advertiser_id].append(a)
    advs = {a.id: a for a in db.scalars(select(Advertiser)).all()}
    products = {p.id: p for p in db.scalars(select(Product)).all()}
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
    return rows


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
def alerts(type: str | None = None, unread: bool = False, limit: int = Query(200, le=500), db: Session = Depends(get_db)):
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


# ============================================================ Data import (§3): CSV templates / upload, manual URL
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


# ============================================================ AI Agent
class AskIn(BaseModel):
    question: str


@app.post("/api/agent/ask")
def ask(body: AskIn, db: Session = Depends(get_db)):
    return agent.ask(db, body.question)


@app.get("/api/health")
def health():
    import time

    cooling = time.time() < getattr(llm, "_cool_until", 0)
    return {"ok": True, "llm": llm.available(),
            "llm_reason": "disabled" if llm._disabled else f"rate-limited, retry in {int(llm._cool_until - time.time())}s" if cooling else None}
