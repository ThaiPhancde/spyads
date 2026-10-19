"""EU Market API (spec §8). Reads are open like the rest of the app; every write — including admin/sync — goes
through main.AdminGuard (X-Admin-Token, attached server-side by the web middleware). Mounted only when
EU_MARKET_ENABLED is on."""
from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Ad
from . import allegro_bot, allegro_import, analytics, connectors, jobs
from .connectors.base import ConnectorError
from .models import (EUAdMatch, EUBotJob, EUFxRate, EUListing, EUPriceSnapshot, EUProduct, EUProductMatch, EUSupplier, EUSyncRun,
                     EUTrendSignal, EUWatchlist)
from .service import fold, fx_table, listing_dict, listing_filters, split_keywords, to_eur, tokens, upsert_items

router = APIRouter(prefix="/api/eu-market", tags=["eu-market"])
REVIEW = {"pending", "confirmed", "rejected"}


def _csv(v: str | None, upper=True) -> list[str] | None:
    out = [x.strip().upper() if upper else x.strip().lower() for x in re.split(r"[,|]", v or "") if x.strip()]
    return out or None


def _window(w: str | None, default=30) -> int:
    m = re.fullmatch(r"(\d+)d?", (w or "").strip())
    return min(365, int(m.group(1))) if m else default


def _product_brief(p: EUProduct) -> dict:
    return {"id": p.id, "title": p.canonical_title, "brand": p.brand, "category": p.category, "ean": p.ean, "image_url": p.image_url,
            "marketer_tags": p.marketer_tags or [], "excluded": p.excluded, "excluded_reason": p.excluded_reason,
            "compliance_flags": analytics.compliance_flags(p.canonical_title), "created_at": p.created_at}


def _ad_counts(db: Session, pids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = defaultdict(lambda: {"confirmed": 0, "pending": 0})
    for pid, st, n in db.execute(select(EUAdMatch.product_id, EUAdMatch.review_status, func.count()).where(
            EUAdMatch.product_id.in_(pids or [-1])).group_by(EUAdMatch.product_id, EUAdMatch.review_status)).all():
        if st in ("confirmed", "pending"):
            out[pid][st] = n
    return out


def _cards(db: Session, pids: list[int], fx: dict, keywords: list[str] | None = None) -> list[dict]:
    prods = {p.id: p for p in db.scalars(select(EUProduct).where(EUProduct.id.in_(pids or [-1]))).all()}
    offers = defaultdict(list)
    for l in db.scalars(select(EUListing).where(EUListing.product_id.in_(pids or [-1])).order_by(EUListing.last_seen_at.desc())).all():
        offers[l.product_id].append(l)
    ads = _ad_counts(db, pids)
    out = []
    for pid in pids:
        p = prods.get(pid)
        if not p:
            continue
        ls = offers[pid]
        eur = [e["eur"] for l in ls if l.kind == "marketplace_listing" for e in [to_eur(l.price, l.currency, fx)] if e]
        why = []
        if keywords:
            hit = [k for k in keywords if all(w in fold(" ".join(l.search_text or "" for l in ls)) for w in k.split())]
            if hit:
                why.append("khớp từ khoá: " + ", ".join(hit[:3]))
        if p.ean and len({l.source for l in ls}) > 1:
            why.append(f"cùng EAN trên {len({l.source for l in ls})} nguồn")
        if ads[pid]["confirmed"]:
            why.append(f"{ads[pid]['confirmed']} quảng cáo đã xác nhận trong kho SpyAds")
        if any(l.kind == "supplier_offer" for l in ls):
            why.append("có nguồn nhập B2B")
        out.append({**_product_brief(p), "offers": [listing_dict(l, fx) for l in ls], "offer_count": len(ls),
                    "sources": sorted({l.source for l in ls}), "countries": sorted({l.market_country for l in ls}),
                    "min_price_eur": min(eur) if eur else None, "max_price_eur": max(eur) if eur else None,
                    "last_seen_at": max((l.last_seen_at for l in ls), default=None),
                    "first_seen_at": min((l.observed_at for l in ls), default=None), "ads": ads[pid], "reasons": why})
    return out


# ------------------------------------------------------------------ overview / sources
@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    by_src = Counter(dict(db.execute(select(EUListing.source, func.count()).group_by(EUListing.source)).all()))
    by_cty = Counter(dict(db.execute(select(EUListing.market_country, func.count()).group_by(EUListing.market_country)).all()))
    by_kind = dict(db.execute(select(EUListing.kind, func.count()).group_by(EUListing.kind)).all())
    new7 = db.scalars(select(EUListing.product_id).where(EUListing.observed_at >= now - timedelta(days=7)).distinct().limit(5000)).all()
    fx_day = db.scalar(select(func.max(EUFxRate.rate_date)))
    cat = connectors.catalogue()
    return {"enabled": True, "products": db.scalar(select(func.count(EUProduct.id))) or 0, "listings": sum(by_src.values()),
            "by_source": dict(by_src), "by_country": dict(by_cty.most_common()), "by_kind": by_kind,
            "suppliers": db.scalar(select(func.count(EUSupplier.id))) or 0,
            "snapshots": db.scalar(select(func.count(EUPriceSnapshot.id))) or 0,
            "ad_matches": dict(db.execute(select(EUAdMatch.review_status, func.count()).group_by(EUAdMatch.review_status)).all()),
            "new_products_7d": len(set(new7)), "fx_rate_date": fx_day,
            "sources": {"available": sum(c["status"] == "available" for c in cat), "configured": sum(c["configured"] for c in cat),
                        "planned": sum(c["status"] == "planned" for c in cat)},
            "recent_products": _cards(db, [p for p in dict.fromkeys(reversed(new7)) if p][:8], fx_table(db)),
            "last_runs": [_run(r) for r in db.scalars(select(EUSyncRun).order_by(EUSyncRun.started_at.desc()).limit(10)).all()],
            "default_countries": jobs.DEFAULT_COUNTRIES}


def _run(r: EUSyncRun) -> dict:
    return {"id": r.id, "connector": r.connector, "query": r.query, "country": r.country, "status": r.status, "started_at": r.started_at,
            "ended_at": r.ended_at, "items_read": r.items_read, "items_written": r.items_written, "error_code": r.error_code,
            "error_detail": r.error_detail, "next_cursor": r.next_cursor}


@router.get("/sources")
def sources(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(EUListing.source, func.count()).group_by(EUListing.source)).all())
    out = []
    for c in connectors.catalogue():
        last = db.scalar(select(EUSyncRun).where(EUSyncRun.connector == c["source"]).order_by(EUSyncRun.started_at.desc()).limit(1))
        ok = db.scalar(select(func.max(EUSyncRun.ended_at)).where(EUSyncRun.connector == c["source"], EUSyncRun.status == "ok"))
        state = c["status"] if c["status"] != "available" else "missing_credentials" if not c["configured"] else \
            (last.status if last and last.status != "running" else "ready")
        extra = {}
        if c["source"] == "allegro":  # import-only source: the file, not credentials, decides the state
            extra = {"import": allegro_import.status()}
            if extra["import"]["state"] != "file_found" and not last:
                state = extra["import"]["state"]
        out.append({**c, **extra, "state": state, "listings": counts.get(c["source"], 0), "last_run": _run(last) if last else None, "last_ok_at": ok})
    return {"sources": out, "fx_rate_date": db.scalar(select(func.max(EUFxRate.rate_date)))}


class FromUrlIn(BaseModel):
    url: str
    index_image: bool = True


@router.post("/listings/from-url")
def listing_from_url(body: FromUrlIn, db: Session = Depends(get_db)):
    """Public product page → EU listing (JSON-LD Product/Offer as the shop publishes it) + its picture into the image
    index, so Price Intelligence / image search see it at once. No login, no anti-bot bypass (connectors/jsonld.py)."""
    from .connectors import jsonld

    try:
        it = jsonld.fetch_product(body.url.strip())
    except ConnectorError as e:
        raise HTTPException(400 if e.code != "temporarily_unavailable" else 503, {"code": e.code, "message": e.detail or e.code})
    ids = upsert_items(db, [it], query=None)
    if not ids:
        raise HTTPException(400, {"code": "bad_response", "message": "page had a Product but no usable title / id"})
    out = {"listing": listing_dict(db.get(EUListing, ids[0]), fx_table(db)), "image_indexed": False}
    if body.index_image and it.get("thumbnail_url"):
        from ..visual.service import index_one

        try:
            out["image_indexed"] = index_one("eu_listing", ids[0]) is not None
        except Exception as e:  # noqa: BLE001 — the listing is saved either way; the worker retries the picture
            out["image_error"] = str(e)[:200]
    return out


@router.get("/imports/allegro")
def allegro_status():
    return allegro_import.status()


@router.post("/imports/allegro")
def allegro_run(db: Session = Depends(get_db)):
    """Import apps/api/data/imports/allegro.json (Grok Bot) into eu_listings; idempotent on source + offer_id."""
    run = jobs._start("allegro", "allegro.json", "PL")
    try:
        res = allegro_import.import_file(db)
    except FileNotFoundError:
        jobs._finish(run, error=ConnectorError("no_data", "allegro.json chưa tồn tại"))
        raise HTTPException(404, {"code": "no_data", "message": "Chưa có file allegro.json — chưa có dữ liệu Allegro."})
    except ValueError as e:
        jobs._finish(run, error=ConnectorError("bad_response", str(e)))
        raise HTTPException(400, {"code": "bad_response", "message": str(e)})
    jobs._finish(run, read=res["rows"], written=res["written"])
    return {k: v for k, v in res.items() if k != "listing_ids"}


class BotSearchIn(BaseModel):
    keyword: str
    limit: int = Field(20, ge=1, le=50)
    force: bool = False  # skip the cache (a new paid search)
    provider: str = "serp"  # serp = SpyAds → SerpApi Google Shopping; grok = task for the Grok Bot agent; bridge = user's Chrome extension reports via /api/imports/allegro


def _bot_job_out(db: Session, j: dict) -> dict:
    ids = j.get("listing_ids") or []
    pids = list(dict.fromkeys(p for p in db.scalars(select(EUListing.product_id).where(EUListing.id.in_(ids or [-1]))).all() if p))
    return {**j, "rows": _cards(db, pids, fx_table(db)) if pids else []}


@router.get("/allegro/bot")
def allegro_bot_state(limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    allegro_bot.expire(db)
    jobs_ = db.scalars(select(EUBotJob).where(EUBotJob.source == "allegro").order_by(EUBotJob.created_at.desc()).limit(limit)).all()
    return {"config": allegro_bot.config(), "http_crawler_enabled": False, "jobs": [allegro_bot.job_dict(j) for j in jobs_]}


@router.post("/allegro/search")
def allegro_bot_search(body: BotSearchIn, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """MKT search. Returns the job at once (cached / in-flight reuse included); poll GET /allegro/jobs/{id}."""
    try:
        j = allegro_bot.request_search(db, body.keyword, body.limit, body.force, provider=body.provider)
    except allegro_bot.BotConfigError as e:
        raise HTTPException(503, {"code": e.code, "message": e.detail})
    except ValueError as e:
        raise HTTPException(422, {"code": "bad_request", "message": str(e)})
    if j["status"] == "running" and not j.get("reused"):
        bg.add_task(allegro_bot.run_serp, j["id"])
    out = _bot_job_out(db, j)
    db.commit()  # end this session's transaction before the background job writes (SQLite single writer)
    return out


@router.get("/allegro/jobs/{job_id}")
def allegro_bot_job(job_id: str, db: Session = Depends(get_db)):
    allegro_bot.expire(db)
    j = db.get(EUBotJob, job_id)
    if not j:
        raise HTTPException(404, {"code": "not_found", "message": "job không tồn tại"})
    return _bot_job_out(db, allegro_bot.job_dict(j))


@router.post("/allegro/callback/{job_id}")
async def allegro_bot_callback(job_id: str, request: Request, token: str | None = None, db: Session = Depends(get_db)):
    """Called by Grok Bot (exempt from AdminGuard): per-job token (query) + X-Callback-Secret (header or body "secret")."""
    if int(request.headers.get("content-length") or 0) > 2_000_000:
        raise HTTPException(413, "payload too large")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, {"code": "bad_json", "message": "body must be JSON"})
    if not isinstance(body, dict):
        raise HTTPException(400, {"code": "bad_json", "message": "body must be a JSON object"})
    j = db.get(EUBotJob, job_id)
    if not allegro_bot.verify(j, token, request.headers.get("x-callback-secret") or body.get("secret")):
        raise HTTPException(401, {"code": "unauthorized", "message": "invalid job / token / secret"})
    out = allegro_bot.handle_callback(db, j, body)
    return {k: out[k] for k in ("id", "status", "items_received", "items_written") if k in out}


@router.post("/sources/{source}/healthcheck")
async def healthcheck(source: str):
    try:
        return {"source": source, **(await connectors.get(source).healthcheck())}
    except ConnectorError as e:
        return {"source": source, "ok": False, "error": e.code, "detail": e.detail[:300]}


# ------------------------------------------------------------------ Product Explorer
@router.get("/products")
def products(q: str | None = None, keywords: str | None = None, countries: str | None = None, sources: str | None = None,
             kind: str | None = None, brand: str | None = None, seller: str | None = None, category: str | None = None,
             price_min: float | None = None, price_max: float | None = None, days: int | None = None,
             min_confidence: float | None = None, has_ads: bool = False, sort: str = "recent",
             offset: int = 0, limit: int = Query(24, le=100), db: Session = Depends(get_db)):
    kws = split_keywords(" | ".join(x for x in (q, keywords) if x))
    f = listing_filters(kws, _csv(countries), _csv(sources, upper=False), kind, brand, seller, category, price_min, price_max, days,
                        min_confidence)
    f.append(EUListing.product_id.is_not(None))
    if has_ads:
        f.append(EUListing.product_id.in_(select(EUAdMatch.product_id).where(EUAdMatch.review_status == "confirmed")))
    rows = db.execute(select(EUListing.product_id, func.count(), func.max(EUListing.last_seen_at)).where(*f)
                      .group_by(EUListing.product_id)).all()
    fx = fx_table(db)
    if sort in ("price_asc", "price_desc"):  # min price in EUR (reference rates); unknown prices sort last
        best: dict[int, float] = {}
        for pid, price, cur in db.execute(select(EUListing.product_id, EUListing.price, EUListing.currency).where(*f)).all():
            e = to_eur(price, cur, fx)
            if e:
                best[pid] = min(best.get(pid, e["eur"]), e["eur"])
        rows.sort(key=lambda r: (r[0] not in best, best.get(r[0], 0) * (1 if sort == "price_asc" else -1)))
    elif sort == "offers":
        rows.sort(key=lambda r: (r[1], r[2] or datetime.min), reverse=True)
    else:
        rows.sort(key=lambda r: r[2] or datetime.min, reverse=True)
    page = [r[0] for r in rows[offset:offset + limit]]
    return {"total": len(rows), "offset": offset, "limit": limit, "keywords": kws, "rows": _cards(db, page, fx, kws),
            "searchable_sources": connectors.searchable()}


@router.get("/products/{pid}")
def product(pid: int, window: str = "90d", db: Session = Depends(get_db)):
    p = db.get(EUProduct, pid)
    if not p:
        raise HTTPException(404, "product not found")
    fx, now, days = fx_table(db), datetime.utcnow(), _window(window, 90)
    card = _cards(db, [pid], fx)[0]
    ls = db.scalars(select(EUListing).where(EUListing.product_id == pid)).all()
    series = analytics.price_series(db, [l.id for l in ls], now - timedelta(days=days), fx)
    sig = db.scalars(select(EUTrendSignal).where(EUTrendSignal.product_id == pid)).all()
    return {**card, "price_compare": analytics.compare_offers(ls, fx),
            "landed_cost": {l.id: analytics.landed_cost(l) for l in ls},
            "price_history": {l.id: {"points": series.get(l.id, []), "stats": analytics.price_stats(series.get(l.id, []))} for l in ls},
            "trend": analytics.trend_score(ls, sig, card["ads"]["confirmed"], series, now),
            "rarity": _rarity_for(db, p, ls, fx, now),
            "signals": [{"listing_id": s.listing_id, "type": s.signal_type, "value": s.value, "source": s.source, "country": s.country,
                         "captured_at": s.captured_at} for s in sorted(sig, key=lambda s: s.captured_at)[-200:]],
            "ad_matches": _ad_matches(db, product_id=pid), "supplier_matches": _supplier_matches(db, product_id=pid)}


class ProductPatch(BaseModel):
    marketer_tags: list[str] | None = None
    excluded: bool | None = None
    excluded_reason: str | None = None


@router.patch("/products/{pid}")
def patch_product(pid: int, body: ProductPatch, db: Session = Depends(get_db)):
    p = db.get(EUProduct, pid)
    if not p:
        raise HTTPException(404, "product not found")
    if body.marketer_tags is not None:
        bad = set(body.marketer_tags) - set(analytics.MANUAL_TAGS)
        if bad:
            raise HTTPException(422, f"unknown tags {sorted(bad)}; allowed {analytics.MANUAL_TAGS}")
        p.marketer_tags = body.marketer_tags
    if body.excluded is not None:
        p.excluded, p.excluded_reason = body.excluded, (body.excluded_reason or None) if body.excluded else None
    db.commit()
    return _product_brief(p)


# ------------------------------------------------------------------ Trending / Prices / Rare
def _scope_products(db: Session, country: str | None, keywords: str | None, kind="marketplace_listing", cap=2000) -> list[int]:
    f = listing_filters(split_keywords(keywords), _csv(country), None, kind)
    return [p for p in db.scalars(select(EUListing.product_id).where(*f, EUListing.product_id.is_not(None)).distinct().limit(cap)).all()]


@router.get("/trending")
def trending(country: str | None = None, keywords: str | None = None, window: str = "30d", limit: int = Query(40, le=200),
             db: Session = Depends(get_db)):
    fx, now, days = fx_table(db), datetime.utcnow(), _window(window)
    pids = _scope_products(db, country, keywords)  # ponytail: scores computed per request over ≤2000 products; precompute past that
    ls_by, sig_by = defaultdict(list), defaultdict(list)
    for l in db.scalars(select(EUListing).where(EUListing.product_id.in_(pids or [-1]))).all():
        ls_by[l.product_id].append(l)
    for s in db.scalars(select(EUTrendSignal).where(EUTrendSignal.product_id.in_(pids or [-1]),
                                                    EUTrendSignal.captured_at >= now - timedelta(days=days))).all():
        sig_by[s.product_id].append(s)
    series = analytics.price_series(db, [l.id for ls in ls_by.values() for l in ls], now - timedelta(days=days), fx)
    ads = _ad_counts(db, pids)
    excluded = set(db.scalars(select(EUProduct.id).where(EUProduct.id.in_(pids or [-1]), EUProduct.excluded.is_(True))).all())
    scored = []
    for pid in pids:
        if pid in excluded:
            continue
        ls = ls_by[pid]
        t = analytics.trend_score(ls, sig_by[pid], ads[pid]["confirmed"], {l.id: series.get(l.id, []) for l in ls}, now, days)
        scored.append((t["insufficient_data"], -t["score"], pid, t))
    scored.sort(key=lambda x: x[:2])
    top = scored[:limit]
    cards = {c["id"]: c for c in _cards(db, [x[2] for x in top], fx)}
    items = [{**cards[x[2]], "trend": x[3]} for x in top if x[2] in cards and not cards[x[2]]["compliance_flags"]]
    return {"window_days": days, "items": items, "scanned": len(pids),
            "method": "mean of available components: new listings 7d, source/country presence, search-rank change, rating-count growth, "
                      "price change, confirmed ad matches; insufficient_data when <7 days of history or ≥3 components missing"}


@router.get("/prices")
def prices(product_id: int | None = None, listing_id: int | None = None, window: str = "90d", country: str | None = None,
           limit: int = Query(40, le=200), db: Session = Depends(get_db)):
    fx, now, days = fx_table(db), datetime.utcnow(), _window(window, 90)
    since = now - timedelta(days=days)
    if product_id or listing_id:
        q = select(EUListing).where(EUListing.id == listing_id) if listing_id else select(EUListing).where(EUListing.product_id == product_id)
        ls = db.scalars(q).all()
        series = analytics.price_series(db, [l.id for l in ls], since, fx)
        return {"window_days": days, "listings": [{**listing_dict(l, fx), "points": series.get(l.id, []),
                                                    "stats": analytics.price_stats(series.get(l.id, [])),
                                                    "landed_cost": analytics.landed_cost(l)} for l in ls],
                "compare": analytics.compare_offers(ls, fx)}
    # movers: listings with ≥2 snapshots in the window, biggest absolute change first
    multi = db.execute(select(EUPriceSnapshot.listing_id).where(EUPriceSnapshot.captured_at >= since)
                       .group_by(EUPriceSnapshot.listing_id).having(func.count() >= 2)).scalars().all()
    q = select(EUListing).where(EUListing.id.in_(multi or [-1]))
    if country:
        q = q.where(EUListing.market_country.in_(_csv(country)))
    ls = db.scalars(q).all()
    series = analytics.price_series(db, [l.id for l in ls], since, fx)
    rows = [{**listing_dict(l, fx), "stats": analytics.price_stats(series.get(l.id, [])), "points": series.get(l.id, [])} for l in ls]
    rows = [r for r in rows if r["stats"].get("change_pct") is not None]
    rows.sort(key=lambda r: -abs(r["stats"]["change_pct"]))
    single = db.scalar(select(func.count(func.distinct(EUPriceSnapshot.listing_id)))) or 0
    return {"window_days": days, "movers": rows[:limit], "listings_with_history": len(ls), "listings_with_any_price": single,
            "note": "only real snapshots; a listing with one snapshot has a price but no trend"}


def _rarity_for(db: Session, p: EUProduct, ls: list[EUListing], fx: dict, now: datetime, ctx: dict | None = None) -> dict:
    ctx = ctx or _rarity_ctx(db, fx)
    mine = tokens(p.canonical_title)
    similar = [q for q, t in ctx["tokens"].items() if q != p.id and analytics.jaccard(mine, t) >= 0.5]
    prices = sorted(ctx["price"][q] for q in similar if q in ctx["price"])
    band = (prices[len(prices) // 10], prices[-1 - len(prices) // 10]) if len(prices) >= 5 else None
    return analytics.rarity(p, ls, len(similar), ctx["dataset"], now, ctx["price"].get(p.id), band)


def _rarity_ctx(db: Session, fx: dict) -> dict:
    toks, price = {}, {}
    for pid, title in db.execute(select(EUProduct.id, EUProduct.canonical_title)).all():
        toks[pid] = tokens(title)
    for pid, pr, cur in db.execute(select(EUListing.product_id, EUListing.price, EUListing.currency)
                                   .where(EUListing.kind == "marketplace_listing")).all():
        e = to_eur(pr, cur, fx)
        if e and pid:
            price[pid] = min(price.get(pid, e["eur"]), e["eur"])
    return {"tokens": toks, "price": price, "dataset": db.scalar(select(func.count(EUListing.id))) or 0}


@router.get("/rare")
def rare(country: str | None = None, keywords: str | None = None, min_score: int = 40, tag: str | None = None,
         min_confidence: str | None = None, limit: int = Query(40, le=200), db: Session = Depends(get_db)):
    fx, now = fx_table(db), datetime.utcnow()
    pids = _scope_products(db, country, keywords)
    ctx = _rarity_ctx(db, fx)  # ponytail: O(products²) similarity — fine for a few thousand products
    prods = {p.id: p for p in db.scalars(select(EUProduct).where(EUProduct.id.in_(pids or [-1]), EUProduct.excluded.is_(False))).all()}
    ls_by = defaultdict(list)
    for l in db.scalars(select(EUListing).where(EUListing.product_id.in_(list(prods) or [-1]))).all():
        ls_by[l.product_id].append(l)
    out = []
    for pid, p in prods.items():
        if analytics.compliance_flags(p.canonical_title) or (tag and tag not in (p.marketer_tags or [])):
            continue
        r = _rarity_for(db, p, ls_by[pid], fx, now, ctx)
        if r["score"] >= min_score and (not min_confidence or ["low", "medium", "high"].index(r["confidence"]) >= ["low", "medium", "high"].index(min_confidence)):
            out.append((r["score"], pid, r))
    out.sort(key=lambda x: -x[0])
    cards = {c["id"]: c for c in _cards(db, [x[1] for x in out[:limit]], fx)}
    return {"items": [{**cards[pid], "rarity": r} for _, pid, r in out[:limit] if pid in cards], "dataset_listings": ctx["dataset"],
            "manual_tags": analytics.MANUAL_TAGS,
            "note": "rare_in_dataset = few equivalents in the sources we collected; never a claim about the whole EU market. "
                    "Products with compliance flags (counterfeit, weapons, nicotine…) or excluded by a marketer are not suggested."}


# ------------------------------------------------------------------ Suppliers
@router.get("/suppliers")
def suppliers(country: str | None = None, category: str | None = None, q: str | None = None, offset: int = 0,
              limit: int = Query(40, le=200), db: Session = Depends(get_db)):
    fx = fx_table(db)
    sups = db.scalars(select(EUSupplier).order_by(EUSupplier.name)).all()
    if country:
        sups = [s for s in sups if s.country in (_csv(country) or [])]
    f = listing_filters(split_keywords(q), None, None, "supplier_offer", category=category)
    if country:
        f.append(EUListing.supplier_id.in_([s.id for s in sups] or [-1]))
    total = db.scalar(select(func.count(EUListing.id)).where(*f)) or 0
    offers = db.scalars(select(EUListing).where(*f).order_by(EUListing.last_seen_at.desc()).offset(offset).limit(limit)).all()
    counts = dict(db.execute(select(EUListing.supplier_id, func.count()).where(EUListing.kind == "supplier_offer")
                             .group_by(EUListing.supplier_id)).all())
    return {"suppliers": [{"id": s.id, "source": s.source, "name": s.name, "supplier_type": s.supplier_type, "country": s.country,
                           "website": s.website, "moq": s.moq, "lead_time_days": s.lead_time_days, "vat_note": s.vat_note,
                           "access_terms": s.access_terms, "offers": counts.get(s.id, 0)} for s in sups],
            "offers": [listing_dict(l, fx) for l in offers], "total": total, "offset": offset,
            "matches_pending": db.scalar(select(func.count(EUProductMatch.id)).where(EUProductMatch.review_status == "pending")) or 0}


def _supplier_matches(db: Session, product_id: int | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    q = select(EUProductMatch).order_by(EUProductMatch.score.desc()).limit(limit)
    if product_id:
        q = q.where(EUProductMatch.product_id == product_id)
    if status:
        q = q.where(EUProductMatch.review_status == status)
    ms = db.scalars(q).all()
    fx = fx_table(db)
    ls = {l.id: l for l in db.scalars(select(EUListing).where(EUListing.id.in_([m.supplier_listing_id for m in ms] or [-1]))).all()}
    return [{"id": m.id, "product_id": m.product_id, "match_method": m.match_method, "score": m.score, "evidence": m.evidence,
             "review_status": m.review_status, "offer": listing_dict(ls[m.supplier_listing_id], fx) if m.supplier_listing_id in ls else None}
            for m in ms]


@router.get("/supplier-matches")
def supplier_matches(product_id: int | None = None, status: str | None = None, db: Session = Depends(get_db)):
    return {"items": _supplier_matches(db, product_id, status, 200)}


class Review(BaseModel):
    status: str


@router.post("/supplier-matches/{mid}/review")
def review_supplier_match(mid: int, body: Review, db: Session = Depends(get_db)):
    return _review(db, EUProductMatch, mid, body.status)


def _review(db: Session, model, mid: int, status: str):
    if status not in REVIEW:
        raise HTTPException(422, f"status must be one of {sorted(REVIEW)}")
    m = db.get(model, mid)
    if not m:
        raise HTTPException(404, "match not found")
    m.review_status = status
    db.commit()
    return {"id": m.id, "review_status": m.review_status}


# ------------------------------------------------------------------ Competitor shops
@router.get("/shops")
def shops(source: str | None = None, country: str | None = None, q: str | None = None, limit: int = Query(60, le=300),
          db: Session = Depends(get_db)):
    now, fx = datetime.utcnow(), fx_table(db)
    f = listing_filters(None, _csv(country), _csv(source, upper=False), "marketplace_listing")
    f.append(func.coalesce(EUListing.seller_name, EUListing.seller_id).is_not(None))
    groups: dict[tuple, dict] = {}
    for l in db.scalars(select(EUListing).where(*f)).all():
        name = l.seller_name or l.seller_id
        if q and fold(q) not in fold(name):
            continue
        g = groups.setdefault((l.source, name), {"source": l.source, "seller": name, "seller_id": l.seller_id, "listings": 0,
                                                 "countries": set(), "categories": Counter(), "prices_eur": [], "new_7d": 0,
                                                 "last_seen_at": l.last_seen_at})
        g["listings"] += 1
        g["countries"].add(l.market_country)
        if l.category:
            g["categories"][l.category] += 1
        if e := to_eur(l.price, l.currency, fx):
            g["prices_eur"].append(e["eur"])
        g["new_7d"] += l.observed_at >= now - timedelta(days=7)
        g["last_seen_at"] = max(g["last_seen_at"], l.last_seen_at)
    out = []
    for g in groups.values():
        pr = g.pop("prices_eur")
        out.append({**g, "countries": sorted(g["countries"]), "categories": [c for c, _ in g["categories"].most_common(3)],
                    "price_min_eur": min(pr) if pr else None, "price_max_eur": max(pr) if pr else None})
    out.sort(key=lambda g: -g["listings"])
    return {"shops": out[:limit], "total": len(out),
            "note": "sellers = winner of the buybox (Kaufland) / best offer (bol) on listings we observed — not a full shop crawl"}


@router.get("/shops/listings")
def shop_listings(source: str, seller: str, db: Session = Depends(get_db)):
    fx = fx_table(db)
    ls = db.scalars(select(EUListing).where(EUListing.source == source, func.coalesce(EUListing.seller_name, EUListing.seller_id) == seller)
                    .order_by(EUListing.last_seen_at.desc()).limit(300)).all()
    return {"items": [listing_dict(l, fx) for l in ls]}


# ------------------------------------------------------------------ Ad matches
def _ad_matches(db: Session, product_id: int | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    q = select(EUAdMatch).order_by(EUAdMatch.score.desc(), EUAdMatch.created_at.desc()).limit(limit)
    if product_id:
        q = q.where(EUAdMatch.product_id == product_id)
    if status:
        q = q.where(EUAdMatch.review_status == status)
    ms = db.scalars(q).all()
    ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.id.in_([m.ad_id for m in ms] or [-1]))).all()}
    prods = {p.id: p for p in db.scalars(select(EUProduct).where(EUProduct.id.in_([m.product_id for m in ms] or [-1]))).all()}
    out = []
    for m in ms:
        a, p = ads.get(m.ad_id), prods.get(m.product_id)
        out.append({"id": m.id, "product_id": m.product_id, "product_title": p.canonical_title if p else None,
                    "product_image": p.image_url if p else None, "match_method": m.match_method, "score": m.score,
                    "strength": "strong" if m.match_method in ("url", "ean") else "weak", "evidence": m.evidence,
                    "review_status": m.review_status,
                    "ad": {"id": a.id, "title": a.title or a.raw_product_name, "text": (a.ad_text or "")[:280], "network": a.network,
                           "platform": a.platform, "country": a.country, "countries": a.countries or [], "landing_url": a.landing_url,
                           "snapshot_url": a.snapshot_url, "is_active": a.is_active, "first_seen_at": a.first_seen_at,
                           "last_seen_at": a.last_seen_at, "product_id": a.product_id} if a else None})
    return out


@router.get("/ad-matches")
def ad_matches(product_id: int | None = None, status: str | None = None, limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    return {"items": _ad_matches(db, product_id, status, limit),
            "note": "‘đã chạy ads’ only for confirmed matches to real ad records; weak (title) matches wait for review. "
                    "No revenue / ROAS / orders are inferred from creatives."}


@router.post("/ad-matches/{mid}/review")
def review_ad_match(mid: int, body: Review, db: Session = Depends(get_db)):
    return _review(db, EUAdMatch, mid, body.status)


# ------------------------------------------------------------------ Watchlists
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    owner: str = "team"
    keywords: list[str] = []
    countries: list[str] = []
    sources: list[str] = []
    product_ids: list[int] = []
    rules: dict = {}
    active: bool = True


def _wl(w: EUWatchlist) -> dict:
    return {"id": w.id, "owner": w.owner, "name": w.name, "keywords": w.keywords, "countries": w.countries, "sources": w.sources,
            "product_ids": w.product_ids, "rules": w.rules, "active": w.active, "created_at": w.created_at,
            "last_synced_at": w.last_synced_at}


def _clean_wl(body: WatchlistIn) -> dict:
    rules = {k: float(v) for k, v in (body.rules or {}).items() if k in ("price_drop_pct", "source_gap_pct", "consecutive_rises") and v}
    return {**body.model_dump(), "keywords": split_keywords(body.keywords), "countries": [c.upper() for c in body.countries],
            "sources": [s for s in body.sources if s in connectors.CONNECTORS], "rules": rules}


@router.get("/watchlists")
def watchlists(owner: str | None = None, db: Session = Depends(get_db)):
    q = select(EUWatchlist).order_by(EUWatchlist.created_at.desc())
    if owner:
        q = q.where(EUWatchlist.owner == owner)
    return {"items": [_wl(w) for w in db.scalars(q).all()]}


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, db: Session = Depends(get_db)):
    w = EUWatchlist(**_clean_wl(body))
    db.add(w)
    db.commit()
    return _wl(w)


@router.put("/watchlists/{wid}")
def update_watchlist(wid: int, body: WatchlistIn, db: Session = Depends(get_db)):
    w = db.get(EUWatchlist, wid)
    if not w:
        raise HTTPException(404, "watchlist not found")
    for k, v in _clean_wl(body).items():
        setattr(w, k, v)
    db.commit()
    return _wl(w)


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int, db: Session = Depends(get_db)):
    w = db.get(EUWatchlist, wid)
    if w:
        db.delete(w)
        db.commit()
    return {"deleted": wid}


# ------------------------------------------------------------------ admin (writes need X-Admin-Token via AdminGuard)
class SyncIn(BaseModel):
    keywords: list[str] | str = []
    countries: list[str] = []
    sources: list[str] = []


def _bg_search(kws, countries, sources):
    asyncio.run(jobs.run_search(kws, countries, sources))
    with jobs.SessionLocal() as db:  # new listings → link supplier offers / existing ads right away
        analytics.match_suppliers(db)


@router.post("/admin/sync")
def admin_sync(body: SyncIn, bg: BackgroundTasks):
    """Live search: runs after the response (sync-run rows show progress). Unconfigured sources are reported now."""
    kws = split_keywords(body.keywords)[:10]
    if not kws:
        raise HTTPException(422, "keywords required")
    countries = [c.upper() for c in body.countries] or jobs.DEFAULT_COUNTRIES
    srcs = body.sources or [s for s, c in connectors.CONNECTORS.items() if c.status == "available" and "search" in c.capabilities]
    ready, problems = [], []
    for s in srcs:
        c = connectors.get(s) if s in connectors.CONNECTORS else None
        if not c or c.status != "available" or "search" not in c.capabilities:
            problems.append({"source": s, "error": "unsupported_capability"})
        elif not c.configured():
            problems.append({"source": s, "error": "missing_credentials", "detail": "set " + ", ".join(c.env_vars)})
        elif not set(countries) & set(c.countries):
            problems.append({"source": s, "error": "unsupported_capability", "detail": f"countries: {', '.join(c.countries)}"})
        else:
            ready.append(s)
    if ready:
        bg.add_task(_bg_search, kws, countries, ready)
    return {"started": bool(ready), "started_at": datetime.utcnow(), "sources": ready, "problems": problems, "keywords": kws,
            "countries": countries}


@router.post("/admin/match")
def admin_match(bg: BackgroundTasks):
    bg.add_task(jobs.match)
    return {"started": True}


@router.post("/admin/fx")
def admin_fx():
    try:
        return jobs.fx()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ECB feed: {e}") from e


@router.post("/admin/run-jobs")
def admin_run_jobs(bg: BackgroundTasks):
    bg.add_task(jobs.run_all)
    return {"started": True}


@router.get("/admin/sync-runs")
def sync_runs(source: str | None = None, since: datetime | None = None, limit: int = Query(50, le=500), db: Session = Depends(get_db)):
    q = select(EUSyncRun).order_by(EUSyncRun.started_at.desc()).limit(limit)
    if source:
        q = q.where(EUSyncRun.connector == source)
    if since:
        q = q.where(EUSyncRun.started_at >= since)
    runs = db.scalars(q).all()
    return {"items": [_run(r) for r in runs], "running": sum(r.status == "running" for r in runs)}


# ------------------------------------------------------------------ Grok Bot tool API (contract spyads.agent.v1)
agent_router = APIRouter(prefix="/api/agent/v1", tags=["agent"])


class ToolSearchIn(BaseModel):
    keyword: str
    limit: int = Field(20, ge=1, le=50)
    job_id: str | None = None  # the task this call serves (audit only)


@agent_router.post("/tools/allegro_search")
def tool_allegro_search(body: ToolSearchIn, request: Request, db: Session = Depends(get_db)):
    """Internal search tool for the agent (exempt from AdminGuard; Bearer GROK_TOOL_TOKEN). Cache first, else one SerpApi
    call (shared budget). Returns only stored, real listings."""
    if not allegro_bot.verify_tool(request.headers.get("authorization")):
        raise HTTPException(401, {"code": "unauthorized", "message": "Authorization: Bearer <GROK_TOOL_TOKEN>"})
    try:
        j = allegro_bot.request_search(db, body.keyword, body.limit, provider="serp", reuse_in_flight=False)
    except allegro_bot.BotConfigError as e:
        raise HTTPException(503, {"code": e.code, "message": e.detail})
    except ValueError as e:
        raise HTTPException(422, {"code": "bad_request", "message": str(e)})
    if j["status"] == "running":
        j = allegro_bot.run_serp(j["id"])
    fx = fx_table(db)
    ls = [listing_dict(l, fx) for l in db.scalars(select(EUListing).where(EUListing.id.in_(j.get("listing_ids") or [-1]))).all()]
    keep = ("source", "source_product_id", "title", "price", "currency", "price_eur", "thumbnail_url", "product_url", "seller_name",
            "market_country", "data_access")
    return {"contract": allegro_bot.CONTRACT, "type": "tool_result", "tool": "allegro_search", "job_id": body.job_id,
            "search_job_id": j.get("id"), "status": "ok" if ls else ("empty" if j.get("status") == "empty" else "error"),
            "cached": j.get("reused") == "cache", "error": j.get("error_detail"),
            "items": [{k: l[k] for k in keep} for l in ls][: body.limit],
            "notes": "Google Shopping (SerpApi): product_url = Google Shopping page, seller_name not provided by the source."}
