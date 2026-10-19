"""Market Intelligence OS — API."""
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .db import ReadOnlyGets, get_db
from .markets import ad_scope_filter, expand, product_in_scope
from .models import (ad_in_country, Ad, Advertiser, AdvertiserDailySnapshot, Comment, Connector, Event, LifecycleEvent,
                     Product, ProductDailySnapshot, Store)
from .services import agent, llm, naming
from .services.connectors import (CSV_TEMPLATES, ENTITY_TYPES, ensure_default_connectors, manual_url_record, normalize_pending,
                                  parse_csv, store_raw)
from .services.decision import LIFECYCLE_STATES, TRANSITIONS, transition
from .services.engine import is_hidden_winner, is_test_candidate, rescore_products, score_all
from .services.enrichment import ASPECTS
from .services import clustering
from .services.entity_resolution import MATCH_THRESHOLD, find_best_match, merge_products, split_ads
from .services.pipeline import run_pipeline
from .services.scoring import confidence_label

app = FastAPI(title="Market Intelligence OS", version="0.1.0")


app.add_middleware(ReadOnlyGets)
from .workspace.auth import SessionGuard
from .workspace.accounts import router as accounts_router
from .workspace.routes import router as workspace_router

app.add_middleware(SessionGuard)
app.include_router(accounts_router)
app.include_router(workspace_router)
# CORS last = outermost, so 401/503 from the guard still carry CORS headers for the browser
app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://[a-p]{32}",  # Browser Bridge
                   allow_origins=[o.strip() for o in os.getenv("WEB_ORIGIN", "http://localhost:3000").split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=True)


from .routers.intel import router as intel_router  # noqa: E402

app.include_router(intel_router)

from .visual.routes import router as visual_router  # noqa: E402 — global image search (docs/IMAGE_SEARCH.md)

app.include_router(visual_router)
from .routers.clusters import router as clusters_router

app.include_router(clusters_router)
from .routers.allocations import router as allocations_router  # noqa: E402 — Phase 2 exclusive product allocation

app.include_router(allocations_router)
from .routers.behavior import router as behavior_router  # noqa: E402 — Phase 3 MKT behavior tracking

app.include_router(behavior_router)
from .routers.preferences import router as preferences_router  # noqa: E402 — Phase 4 MKT preference profile

app.include_router(preferences_router)
from .routers.recommendations import router as recommendations_router  # noqa: E402 — Phase 5 personalized recommendations

app.include_router(recommendations_router)
from .routers.similar import router as similar_router  # noqa: E402 — Phase 6 similar ads & products

app.include_router(similar_router)
from .routers.library import router as library_router  # noqa: E402 — Phase 8 personalized Library

app.include_router(library_router)
from .engagement.routes import router as engagement_router  # noqa: E402 — likes / comments / shares / views per ad, N/A when unknown

app.include_router(engagement_router)

from .pricing.routes import router as pricing_router  # noqa: E402 — background price enrichment (docs/PRICE_INTELLIGENCE.md)

app.include_router(pricing_router)

from .routers.bridge import router as bridge_router  # noqa: E402 — Browser Bridge (extensions/allegro-bridge)

app.include_router(bridge_router)

from .eu_market.jobs import enabled as eu_market_enabled  # noqa: E402

if eu_market_enabled():  # EU_MARKET_ENABLED=0 → no /api/eu-market routes (the web page shows "disabled")
    from .eu_market.routes import agent_router, router as eu_market_router

    app.include_router(eu_market_router)
    app.include_router(agent_router)  # /api/agent/v1 — Grok Bot tools (docs/grokbot_integration.md)


@app.on_event("startup")
async def _startup():
    import threading

    def _warm_hash_index():  # ~1-2 s for 72k rows; otherwise the first image search of the day pays it
        try:
            from .db import SessionLocal
            from .visual.service import HASH_INDEX

            with SessionLocal() as db:
                HASH_INDEX.refresh(db, force=True)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_warm_hash_index, name="hash-index-warmup", daemon=True).start()
    import asyncio
    import logging

    from .db import SessionLocal, auto_migrate
    from .media import enqueue_pending
    from .realtime import scheduler_loop

    logging.basicConfig(level=logging.INFO)
    for noisy in ("httpx", "httpcore"):  # one INFO line per request, with the full (signed) CDN URL — drowns real logs
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from .workspace.migrate import migrate
    migrate()
    from .cluster_migrate import migrate as migrate_clusters
    migrate_clusters()
    from .similar import migrate as migrate_similar
    migrate_similar()
    from .personalization_migrate import migrate as migrate_personalization
    migrate_personalization()
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
    if os.getenv("DISABLE_SCHEDULER", "").lower() not in ("1", "true"):  # else `python -m app.worker` runs all of this
        from .media import requeue_lost

        logging.getLogger(__name__).info("media: %s lost creatives re-queued", requeue_lost())
        enqueue_pending()
        app.state.scheduler = asyncio.create_task(scheduler_loop())
        from .workspace.worker import tracker_loop
        app.state.competitor_scheduler = asyncio.create_task(tracker_loop())


@app.on_event("shutdown")
async def _shutdown_workspace():
    import asyncio
    tasks = [getattr(app.state, key, None) for key in ("scheduler", "competitor_scheduler")]
    for task in tasks:
        if task:
            task.cancel()
    await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)


# ============================================================ serializers
def product_card(p: Product) -> dict:
    f = p.features or {}
    return {
        "id": p.id, "product_code": p.product_code, **naming.card_fields(p), "category": p.category,
        "country": p.country, "markets": f.get("markets", []),
        "price": p.price if p.price and p.currency else f.get("avg_price"),
        "currency": p.currency if p.price and p.currency else "USD" if f.get("avg_price") else None,
        "lifecycle": p.lifecycle_status, "recommendation": p.recommendation, "reasons": p.recommendation_reasons,
        "rarity_score": p.rarity_score, "saturation_score": p.saturation_score,
        "saturation_state": p.saturation_state, "opportunity_score": p.opportunity_score, "confidence_score": p.confidence_score,
        "confidence_label": confidence_label(p.confidence_score),
        "sentiment_score": p.sentiment_score,
        "advertisers": f.get("advertiser_count", 0), "active_ads": f.get("active_ads", 0), "unique_ads": f.get("unique_ads", f.get("active_ads", 0)), "new_ads_7d": f.get("new_ads_7d", 0),
        "growth_7d": f.get("creative_growth_7d", 0), "stores": f.get("store_count", 0), "traffic_growth": f.get("traffic_growth"),
        "tags": p.tags or [],
        "first_seen_at": p.first_seen_at, "last_seen_at": p.last_seen_at, "hidden_winner": is_hidden_winner(p),
        **platform_signals(f),
    }


# ============================================================ meta
from .platforms import catalogue as platforms_catalogue
from .models import ADS_ONLY
from .routers.intel import platform_signals


def _known_sum(values) -> int | None:
    """Sum of the numbers a source really reported; None when none did (unknown is not 0)."""
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


@app.get("/api/naming/stats")
def naming_stats(db: Session = Depends(get_db)):
    """Naming dashboard: named / unnamed / review / errors, confidence, provider tokens + live quota (naming_ai.py, llm_providers.py)."""
    from . import naming_ai

    return naming_ai.stats(db)


@app.get("/api/meta")
def meta(db: Session = Depends(get_db)):
    cats = sorted({c for c in db.scalars(select(Product.category).distinct()).all() if c})
    from .markets import REGION_LABEL, REGIONS, targets as _targets

    countries = _targets()
    return {"countries": countries, "regions": {r: {"label": REGION_LABEL[r], "countries": REGIONS[r]} for r in REGIONS}, "categories": cats, "lifecycle_states": LIFECYCLE_STATES, "transitions": {k: sorted(v) for k, v in TRANSITIONS.items()},
            "aspects": ASPECTS, "recommendations": ["TEST_NOW", "TEST", "WATCH", "REVIEW", "SKIP"], "llm": llm.available(), "llm_model": llm.model() if llm.available() else None,
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

    from .services import hidden as _hidden

    hidden = [r["product"] for r in _hidden.compute(db, country)]
    trend = db.execute(select(ProductDailySnapshot.date, func.sum(ProductDailySnapshot.new_ads), func.sum(ProductDailySnapshot.active_ads))
                       .group_by(ProductDailySnapshot.date).order_by(ProductDailySnapshot.date)).all()
    recs = Counter(p.recommendation for p in products)
    return {
        "country": country,
        "kpis": {
            "products_monitored": len(products), "new_products": len(new_products), "new_active_ads": len(new_ads),
            "new_advertisers": len(new_adv_ids), "test_candidates": sum(1 for p in products if is_test_candidate(p)),
            "hidden_winners": len(hidden), "saturated_products": sum(1 for p in products if p.saturation_state in ("Highly Saturated", "Declining")),
        },
        "recommendations": dict(recs),
        "top_hidden_winners": [product_card(p) for p in hidden[:5]],
        "top_opportunities": [product_card(p) for p in sorted(products, key=lambda p: -p.opportunity_score)[:8]],
        "ads_trend": [{"date": d.isoformat(), "new_ads": n or 0, "active_ads": a or 0} for d, n, a in trend],
    }


# ============================================================ Product Explorer
V1_SORTS = {"win_score", "external_win_score", "rare_winner_score"}  # generation-1 scores: stored, never ranked on
@app.get("/api/products")
def list_products(q: str | None = None, country: str | None = None, category: str | None = None,
                  recommendation: str | None = None, lifecycle: str | None = None,
                  max_advertisers: int | None = None, min_confidence: float | None = None, saturation_state: str | None = None,
                  hidden_only: bool = False, sort: str = "opportunity_score", order: str = "desc", name_status: str | None = None,
                  limit: int = Query(50, le=500), offset: int = 0, db: Session = Depends(get_db)):
    from .models import ProductAlias

    pq = select(Product)
    if q:
        pq = pq.where(or_(Product.canonical_name.ilike(f"%{q}%"), Product.name_vi.ilike(f"%{q}%"), Product.product_code == q.upper(),
                          Product.id.in_(select(ProductAlias.product_id).where(ProductAlias.name.ilike(f"%{q}%")))))
    if not country:  # ponytail: in_target NULL (never scored) counts as out of scope
        pq = pq.where(Product.in_target.is_(True))
    elif (codes := expand(country)) is not None:  # superset pre-filter on the JSON text; product_in_scope() below is exact
        pq = pq.where(or_(Product.country.in_(codes), *[cast(Product.features, String).like(f'%"{c}"%') for c in codes]))
    pq = pq.where(Product.category == category) if category else pq.where(or_(Product.category.is_(None), Product.category != "non_product"))
    for col, val in ((Product.recommendation, recommendation), (Product.lifecycle_status, lifecycle), (Product.saturation_state, saturation_state)):
        if val:
            pq = pq.where(col == val)
    if name_status:  # "UNKNOWN" = the queue of unidentified products MKT names
        pq = pq.where(Product.name_status.in_(name_status.upper().split(",")))
    if min_confidence is not None:
        pq = pq.where(Product.confidence_score >= min_confidence)
    sort = "opportunity_score" if sort in V1_SORTS else sort  # old bookmarks (?sort=win_score) land on the v2 number
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
def hidden_winners(tag: str | None = None, scope: str | None = None, status: str = "CONFIRMED", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    """Few sellers + real winning signals (long-running / duplicated / multiplying ads), computed live from the ads."""
    from .routers.intel import covers, landing_links
    from .routers.intel import cached
    from .routers.intel import product_card as media_card
    from .services import hidden

    if status not in hidden.STATUS_LABELS:
        raise HTTPException(422, "status = CONFIRMED | WATCH | REJECTED")
    assessed = cached(("hidden-v3", scope), lambda: hidden.compute(db, scope, include_unconfirmed=True))
    status_counts = Counter(r["status"] for r in assessed)
    allrows = [r for r in assessed if r["status"] == status]
    tags = Counter(t for r in allrows for t in r["tags"])  # counts before the tag filter → every chip shows its number
    tagged = [r for r in allrows if not tag or tag in r["tags"]]
    rows = tagged[offset: offset + limit]
    prods = [r["product"] for r in rows]
    cov, lp = covers(db, prods), landing_links(db, prods)
    out = [media_card(r["product"], cov.get(r["product"].id)) | {k: v for k, v in r.items() if k != "product"}
           | {"landing_url": lp.get(r["product"].id)} for r in rows]
    return {"rows": out, "total": len(tagged), "all_total": len(allrows),
            "has_more": offset + len(rows) < len(tagged), "tags": dict(tags), "tag_labels": hidden.TAGS,
            "status_counts": dict(status_counts), "status_labels": hidden.STATUS_LABELS}


# ============================================================ Product 360
from .services.spy import SKIP_HOST as _SKIP_HOST


def mkt_summary(ads: list[Ad], advs: dict, observations=()) -> dict:
    """What a marketer checks first: floor price per marketplace, supplier price → margin, engagement on the ads,
    marketplace proof (reviews / sold / rank), how long the ads have run and where they send traffic."""
    from urllib.parse import urlparse

    from .services.enrichment import to_usd, FX_TO_USD
    from .pricing.models import VERIFIED_COMPETITOR, VERIFIED_MARKETPLACE

    listings = [a for a in ads if a.channel == "commerce"]
    spy = [a for a in ads if a.channel != "commerce" and not a.is_internal]
    # an ad price without a currency ("199k" in a non-Vietnamese ad) is not a price we can show in USD
    usd = lambda a: to_usd(a.price, a.currency) if a.price and (a.currency or a.channel == "commerce") else None
    floor: dict[str, dict] = {}
    for a in listings:
        u = usd(a)
        if u and (a.network not in floor or u < floor[a.network]["usd"]):
            floor[a.network] = {"network": a.network, "price": a.price, "currency": a.currency, "usd": round(u, 2),
                                "title": a.title or a.raw_product_name, "url": a.landing_url, "rating": a.rating,
                                "reviews": a.review_count, "sold": a.sold_count}
    retail = sorted(u for a in listings if a.network != "aliexpress" and (u := usd(a)))
    priced = [a for a in spy if usd(a)]
    ad_prices = sorted(usd(a) for a in priced)
    supplier = floor.get("aliexpress", {}).get("usd")
    # competitor price = what the ADS sell at (copy or landing page) — never a marketplace median dressed up as one
    sell = ad_prices[len(ad_prices) // 2] if ad_prices else None
    # Verified router observations feed detail-page margins; snippets/look-alikes never become costs.
    verified_prices = {}
    for observation in observations:
        if (not observation.price_amount or observation.currency not in FX_TO_USD
            or observation.price_confidence < .7 or observation.match_confidence < .8
            or not observation.expires_at or observation.expires_at <= datetime.utcnow()):
            continue
        key = (observation.price_type, observation.source_url, observation.seller)
        if key not in verified_prices or observation.observed_at > verified_prices[key].observed_at:
            verified_prices[key] = observation
    cached_sell = sorted(to_usd(o.price_amount, o.currency) for o in verified_prices.values() if o.price_type == VERIFIED_COMPETITOR)
    cached_supplier = [to_usd(o.price_amount, o.currency) for o in verified_prices.values()
                       if o.price_type == VERIFIED_MARKETPLACE and (o.evidence or {}).get("role") == "supplier"]
    if sell is None and cached_sell:
        sell = cached_sell[len(cached_sell) // 2]
    if cached_supplier:
        supplier = min(([supplier] if supplier else []) + cached_supplier)
    retail_med = retail[len(retail) // 2] if retail else None
    by_adv: dict = defaultdict(list)
    for a in priced:
        by_adv[a.advertiser_id].append(a)
    adv_prices = sorted(({"advertiser": advs[k].name if k in advs else None, "ads": len(v),
                          "price": sorted(x.price for x in v)[len(v) // 2], "currency": v[0].currency,
                          "usd": round(sorted(usd(x) for x in v)[len(v) // 2], 2), "source": Counter(x.price_source or "source" for x in v).most_common(1)[0][0],
                          "domain": next((urlparse(x.landing_url).netloc.removeprefix("www.") for x in v if x.landing_url), None),
                          "url": next((x.landing_url for x in v if x.landing_url), None), "active": sum(1 for x in v if x.is_active)}
                         for k, v in by_adv.items()), key=lambda r: (-r["active"], -r["ads"]))
    fetchable = [a for a in spy if a.landing_url and not _SKIP_HOST.search(urlparse(a.landing_url).netloc)]
    eng = lambda a: (a.likes or 0) + 3 * (a.comments_count or 0) + 5 * (a.shares or 0)
    top = sorted((a for a in spy if eng(a) or a.views), key=lambda a: (eng(a), a.views or 0), reverse=True)[:5]
    now = datetime.utcnow()
    days = [((a.last_seen_at if not a.is_active else now) - a.first_seen_at).days for a in spy if a.first_seen_at]
    domains = Counter(urlparse(a.landing_url).netloc.removeprefix("www.") for a in spy if a.landing_url)
    return {
        "floor": sorted(floor.values(), key=lambda r: r["usd"]),
        "floor_usd": min((r["usd"] for r in floor.values() if r["network"] != "aliexpress"), default=None),
        "supplier_usd": supplier,
        "verified_cache_prices": len(cached_sell) + len(cached_supplier),
        "sell_usd": round(sell, 2) if sell else None,  # median of competitor selling prices (ads only)
        "retail_usd": round(retail_med, 2) if retail_med else None,  # Walmart / Amazon listings — shown separately
        "ad_price_range_usd": [round(ad_prices[0], 2), round(ad_prices[-1], 2)] if ad_prices else None,
        # transparency: how many ads the price rests on, where it came from, and what is still fetchable
        "price_coverage": {"priced": len(priced), "ads": len(spy), "sources": dict(Counter(a.price_source or "source" for a in priced)),
                           "fetchable": len(fetchable), "unchecked": sum(1 for a in fetchable if a.price is None and not a.landing_checked_at),
                           "inbox": sum(1 for a in spy if not a.landing_url or _SKIP_HOST.search(urlparse(a.landing_url).netloc))},
        "sell_by_advertiser": adv_prices[:10],
        "margin_est": round(1 - supplier * 1.6 / sell, 3) if supplier and sell else None,  # landed ≈ supplier × 1.6
        # None = no ad of the product reported it (Meta Ad Library never does) → N/A on the card, never a fake 0
        "engagement": {"likes": _known_sum(a.likes for a in spy), "comments": _known_sum(a.comments_count for a in spy),
                       "shares": _known_sum(a.shares for a in spy), "views": _known_sum(a.views for a in ads),
                       "ads_reporting": sum(1 for a in spy if a.likes is not None or a.comments_count is not None or a.shares is not None),
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
        c["engagement"] += (a.likes or 0) + 3 * (a.comments_count or 0) + 5 * (a.shares or 0)
        c["platforms"].add(a.platform)
        if a.country:
            c["countries"].add(a.country)
    creative_lib = sorted(({**c, "platforms": sorted(c["platforms"]), "countries": sorted(c["countries"])} for c in creatives.values()),
                          key=lambda c: (-c["active"], -c["longest_days"]))[:40]

    snaps = db.scalars(select(ProductDailySnapshot).where(ProductDailySnapshot.product_id == pid).order_by(ProductDailySnapshot.date)).all()
    stores = db.scalars(select(Store).where(Store.product_id == pid).order_by(Store.estimated_traffic.desc())).all()
    events = db.scalars(select(LifecycleEvent).where(LifecycleEvent.product_id == pid).order_by(LifecycleEvent.created_at.desc())).all()
    by_country = Counter(a.country for a in ads if a.is_active and not a.is_internal)
    hooks = Counter(a.hook for a in ads if a.is_active)
    angles = Counter(a.angle for a in ads if a.is_active)
    offers = Counter(o for a in ads if a.is_active and a.offer for o in a.offer.split(","))
    price_points = sorted([{"date": a.first_seen_at.date().isoformat(), "price": a.price, "country": a.country} for a in ads if a.price],
                          key=lambda x: x["date"])
    from .pricing.models import PriceObservation
    price_observations = list(db.scalars(select(PriceObservation).where(
        or_(PriceObservation.cluster_key == f"product:{pid}", PriceObservation.subject == f"product:{pid}"),
        PriceObservation.expires_at > datetime.utcnow()).order_by(PriceObservation.observed_at.desc()).limit(100)))
    return {
        "product": {**product_card(p), "opportunity_raw": ((p.potential or {}).get("vector") or {}).get("opportunity_raw"), "brand": p.brand, "cost": p.cost, "language": p.language,
                    "search_trend": p.search_trend, "keyword_competition": p.keyword_competition,
                    "aliases": [{"name": a.name, "source": a.source, "match_score": a.match_score} for a in p.aliases],
                    "name_evidence": p.name_evidence or [], "name_history": list(reversed(p.name_history or [])),
                    "advertiser": advertisers[0]["name"] if advertisers else None},
        # every ad of the cluster, for "Tách cụm" (split) — ad type + the name its own evidence gives
        "cluster_ads": [{"id": a.id, "advertiser": advs[a.advertiser_id].name if a.advertiser_id in advs else None,
                         "text": (a.ad_text or a.title or "")[:160], "evidence_name": a.raw_product_name, "name_source": a.name_source,
                         "ad_type": a.ad_type, "network": a.network, "channel": a.channel, "landing_url": a.landing_url,
                         "first_seen": a.first_seen_at, "active": a.is_active} for a in ads[:300]],
        "features": {k: v for k, v in f.items() if not k.startswith("_")},
        "breakdown": {k: v for k, v in f.get("_breakdown", {}).items() if k != "external"},
        "derived": {k: v for k, v in f.get("_scores", {}).items() if k != "opportunity_raw"},
        "mkt": mkt_summary(ads, advs, price_observations),
        "market_performance": [{"country": c, "active_ads": n} for c, n in by_country.most_common()],
        "advertisers": advertisers[:50],
        "creatives": creative_lib,
        "creative_mix": {"hooks": dict(hooks.most_common()), "angles": dict(angles.most_common()), "offers": dict(offers.most_common())},
        "stores": [{"domain": s.domain, "country": s.country, "price": s.price, "traffic": s.estimated_traffic,
                    "traffic_growth": s.traffic_growth} for s in stores],
        "pricing": {"points": price_points[-200:], "avg": f.get("avg_price"), "trend": f.get("price_trend")},
        "comments": comment_summary(db, [pid]),
        "timeseries": [{"date": s.date.isoformat(), "active_ads": s.active_ads, "new_ads": s.new_ads, "advertisers": s.advertisers,
                        "saturation": s.saturation_score, "negative_comments": s.negative_comments, "comments": s.comments} for s in snaps],
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
    user: str | None = None


@app.post("/api/products/merge")
def merge(body: MergeIn, db: Session = Depends(get_db)):
    s, t = db.get(Product, body.source_id), db.get(Product, body.target_id)
    if not s or not t or s.id == t.id:
        raise HTTPException(400, "invalid products")
    merge_products(db, s, t, by=body.user)
    rescore_products(db, {t.id})  # was score_all: minutes of rescoring the whole catalogue for one merge
    db.commit()
    return {"ok": True, "target_id": t.id}


# ============================================================ naming (MKT): edit · confirm · undo · split · unmerge
def _product(db: Session, pid: int) -> Product:
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404, "Product not found")
    return p


def _duplicate_of(db: Session, p: Product) -> dict | None:
    """Another product already under this name → the UI offers to merge (never automatic)."""
    other, score = find_best_match(db, {"name": p.canonical_name, "category": p.category})
    if other is not None and other.id != p.id and score >= MATCH_THRESHOLD and other.name_status != "UNKNOWN":
        return {"id": other.id, "name": naming.display_name(other), "product_code": other.product_code, "score": round(score, 2)}
    return None


class NameIn(BaseModel):
    name: str | None = None  # canonical (matching) name — English / source language
    name_vi: str | None = None  # what MKT reads; "" clears it
    user: str | None = None


@app.patch("/api/products/{pid}/name")
def set_product_name(pid: int, body: NameIn, db: Session = Depends(get_db)):
    p = _product(db, pid)
    if not (body.name or "").strip() and body.name_vi is None:
        raise HTTPException(400, "name or name_vi required")
    if p.name_status == "UNKNOWN" and not (body.name or "").strip() and (body.name_vi or "").strip():
        body.name = body.name_vi  # an unidentified product named in Vietnamese only: that is its name too
    naming.set_name(p, body.name, body.name_vi, body.user)
    db.commit()
    return {"ok": True, "product": product_card(p), "duplicate": _duplicate_of(db, p)}


class ByIn(BaseModel):
    user: str | None = None


@app.post("/api/products/{pid}/name/confirm")
def confirm_product_name(pid: int, body: ByIn, db: Session = Depends(get_db)):
    p = _product(db, pid)
    if p.name_status == "UNKNOWN":
        raise HTTPException(400, "Chưa có tên để xác nhận — nhập tên sản phẩm")
    naming.confirm(p, body.user)
    db.commit()
    return {"ok": True, "product": product_card(p)}


@app.post("/api/products/{pid}/name/undo")
def undo_product_name(pid: int, db: Session = Depends(get_db)):
    p = _product(db, pid)
    if not naming.undo(p):
        raise HTTPException(400, "Không có thay đổi nào để hoàn tác")
    db.commit()
    return {"ok": True, "product": product_card(p)}


class SplitIn(BaseModel):
    ad_ids: list[int]
    name: str | None = None
    name_vi: str | None = None
    user: str | None = None


@app.post("/api/products/{pid}/split")
def split_product(pid: int, body: SplitIn, db: Session = Depends(get_db)):
    """Selected ads leave this product for a new one (named by MKT, or 'Chưa xác định sản phẩm')."""
    p = _product(db, pid)
    try:
        new = split_ads(db, p, body.ad_ids, (body.name or "").strip() or None, body.user, body.name_vi)
    except ValueError as e:
        raise HTTPException(400, str(e))
    clustering.record_cannot_link(db, p.id, new.id, body.user)  # MKT separated them: never auto-merge them back
    rescore_products(db, {p.id, new.id})
    db.commit()
    return {"ok": True, "new_id": new.id, "duplicate": _duplicate_of(db, new) if new.name_status != "UNKNOWN" else None}


@app.get("/api/products/{pid}/merges")
def product_merges(pid: int, db: Session = Depends(get_db)):
    """Merges into / splits out of this product, newest first — what Hoàn tác can revert."""
    rows = db.scalars(select(Event).where(Event.product_id == pid, Event.type.in_(["PRODUCT_MERGED", "PRODUCT_SPLIT"]))
                      .order_by(Event.created_at.desc()).limit(20)).all()
    return [{"id": e.id, "type": e.type, "at": e.created_at, **{k: v for k, v in (e.data or {}).items() if k != "ad_ids"},
             "ads": len((e.data or {}).get("ad_ids") or []), "undone": bool((e.data or {}).get("undone"))} for e in rows]


@app.post("/api/products/{pid}/merges/{eid}/undo")
def undo_merge(pid: int, eid: int, body: ByIn, db: Session = Depends(get_db)):
    """Merge undone = its ads split back out under the old name; split undone = the split product merged back."""
    _product(db, pid)
    try:
        touched = clustering.undo_event(db, pid, eid, body.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rescore_products(db, touched)
    db.commit()
    return {"ok": True}


@app.post("/api/products/{pid}/scan")
def scan_product(pid: int, db: Session = Depends(get_db)):
    """On-demand competitor spy: prices from ad copy + landing pages, reviews from AliExpress / landing pages."""
    from .services import spy

    if not db.get(Product, pid):
        raise HTTPException(404, "Product not found")
    db.commit()  # the scan manages its own transactions (fetches run with no write lock held)
    stats = spy.scan(db, pid, limit=30)
    ads = db.scalars(select(Ad).where(Ad.product_id == pid)).all()
    advs = {a.id: a for a in db.scalars(select(Advertiser).where(Advertiser.id.in_({a.advertiser_id for a in ads if a.advertiser_id})))}
    return {"stats": stats, "mkt": mkt_summary(ads, advs), "comments": comment_summary(db, [pid])}


# ============================================================ Competitor reviews (§17)
def comment_summary(db: Session, product_ids: list[int] | None = None) -> dict:
    q = select(Comment).where(Comment.overall.is_not(None))
    if product_ids is not None:
        q = q.where(Comment.product_id.in_(product_ids))
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
    rated = [c.rating for c in cs if c.rating]
    neg_examples = [{"text": c.text, "rating": c.rating, "source": c.source, "country": c.country, "at": c.created_at}
                    for c in sorted((c for c in cs if c.overall == "negative"), key=lambda c: c.created_at, reverse=True)[:5]]
    pos_examples = [{"text": c.text, "rating": c.rating, "source": c.source, "country": c.country, "at": c.created_at}
                    for c in sorted((c for c in cs if c.overall == "positive"), key=lambda c: c.created_at, reverse=True)[:3]]
    return {
        "analysed": n,
        "sources": dict(Counter(c.source for c in cs)),  # review (AliExpress) / landing_review / ad_comment …
        "rating_avg": round(sum(rated) / len(rated), 2) if rated else None,
        "negative_examples": neg_examples, "positive_examples": pos_examples,
        "overall": {k: round(overall[k] / n, 3) if n else 0 for k in ("positive", "neutral", "negative")},
        "top_positive": [{"aspect": a, "share": round(c / tp, 3), "count": c} for a, c in pos_asp.most_common(6)],
        "top_complaints": [{"aspect": a, "share": round(c / tn, 3), "count": c} for a, c in neg_asp.most_common(8)],
        "purchase_intent": dict(Counter(c.purchase_intent for c in cs)),
        "questions": sum(1 for c in cs if c.is_question),
        "weekly": [{"week": w, **v} for w, v in sorted(weekly.items())],
        "examples": dict(examples),
        "engines": dict(Counter(c.analyzed_by for c in cs)),
    }


# ============================================================ Competitor Radar
@app.get("/api/competitor-radar")
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


@app.get("/api/competitor-radar/{aid}")
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


@app.post("/api/competitor-radar/{aid}/watch")
def watch(aid: int, watched: bool = True, db: Session = Depends(get_db)):
    adv = db.get(Advertiser, aid)
    if not adv:
        raise HTTPException(404)
    adv.is_competitor_watched = watched
    db.commit()
    from .routers.intel import _cache

    for k in [k for k in _cache if isinstance(k, tuple) and k and k[0] == "competitors"]:  # the list is memoised 60 s: a watch must show at once
        _cache.pop(k, None)
    return {"ok": True}


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
