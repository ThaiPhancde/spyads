"""Product Search · Creative Vault · Discovery · Realtime · Unified Collector API."""
from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, case, func, literal, not_, or_, select
from sqlalchemy.orm import Session

from .. import events, media, realtime
from ..collector.contract import AdRecord
from ..collector.factory import PUSH_SOURCES, ConnectorFactory
from ..db import get_db, ingest_lock
from ..ingest import ingest_ads
from ..markets import REGION_LABEL, REGIONS, ad_scope_filter, product_in_scope, targets
from ..models import (Ad, AdSource, Advertiser, Connector, Creative, Event, Product, ProductAlias, SearchJob,
                      TrackedQuery, Vote)
from ..services import lexicon, naming, query_intent
from ..services.discovery import VOTE_REASONS, VOTE_VALUE, market_dna
from ..storage import BACKEND, DATA_DIR, store
from ..workspace.filters import ad_filter, has_ad_filter

router = APIRouter()
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")


def require_token(x_ingest_token: str | None = Header(None)):
    if not INGEST_TOKEN:
        raise HTTPException(503, "INGEST_TOKEN not configured")
    if x_ingest_token != INGEST_TOKEN:
        raise HTTPException(401, "invalid ingest token (header X-Ingest-Token)")


_cache: dict = {}


events.listeners.append(lambda t: _cache.clear() if t in ("SEARCH_DONE", "CONNECTOR_SYNCED") else None)


def cached(key: tuple, fn, ttl: int = 60):
    """Memoise a slow read for `ttl` seconds. # ponytail: process-local cache, swap for Redis if multi-process"""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (time.time(), val := fn())
    return val


# ============================================================ helpers
def media_url(key: str | None, download_name: str | None = None) -> str | None:
    if not key:
        return None
    if BACKEND in ("r2", "s3"):
        return store().public_url(key, download_name)
    return f"/api/media/{key}" + (f"?dl={download_name}" if download_name else "")


source_alive = media.source_alive


def creative_dict(c: Creative, ad: Ad | None = None, adv_name: str | None = None) -> dict:
    ext = ".mp4" if c.type == "video" else (Path(c.storage_key or "").suffix or ".jpg")
    name = re.sub(r"[^\w.-]+", "_", f"{adv_name or c.source or 'creative'}_{c.source_ad_id or c.id}_{c.position}")[:80] + ext
    return {
        "id": c.id, "type": c.type, "status": c.status, "error": c.error, "product_id": c.product_id, "ad_id": c.ad_id,
        "url": media_url(c.storage_key) if c.status == "stored" else None,
        # saved ≤480 px JPEG; not grabbed yet → /thumb fetches + saves it on first view (fast, no hotlink / expiry issues)
        "thumb": media_url(c.thumb_key) if c.thumb_key else (
            f"/api/creatives/{c.id}/thumb" if source_alive(c.preview_source_url if c.type == "video" else c.source_url) else None),
        "download": f"/api/creatives/{c.id}/download" if c.status == "stored" else None,
        "source": c.source, "source_platform": c.source_platform, "source_ad_id": c.source_ad_id,
        "width": c.width, "height": c.height, "duration": c.duration_sec, "size": c.size_bytes, "family_id": c.family_id,
        # not in our vault: the player streams the source on Play — light (~360p) rendition first, never re-encoded
        "stream_url": next((u for u in (c.sd_source_url, c.source_url) if source_alive(u)), None) if c.status != "stored" else None,
        "origin_url": c.source_url,  # original link as collected (copy / open / download in the browser); may have expired
        "origin_alive": source_alive(c.source_url),
        "pinned": bool(c.pinned), "archived": c.status == "archived", "sha256": c.sha256, "first_seen_at": c.first_seen_at, "collected_at": c.collected_at, "file_name": name,
        **({"ad": ad_brief(ad, adv_name)} if ad else {}),
    }


def ad_markets_(a: Ad) -> list[str]:
    from ..models import ad_markets

    return ad_markets(a)


def ad_brief(a: Ad, adv_name: str | None = None) -> dict:
    from ..services.adsignals import days_running

    days = days_running(a) if a.first_seen_at else None
    return {"id": a.id, "advertiser": adv_name, "text": a.ad_text, "title": a.title, "cta": a.cta_text or a.cta_type,
            "funnel": a.funnel, "landing_url": a.landing_url, "country": a.country, "platform": a.platform,
            "first_seen": a.first_seen_at, "last_seen": a.last_seen_at, "days_running": days, "active": a.is_active,
            "variants": a.variants, "hook": a.hook, "angle": a.angle, "offer": a.offer, "snapshot_url": a.snapshot_url,
            "source": a.source, "force_score": a.force_score, "force_tier": a.force_tier, "last_verified": a.last_verified_at,
            "inactive_at": a.inactive_at, "reactivated_at": a.reactivated_at, "impressions_text": a.impressions_text,
            "spend_text": a.spend_text, "reach": a.reach, "markets": ad_markets_(a),
            # None = the source never reported it → the card shows N/A (never 0). Post-scope numbers: GET /api/engagement.
            "engagement": {"likes": a.likes, "comments": a.comments_count, "shares": a.shares, "views": a.views, "scope": "ad"}}


def product_card(p: Product, cover: Creative | None = None) -> dict:
    from ..services.engine import is_hidden_winner
    f = p.features or {}
    v = (p.potential or {}).get("vector", {})
    return {
        "id": p.id, "product_code": p.product_code, **naming.card_fields(p), "category": p.category, "country": p.country,
        "markets": f.get("markets", []), "price": p.price if p.price and p.currency else f.get("avg_price"),
        "currency": p.currency if p.price and p.currency else "USD" if f.get("avg_price") else None,
        "recommendation": p.recommendation, "reasons": p.recommendation_reasons, "classification": p.classification,
        "hidden_winner": is_hidden_winner(p),
        "lifecycle": p.lifecycle_status, "opportunity": v.get("opportunity", p.opportunity_score),
        "vector": v, "saturation_score": p.saturation_score, "confidence_score": p.confidence_score,
        "advertisers": f.get("advertiser_count", 0), "active_ads": f.get("active_ads", 0), "unique_ads": f.get("unique_ads", f.get("active_ads", 0)), "total_ads": f.get("total_ads", 0),
        "new_ads_7d": f.get("new_ads_7d", 0), "growth_7d": f.get("creative_growth_7d", 0),
        "funnel_mix": p.funnel_mix or {}, "first_seen_at": p.first_seen_at, "last_seen_at": p.last_seen_at,
        "cover": creative_dict(cover) if cover else None, **platform_signals(f),
    }


def platform_signals(f: dict) -> dict:
    """Cross-platform evidence for a product card: which networks it runs on + marketplace / viral numbers."""
    return {"networks": f.get("networks") or (["meta"] if f.get("total_ads") else []), "listings": f.get("listings", 0),
            "reviews_total": f.get("reviews_total", 0), "sold_total": f.get("sold_total", 0),
            "rating_avg": f.get("rating_avg"), "supplier_price_usd": f.get("supplier_price_usd"),
            "organic_views": f.get("organic_views", 0)}


def landing_links(db: Session, products: list[Product]) -> dict[int, str]:
    """Product → its landing page: a running Ladi ad's link first, then any active ad, newest first."""
    ids = [p.id for p in products]
    if not ids:
        return {}
    best: dict[int, tuple] = {}
    for pid, url, funnel, active, aid in db.execute(select(Ad.product_id, Ad.landing_url, Ad.funnel, Ad.is_active, Ad.id)
                                                    .where(Ad.product_id.in_(ids), ad_filter(db), Ad.landing_url.is_not(None), Ad.landing_url != "")):
        rank = (funnel == "ladi", bool(active), aid)
        if pid not in best or rank > best[pid][0]:
            best[pid] = (rank, url)
    return {pid: v[1] for pid, v in best.items()}


def top_advertisers(db: Session, pids: list[int]) -> dict[int, str]:
    """Product → the advertiser running most of its ads (shown next to the name, never as it)."""
    if not pids:
        return {}
    out: dict[int, tuple[int, str]] = {}
    for pid, name, n in db.execute(select(Ad.product_id, Advertiser.name, func.count(Ad.id)).join(Advertiser, Advertiser.id == Ad.advertiser_id)
                                   .where(Ad.product_id.in_(pids), ad_filter(db)).group_by(Ad.product_id, Advertiser.name)):
        if pid not in out or n > out[pid][0]:
            out[pid] = (n, name)
    return {pid: v[1] for pid, v in out.items()}


def covers(db: Session, products: list[Product], nets: list[str] | None = None) -> dict[int, Creative]:
    """Product → card media. `nets` (source filter) = only creatives from those networks (NULL network = old Meta rows)."""
    allowed_ads = select(Ad.id).where(ad_filter(db))
    in_nets = (lambda c: c.network in nets or (c.network is None and "meta" in nets)) if nets else (lambda c: True)
    ids = [p.cover_creative_id for p in products if p.cover_creative_id]
    out = {c.id: c for c in db.scalars(select(Creative).where(Creative.ad_id.in_(allowed_ads), Creative.id.in_(ids)))} if ids else {}
    res = {p.id: out.get(p.cover_creative_id) for p in products}
    # the stored cover can be archived / expired later, or be an image while a video still plays from its source
    bad = [pid for pid, c in res.items() if c is None or c.status != "stored" or c.type != "video" or not in_nets(c)]
    if bad:
        best: dict[int, Creative] = {}
        for c in db.scalars(select(Creative).where(Creative.ad_id.in_(allowed_ads), Creative.product_id.in_(bad), Creative.status == "stored")
                            .order_by(Creative.type.desc(), Creative.id)):  # "video" sorts before "image" when descending
            if in_nets(c):
                best.setdefault(c.product_id, c)
        still = [pid for pid in bad if pid not in best or best[pid].type != "video"]
        dead: dict[int, Creative] = {}  # last resort: a video whose link expired — the card says so + links the ad page
        if still:  # nothing stored (or only images; videos aren't downloaded by default) → a creative whose original link still works
            for c in db.scalars(select(Creative).where(Creative.ad_id.in_(allowed_ads), Creative.product_id.in_(still), Creative.status.in_(["pending", "skipped", "failed", "archived", "expired"]))
                                .order_by(Creative.type.desc(), Creative.id.desc())):
                cur = best.get(c.product_id)
                if (cur is None or (cur.type != "video" and c.type == "video")) and in_nets(c) and source_alive(c.source_url):
                    best[c.product_id] = c
                elif c.type == "video" and in_nets(c):
                    dead.setdefault(c.product_id, c)
        for pid in bad:
            if pid in best:
                res[pid] = best[pid]
            elif res[pid] is None or not in_nets(res[pid]):
                res[pid] = dead.get(pid)  # never another network's media under a source filter
    # a card must never be an empty "media pending / link expired" box while the product HAS a saved thumbnail: the picked
    # cover has none and its source is gone → another creative of the same product that does (videos first)
    blank = [pid for pid, c in res.items() if c is None or (not c.thumb_key and c.status != "stored" and not source_alive(
        c.preview_source_url if c.type == "video" else c.source_url))]
    if blank:
        best_thumb: dict[int, Creative] = {}
        for c in db.scalars(select(Creative).where(Creative.ad_id.in_(allowed_ads), Creative.product_id.in_(blank), Creative.thumb_key.is_not(None))
                            .order_by(Creative.type.desc(), Creative.id.desc())):
            if in_nets(c):
                best_thumb.setdefault(c.product_id, c)
        for pid, c in best_thumb.items():
            res[pid] = c
    return res


# ============================================================ Realtime: SSE + event feed
@router.get("/api/events/stream")
async def stream(request: Request):
    if os.getenv("DISABLE_SSE"):
        from fastapi import Response

        return Response(status_code=204)
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                    if getattr(request.state, "user", {}).get("role") == "MKT":
                        # Global bus is shared: never disclose another user's queries, votes, or activity.
                        if payload.get("type") not in ("CREATIVE_STORED", "PRODUCT_SCORED", "AD_ADDED"):
                            continue
                        payload = {**payload, "data": {k: v for k, v in (payload.get("data") or {}).items() if k in ("ad_id", "creative_id", "product_id")}}
                    yield events.sse_format(payload)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/events")
def recent_events(limit: int = Query(50, le=500), type: str | None = None, db: Session = Depends(get_db)):
    if db.info.get("role") == "MKT":
        return []  # personal activity lives at /workspace/activity
    q = select(Event).order_by(Event.id.desc()).limit(limit)
    if type:
        q = q.where(Event.type == type)
    return [{"id": e.id, "type": e.type, "product_id": e.product_id, "data": e.data, "at": e.created_at} for e in db.scalars(q)]


# ============================================================ Ingest API (Unified Collector, §9) — token protected
class IngestAds(BaseModel):
    records: list[dict]
    saved_by: str | None = None


@router.post("/api/ingest/ads", dependencies=[Depends(require_token)])
def ingest_ads_api(body: IngestAds, db: Session = Depends(get_db)):
    recs = []
    for r in body.records:
        try:
            recs.append(AdRecord.from_dict(r))
        except TypeError as e:
            raise HTTPException(422, f"record không đúng Common Data Contract: {e}")
    with ingest_lock():  # serialize writers (see realtime.py)
        stats = ingest_ads(db, recs, saved_by=body.saved_by)
    realtime.after_business_events(db, set(stats["product_ids"]))
    return stats


class IngestProduct(BaseModel):
    name: str
    url: str | None = None
    country: str | None = None
    price: float | None = None
    currency: str | None = None
    video_urls: list[str] = []
    image_urls: list[str] = []
    note: str | None = None
    saved_by: str | None = None
    advertiser: str | None = None


@router.post("/api/ingest/products", dependencies=[Depends(require_token)])
def ingest_product(body: IngestProduct, db: Session = Depends(get_db)):
    """Manual / extension 'Save product': becomes one AdRecord from source 'manual'."""
    import hashlib

    rec = AdRecord(
        source="manual", source_ad_id=hashlib.sha1(f"{body.url}|{body.name}".encode()).hexdigest()[:16],
        country=(body.country or "").upper() or None, advertiser=body.advertiser, ad_text=body.note, title=body.name,
        product_name=body.name, landing_page=body.url, product_url=body.url, price=body.price, currency=body.currency,
        first_seen=datetime.utcnow().isoformat(), active=True, saved_by=body.saved_by,
        media=[{"type": "video", "url": u} for u in body.video_urls] + [{"type": "image", "url": u} for u in body.image_urls],  # type: ignore
    )
    rec = AdRecord.from_dict(rec.to_dict())
    with ingest_lock():
        stats = ingest_ads(db, [rec], saved_by=body.saved_by)
    realtime.after_business_events(db, set(stats["product_ids"]))
    return stats


class IngestCreative(BaseModel):
    source: str
    source_ad_id: str
    type: str = "video"
    url: str
    preview_url: str | None = None


@router.post("/api/ingest/creatives", dependencies=[Depends(require_token)])
def ingest_creative(body: IngestCreative, db: Session = Depends(get_db)):
    prov = db.scalar(select(AdSource).where(AdSource.source == body.source, AdSource.source_ad_id == body.source_ad_id))
    if not prov:
        raise HTTPException(404, "ad not found — POST /api/ingest/ads first")
    ad = db.get(Ad, prov.ad_id)
    c = Creative(ad_id=ad.id, product_id=ad.product_id, type=body.type, source=body.source, source_ad_id=body.source_ad_id,
                 source_url=body.url, preview_source_url=body.preview_url, first_seen_at=ad.first_seen_at)
    db.add(c)
    db.commit()
    media.enqueue([c.id])
    return {"creative_id": c.id}


# ============================================================ Product Search (main task #1)
SORTS = {
    "opportunity": lambda c: c["opportunity"] or 0, "newest": lambda c: c["first_seen_at"] or datetime.min,
    "wave": lambda c: c["vector"].get("wave_potential") or 0, "novelty": lambda c: c["vector"].get("novelty") or 0,
    "creative": lambda c: c["vector"].get("creative_potential") or 0, "demand": lambda c: c["vector"].get("market_demand") or 0,
    "ads": lambda c: c["active_ads"], "growth": lambda c: c["growth_7d"] or 0,
}


def split_keywords(s: str | None) -> list[str]:
    """'fengshui | lucky bracelet, pixiu' → ['fengshui', 'lucky bracelet', 'pixiu'] (lower-cased, deduped)."""
    return list(dict.fromkeys(k.strip().lower() for k in re.split(r"[|,\n]", s or "") if k.strip()))


def _ad_has(term: str):
    return or_(func.coalesce(Ad.search_text, "").contains(term), func.coalesce(Ad.raw_product_name, "").ilike(f"%{term}%"),
               func.coalesce(Ad.ad_text, "").ilike(f"%{term}%"))


def _variant_has(term: str):
    """Expanded term, on search_text only (it already holds name + title + copy + advertiser + link, lower-cased: one
    column instead of three keeps 40 variants cheap). Short Latin words must start a word ("ring" ≠ "spring")."""
    if term.isascii() and len(term) <= 4:
        return (literal(" ") + func.coalesce(Ad.search_text, "")).contains(" " + term)
    return func.coalesce(Ad.search_text, "").contains(term)


_VI_TONE = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.I)


def _exact(kw: str):
    """Words of a keyword anywhere (AND) — except Vietnamese, whose words are syllables: "nước hoa" (perfume) is not
    "nước" + "hoa" anywhere in the copy. (English stays words-anywhere: "whiten your teeth" is a teeth-whitening ad.)"""
    if len(kw.split()) > 1 and _VI_TONE.search(kw):
        return _ad_has(kw)
    return and_(*[_ad_has(t) for t in kw.split()])


TIERS = ("exact", "synonym", "related")
RELATED_MIN = 30  # related concepts join only when exact + synonym found fewer products / ads than this (limited expansion)


def _like(col, v: str):
    """col holds v — short Latin words only at a word start ("ring" ≠ "Spring")."""
    if v.isascii() and len(v) <= 4:
        return (literal(" ") + func.lower(func.coalesce(col, ""))).contains(" " + v)
    return col.ilike(f"%{v}%")


def _name_has(variants: list[str]):
    """Product side of a synonym group: canonical / Vietnamese name or any alias (original names) holds a variant."""
    return or_(*[or_(_like(Product.canonical_name, v), _like(Product.name_vi, v)) for v in variants],
               Product.id.in_(select(ProductAlias.product_id).where(or_(*[_like(ProductAlias.name, v) for v in variants]))))


def keyword_tiers(kw: str, e: dict | None = None) -> list:
    """Ad filters for one keyword, best first: exact words (AND) → same concept in any language (lexicon.py: vi / en /
    tl / ar / zh) → related concepts ("vòng tay phong thủy" → lucky / pixiu bracelets). Semantic (text embeddings) is not
    a tier: the image model has no text tower — use image search for look-alikes."""
    exact = _exact(kw)
    e = e or lexicon.expand(kw)   # callers pass query_intent.expand(db, kw): lexicon, else the cached model meaning of the keyword
    if not e["groups"]:
        return [exact]
    rest = [_ad_has(w) for w in e["rest"] if len(w) > 2]
    avoid = [or_(*[_variant_has(v) for v in a]) for a in e.get("avoid") or [] if a]   # "sunglasses" ads are not "eyeglasses" ads
    syn = and_(*[or_(*[_variant_has(v) for v in g]) for g in e["groups"]], *rest, *[not_(a) for a in avoid])
    rel = and_(*[or_(*[_variant_has(v) for v in g]) for g in e["related"]], *rest)
    return [exact, syn, rel]


def _search_all(q: str | None = None, country: str | None = None, funnel: str | None = None, platform: str | None = None,
           media_type: str | None = None, category: str | None = None, min_days: int | None = None,
           max_advertisers: int | None = None, decision: str | None = None, quadrant: str | None = None,
           active_only: bool = True, has_video: bool = False, sort: str = "opportunity", limit: int = 60,
           offset: int = 0, ids: str | None = None, network: str | None = None, min_networks: int | None = None,
           exclude: str | None = None, name_status: str | None = None, expand: bool = True, relevant: bool = True,
           only_off: bool = False, mode: str | None = None, language: str | None = None, similar_to: int | None = None,
           min_fit: float | None = None, min_demand: float | None = None, min_engagement: float | None = None,
           db: Session = Depends(get_db)):
    """Search products by keyword over product names, aliases, ad copy, titles, advertisers and landing pages.
    `q` = several keywords split by | , or newline: OR between keywords, AND between the words of one keyword.
    `exclude` = keywords (same separators) whose ads / products are dropped.
    `ids` (comma list, e.g. a live-search job's products) restricts to those products and skips market scope. The keyword still
    applies (exact + same-meaning in any language): a source's fuzzy hits ("kính mắt" → window-glass ads) are dropped and counted in
    `off_topic`; `relevant=false` shows them all.
    `only_off=true` (with `ids`): the opposite list — just the products `relevant` hides, paged like any other list.
    `expand` (default on): a keyword also matches its translations / synonyms / related concepts, ranked below exact hits.
    `name_status` = UNKNOWN / SUGGESTED / AUTO_CONFIRMED / VERIFIED (comma list): the "Chưa xác định" queue for MKT.
    Personalized (Phase 7, app/personal_search.py): `mode` = for_me | claimable | followed | similar (`similar_to` = seed
    product; empty = similar to the MKT's history). Every mode is an exclusive-opportunity view: products another MKT
    owns are never listed. `language` (ad language), `min_fit` (personal fit 0–100), `min_demand` (market demand 0–1),
    `min_engagement` (only products with real likes/comments/shares data, engagement per ad ≥ value), sort=fit.
    Search never claims anything."""
    from .. import personal_search as PS
    uid = db.info.get("user_id")
    if (mode or min_fit is not None or sort == "fit") and not uid:
        raise HTTPException(401, "Chế độ cá nhân hóa cần đăng nhập")
    personal = PS.candidates(db, uid, mode, similar_to) if mode else None
    aq = select(Ad.product_id).where(Ad.product_id.is_not(None), ad_filter(db))
    if personal is not None and personal.restrict is not None:
        aq = aq.where(Ad.product_id.in_(personal.restrict or [0]))
    if personal is not None and personal.advertisers is not None:
        aq = aq.where(Ad.advertiser_id.in_(personal.advertisers or [0]))
    if language:
        aq = aq.where(func.lower(func.coalesce(Ad.language, "")).like(f"{language.lower()[:8]}%"))
    id_list = [int(i) for i in (ids or "").split(",") if i.strip().isdigit()]
    if only_off and id_list and split_keywords(q):  # ponytail: second scan of the job's ids, only paid when the user opens the off-topic section
        ok = {r["id"] for r in search(q=q, ids=ids, expand=expand, relevant=True, limit=200, db=db)["rows"]}
        id_list, relevant, q = [i for i in id_list if i not in ok], False, None
        if not id_list:
            return {"cards": [], "products": [], "cov": {}, "nets": [], "has_video": has_video,
                    "meta": {"funnels": {}, "keywords": [], "exclude": [], "off_topic": 0, "understood": {}}}
    if id_list:  # a live-search job's products: no market scope / active filter — foreign-market results must show
        aq, country = aq.where(Ad.product_id.in_(id_list)), None
        if not relevant:
            q = None
    kws, exs = split_keywords(q), split_keywords(exclude)
    for x in exs:
        aq = aq.where(not_(_ad_has(x)))
    _f = None if id_list else ad_scope_filter(country)
    if _f is not None:
        aq = aq.where(_f)
    if funnel:
        aq = aq.where(Ad.funnel == funnel)
    if platform:
        aq = aq.where(Ad.platform == platform)
    nets = [n for n in (network or "").split(",") if n]  # source filter: only products with an ad from these networks
    if nets:
        aq = aq.where(or_(Ad.network.in_(nets), Ad.network.is_(None)) if "meta" in nets else Ad.network.in_(nets))  # NULL = pre-network Meta rows
    if media_type:
        aq = aq.where(Ad.media_type == media_type)
    if active_only and not id_list:
        aq = aq.where(Ad.is_active.is_(True))
    exp = {kw: query_intent.expand(db, kw) for kw in kws} if expand else {}
    tiers = {kw: keyword_tiers(kw, exp[kw]) if expand else [_exact(kw)] for kw in kws}
    if kws:  # live-job products too: only ads whose own text / name says the keyword (or what it means); a source's fuzzy hit is off-topic
        aq = aq.where(or_(*[c for kw in kws for c in tiers[kw]]))  # OR between keywords, AND between the words of one keyword
    # one scan: every matched ad with, per keyword, the best tier it reached (0 exact · 1 synonym · 2 related · NULL)
    cols = [case(*[(t, i) for i, t in enumerate(tiers[kw])], else_=None) for kw in kws]
    span: dict[int, list] = {}
    kw_tier: dict[int, dict[str, int]] = {}
    funnels: Counter = Counter()  # ponytail: funnel facet counts every matched ad; min_days only trims the product list
    for pid, first, last, fn, *ts in db.execute(aq.with_only_columns(Ad.product_id, Ad.first_seen_at, Ad.last_seen_at, Ad.funnel, *cols)):
        sp = span.setdefault(pid, [0, first, last])
        sp[0] += 1
        sp[1], sp[2] = min(sp[1] or first, first or sp[1]), max(sp[2] or last, last or sp[2])
        funnels[fn] += 1
        for kw, t in zip(kws, ts):
            if t is not None and t < kw_tier.setdefault(pid, {}).get(kw, 9):
                kw_tier[pid][kw] = t
    # product → matched ads (seeded so id products with no ad still show — not under a source filter)
    filtered_advertisers = has_ad_filter(db)
    matches: dict[int, int] = dict.fromkeys([] if nets or kws or filtered_advertisers else id_list, 0)
    now = datetime.utcnow()
    for pid, (n, first, last) in span.items():
        if min_days and first and ((last or now) - first).days < min_days:  # longest span of the product's matched ads
            continue
        matches[pid] = n
    named = or_(Product.name_status.is_(None), Product.name_status != "UNKNOWN")
    for kw in kws:  # product names, Vietnamese names and aliases: the keyword itself = exact, a translation = synonym
        hits = [(0, _name_has([kw]))]
        if expand and (e := exp[kw])["groups"]:
            hits.append((1, and_(*[_name_has(g) for g in e["groups"]])))
        for tier, cond in hits:
            visible = [Product.id.in_(select(Ad.product_id).where(ad_filter(db)))] if filtered_advertisers else []
            for p in db.scalars(select(Product).where(cond, named, *visible, *([Product.id.in_(id_list)] if id_list else []))):
                # an ad-level filter (source, language, mode's advertisers) must hold for name hits too
                ad_level = nets or language or (personal is not None and personal.advertisers is not None)
                if product_in_scope(p, country) and (not ad_level or p.id in matches):
                    matches.setdefault(p.id, 0)
                    t = kw_tier.setdefault(p.id, {})
                    t[kw] = min(t.get(kw, 9), tier)
    if kws and sum(1 for pid in matches if min(kw_tier.get(pid, {}).values(), default=2) <= 1) >= RELATED_MIN:
        matches = {pid: n for pid, n in matches.items() if min(kw_tier.get(pid, {}).values(), default=2) <= 1}
    off_topic = len(set(id_list) - set(matches)) if id_list and kws else 0
    products = list(db.scalars(select(Product).where(Product.id.in_(list(matches)))))
    if exs:
        products = [p for p in products if not any(x in (p.canonical_name or "").lower() for x in exs)]
    products = [p for p in products if p.category == category] if category else [p for p in products if p.category != "non_product"]
    if max_advertisers is not None:
        products = [p for p in products if (p.features or {}).get("advertiser_count", 0) <= max_advertisers]
    if decision:
        products = [p for p in products if p.recommendation == decision]
    if quadrant:
        products = [p for p in products if p.classification == quadrant]
    if name_status:
        want = set(name_status.upper().split(","))
        products = [p for p in products if (p.name_status or "SUGGESTED") in want]
    if min_networks:  # cross-platform validated: seen on ≥ N networks (ads, marketplaces, viral)
        products = [p for p in products if len((p.features or {}).get("networks") or []) >= min_networks]
    cov = covers(db, products, nets) if has_video else {}  # every product's cover only when filtering on it: ~100k rows
    if has_video:
        products = [p for p in products if cov.get(p.id) and cov[p.id].type == "video"]
    if personal is not None and personal.restrict is not None:  # name matches bypass the ad scan: keep the mode's set
        allowed = set(personal.restrict)
        products = [p for p in products if p.id in allowed]
    products = PS.filter_signals(db, products, min_demand, min_engagement)
    fit, alloc, extra = PS.annotate(db, uid, products, personal, need_fit=bool(mode or min_fit is not None or sort == "fit"))
    if mode:  # exclusive-opportunity views: someone else's product is not an opportunity
        products = [p for p in products if alloc.get(p.id) != "taken" and (mode != "claimable" or alloc.get(p.id) == "available")]
    if min_fit is not None:
        products = [p for p in products if fit.get(p.id, {}).get("score", 0) >= min_fit]
    hit = lambda pid: kw_tier.get(pid, {})
    cards = [product_card(p, cov.get(p.id)) | {"matched_ads": matches.get(p.id, 0), "matched_keywords": [k for k in kws if k in hit(p.id)],
                                               "match_tier": TIERS[min(hit(p.id).values(), default=2)] if kws else None,
                                               "fit": fit.get(p.id), "allocation": alloc.get(p.id), **extra.get(p.id, {})}
             for p in products]
    key = (lambda c: (c["fit"] or {}).get("score", -1)) if sort == "fit" else SORTS.get(sort, SORTS["opportunity"])
    cards.sort(key=key, reverse=True)
    if kws:  # relevance (stable sort, the chosen sort inside): keywords hit by exact / synonym — a loose related match
        # ("money back" ~ wealth) must not outrank a real one — then the best tier, identified products before unknown
        strong = lambda c: sum(1 for t in hit(c["id"]).values() if t <= 1)
        cards.sort(key=lambda c: (-strong(c), TIERS.index(c["match_tier"]), c["name_status"] == "UNKNOWN"))
    return {"cards": cards, "products": products, "cov": cov, "nets": nets, "has_video": has_video,
            "meta": {"funnels": {k: v for k, v in funnels.items() if k and v}, "keywords": kws, "exclude": exs,
                     "off_topic": off_topic, "mode": mode, "mode_info": personal.info if personal is not None else None,
                     "understood": {kw: {"source": e["source"], "variants": (e["groups"][0] if e["groups"] else [])[:12]}
                                    for kw, e in exp.items()}}}


_SEARCH_CACHE: dict = {}  # key → (time, full sorted result); pages of one query ("Tải thêm") skip the 1–4 s scan
SEARCH_CACHE_S = 60


def _cache_key(db, params):
    """User, every filter except paging, and what can change the answer for this user: claims / releases (allocation
    audit), the MKT's advertiser filters, and — for personal views — the profile / seeds / follows signature."""
    from sqlalchemy import func as F

    from ..allocation import AllocationAudit
    from ..workspace.models import AdvertiserFilter, Profile
    uid = db.info.get("user_id")
    alloc_v = db.scalar(select(F.max(AllocationAudit.id))) or 0
    from ..workspace.models import AdInteraction
    interest_v = tuple(db.execute(select(F.count(), F.max(AdInteraction.updated_at))).one())  # soft-dedup signals
    filt = None
    if uid:
        prof = db.get(Profile, uid)
        filt = (prof.advertiser_mode if prof else "all",
                tuple(db.execute(select(AdvertiserFilter.advertiser_id, AdvertiserFilter.mode).where(AdvertiserFilter.user_id == uid)
                                 .order_by(AdvertiserFilter.advertiser_id)).all()))
    sig = None
    if uid and (params["mode"] or params["min_fit"] is not None or params["sort"] == "fit"):
        from .. import recommend as R
        sig = R.context(db, uid)["signature"]
    return (uid, tuple(sorted(params.items())), alloc_v, interest_v, filt, sig)


@router.get("/api/search")
def search(q: str | None = None, country: str | None = None, funnel: str | None = None, platform: str | None = None,
           media_type: str | None = None, category: str | None = None, min_days: int | None = None,
           max_advertisers: int | None = None, decision: str | None = None, quadrant: str | None = None,
           active_only: bool = True, has_video: bool = False, sort: str = "opportunity", limit: int = Query(60, le=200),
           offset: int = 0, ids: str | None = None, network: str | None = None, min_networks: int | None = None,
           exclude: str | None = None, name_status: str | None = None, expand: bool = True, relevant: bool = True,
           only_off: bool = False, mode: str | None = None, language: str | None = None, similar_to: int | None = None,
           min_fit: float | None = None, min_demand: float | None = None, min_engagement: float | None = None,
           db: Session = Depends(get_db)):
    """Search products by keyword / filters / personal mode — see _search_all. The full sorted list is cached per user and
    filters for 60 s, so paging only renders the page. A live-search job's id list (`ids`) is never cached: it changes
    while the job runs."""
    params = dict(q=q, country=country, funnel=funnel, platform=platform, media_type=media_type, category=category,
                  min_days=min_days, max_advertisers=max_advertisers, decision=decision, quadrant=quadrant,
                  active_only=active_only, has_video=has_video, sort=sort, ids=ids, network=network, min_networks=min_networks,
                  exclude=exclude, name_status=name_status, expand=expand, relevant=relevant, only_off=only_off, mode=mode,
                  language=language, similar_to=similar_to, min_fit=min_fit, min_demand=min_demand, min_engagement=min_engagement)
    limit = limit if isinstance(limit, int) else 60  # direct (non-HTTP) callers may pass nothing
    key = None if ids else _cache_key(db, params)
    hit = _SEARCH_CACHE.get(key) if key else None
    if hit and time.time() - hit[0] < SEARCH_CACHE_S:
        res = hit[1]
    else:
        res = _search_all(db=db, **params)
        if key:
            if len(_SEARCH_CACHE) > 64:  # ponytail: tiny per-process cache, oldest out; Redis if several API processes need to share it
                _SEARCH_CACHE.pop(min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0]))
            _SEARCH_CACHE[key] = (time.time(), res)
    cards, products, cov, nets, has_video = res["cards"], res["products"], res["cov"], res["nets"], res["has_video"]
    cards = [dict(c) for c in cards[offset: offset + limit]]  # page rows are decorated below: never mutate the cache
    page = cards
    page_products = [p for p in products if p.id in {c["id"] for c in page}]
    if not has_video:  # covers for the shown page only (sorting never reads them)
        cov = covers(db, page_products, nets)
        for c in page:
            c["cover"] = creative_dict(cov[c["id"]]) if cov.get(c["id"]) else None
    lp = landing_links(db, page_products)
    top_adv = top_advertisers(db, [c["id"] for c in page])
    advertiser_ids = {}
    for pid, aid in db.execute(select(Ad.product_id, Ad.advertiser_id).where(Ad.product_id.in_([c["id"] for c in page]),
                              Ad.advertiser_id.is_not(None), ad_filter(db)).order_by(Ad.id.desc())):
        advertiser_ids.setdefault(pid, aid)
    names = dict(db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_(advertiser_ids.values()))).all())
    aids = [c["cover"]["ad_id"] for c in page if c["cover"] and c["cover"]["ad_id"]]
    snaps = dict(db.execute(select(Ad.id, Ad.snapshot_url).where(Ad.id.in_(aids))).all()) if aids else {}
    for c in page:
        c["landing_url"] = lp.get(c["id"])
        c["advertiser_id"] = advertiser_ids.get(c["id"])
        c["advertiser"] = names.get(c["advertiser_id"]) or top_adv.get(c["id"])
        if c["cover"]:  # the ad's own page (Ad Library / TikTok Creative Center): where to watch once the CDN link died
            c["cover"]["ad"] = {"snapshot_url": snaps.get(c["cover"]["ad_id"])}
    return {"total": len(res["cards"]), "rows": page, **res["meta"]}


class LiveSearch(BaseModel):
    query: str
    countries: list[str] = ["ALL"]
    adapters: list[str] = []
    paid: bool = False  # false (default) = free sources only; true = Apify actors too when `adapters` is empty
    user: str | None = None
    track: bool = False  # also save as a tracked query (scheduler re-runs it)
    every_minutes: int = 60
    limit: int = 50  # ads per country for one results page (≈10 per source page); next page = POST /jobs/{id}/more
    media_type: str | None = None  # video | image
    reuse_hours: int = 0  # > 0: the same query + sources still running or done within N hours → return that job, no new crawl
    expand: bool = False  # also crawl the English source keywords of a vi / tl / ar / zh query (≤ 2 extra per keyword)


@router.post("/api/search/live")
def live_search(body: LiveSearch, db: Session = Depends(get_db)):
    """Search the sources right now (Meta Ad Library, Apify, providers…). Progress via SSE SEARCH_PROGRESS/SEARCH_DONE."""
    if db.info.get("user_id"):
        from ..workspace.auth import rate_limit
        rate_limit(f"live-search:{db.info['user_id']}", 30, 3600)
        body.user = str(db.info["user_id"])
        if body.track:  # system-wide crawl schedules are internal (service token / CLI), never per account
            raise HTTPException(403, "Lịch crawl từ khóa toàn hệ thống do dịch vụ nội bộ quản lý; dùng lưu bộ lọc hoặc Follow đối thủ")
    countries = [c.strip().upper() for c in body.countries if c.strip()][:10] or ["ALL"]
    kws = split_keywords(body.query)
    if body.expand:  # opt-in: each extra keyword is another scan of every (paid / rate-limited) source
        kws = list(dict.fromkeys(kws + [x for k in kws for x in query_intent.source_queries(db, k, countries, 2)]))
    kws = kws[:10]  # ponytail: hard cap — every keyword is a full scan of every source
    if not kws:
        raise HTTPException(400, "nhập ít nhất 1 từ khoá")
    adapters = body.adapters
    if not adapters and not body.paid:  # [] means "every source" downstream → pin the free search connectors explicitly
        adapters = [c.adapter for c in db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.adapter.is_not(None)))
                    if (cls := ConnectorFactory.connectors.get(c.adapter)) and cls.supports_search
                    and (not c.adapter.startswith("apify") or c.adapter == "apify_tiktok_top_ads")  # Apify Top Ads = the PH/SA/AE TikTok source, always on (budget-capped)
                    and (c.adapter != "ali1688" or (c.config or {}).get("ak") or os.getenv("ALI_1688_AK"))]  # no AK → skip, not an error per search
        if not adapters:
            raise HTTPException(400, "không có nguồn miễn phí nào được bật — chọn adapters hoặc paid=true")
    if body.reuse_hours > 0 and not body.track:  # TTL + job dedupe (1688 "Tìm mới"): don't re-crawl / re-bill a search we already have
        since = datetime.utcnow() - timedelta(hours=body.reuse_hours)
        for old in db.scalars(select(SearchJob).where(SearchJob.query == " | ".join(kws), SearchJob.created_at >= since, SearchJob.user_id == db.info.get("user_id"))
                              .order_by(SearchJob.id.desc())):
            got = old.found + sum(s.get("listings", 0) for s in (old.sources or {}).values())
            same = (sorted(old.adapters or []) == sorted(adapters) and sorted(old.countries or []) == sorted(countries)
                    and old.media_type == (body.media_type or None) and old.limit >= max(10, min(body.limit, 1000)))
            if same and ((old.status == "running" and old.id in realtime.LIVE_JOBS) or (old.status == "done" and got)):
                return {"job_id": old.id, "reused": True}
    job = SearchJob(query=" | ".join(kws), countries=countries, adapters=adapters, created_by=body.user, user_id=db.info.get("user_id"),
                    limit=max(10, min(body.limit, 1000)), media_type=body.media_type or None)
    db.add(job)
    if body.track:
        for c in db.scalars(select(Connector).where(Connector.enabled.is_(True), Connector.adapter.is_not(None))):
            cls = ConnectorFactory.connectors.get(c.adapter)
            # paid Apify actors run on demand only: an hourly re-run per tracked keyword would drain the budget
            if cls and cls.supports_search and not c.adapter.startswith("apify") and (not body.adapters or c.adapter in body.adapters):
                for kw in kws:
                    db.add(TrackedQuery(connector_id=c.id, query=kw, countries=countries,
                                        every_minutes=max(15, body.every_minutes), created_by=body.user))
    db.commit()
    realtime.start_search(job.id)
    return {"job_id": job.id}


@router.get("/api/search/jobs/{jid}")
def search_job(jid: int, db: Session = Depends(get_db)):
    j = db.get(SearchJob, jid)
    if not j or (db.info.get("user_id") and j.user_id != db.info["user_id"]):
        raise HTTPException(404)
    if j.status == "running" and jid not in realtime.LIVE_JOBS:  # its thread died (server restart) — don't leave it running forever
        j.status, j.error, j.finished_at = "error", j.error or "Tìm kiếm bị ngắt (server khởi động lại) — chạy lại", datetime.utcnow()
        db.commit()
    return {"id": j.id, "query": j.query, "countries": j.countries, "status": j.status, "found": j.found, "new_ads": j.new_ads,
            "product_ids": j.product_ids, "error": j.error, "created_at": j.created_at, "finished_at": j.finished_at,
            "has_more": j.has_more, "limit": j.limit, "media_type": j.media_type, "sources": j.sources or {}}


class MoreIn(BaseModel):
    count: int = 30


@router.post("/api/search/jobs/{jid}/more")
def search_more(jid: int, body: MoreIn, db: Session = Depends(get_db)):
    """Next results page: continue the live search from its saved cursor, `count` more ads per country."""
    j = db.get(SearchJob, jid)
    if not j:
        raise HTTPException(404)
    search_job(jid, db)  # expires a dead "running" job first
    db.refresh(j)
    if j.status == "running":
        raise HTTPException(409, "tìm kiếm đang chạy")
    if not j.has_more:
        raise HTTPException(409, "nguồn đã hết kết quả")
    j.status = "running"
    db.commit()
    realtime.start_search(jid, max(10, min(body.count, 1000)))
    return {"ok": True}


@router.get("/api/search/jobs")
def search_jobs(db: Session = Depends(get_db)):
    return [search_job(j.id, db) for j in db.scalars(select(SearchJob).where(SearchJob.user_id == db.info.get("user_id")).order_by(SearchJob.id.desc()).limit(20))]


# ============================================================ Creative Vault
@router.get("/api/creatives")
def list_creatives(product_id: int | None = None, type: str | None = None, status: str | None = None, q: str | None = None,
                   country: str | None = None, funnel: str | None = None, family_id: int | None = None,
                   limit: int = Query(60, le=300), offset: int = 0, db: Session = Depends(get_db)):
    cq = select(Creative, Ad).join(Ad, Ad.id == Creative.ad_id, isouter=True).order_by(Creative.id.desc())
    if product_id:
        cq = cq.where(Creative.product_id == product_id)
    if type:
        cq = cq.where(Creative.type == type)
    if status:
        cq = cq.where(Creative.status == status)
    if family_id:
        cq = cq.where(Creative.family_id == family_id)
    _f = ad_scope_filter(country)
    if _f is not None:
        cq = cq.where(_f)
    if funnel:
        cq = cq.where(Ad.funnel == funnel)
    if q:
        cq = cq.where(Ad.search_text.contains(q.lower()))
    rows = db.execute(cq.offset(offset).limit(limit)).all()
    advs = {a.id: a.name for a in db.scalars(select(Advertiser).where(Advertiser.id.in_({ad.advertiser_id for _, ad in rows if ad})))}
    fam = dict(db.execute(select(Creative.family_id, func.count(Creative.id)).group_by(Creative.family_id)).all())
    stats = dict(db.execute(select(Creative.status, func.count(Creative.id)).group_by(Creative.status)).all())
    return {"rows": [creative_dict(c, ad, advs.get(ad.advertiser_id) if ad else None) | {"family_size": fam.get(c.family_id, 1)}
                     for c, ad in rows], "stats": stats}


@router.get("/api/creatives/{cid}/download")
def download_creative(cid: int, db: Session = Depends(get_db)):
    c = db.get(Creative, cid)
    if not c or c.status != "stored":
        raise HTTPException(404, "creative chưa được lưu")
    ad = db.get(Ad, c.ad_id) if c.ad_id else None
    adv = db.get(Advertiser, ad.advertiser_id) if ad and ad.advertiser_id else None
    name = creative_dict(c, None, adv.name if adv else None)["file_name"]
    key = c.storage_key
    if not store().exists(key):  # ponytail: GET stays read-only; retention flips the row to archived
        raise HTTPException(410, "File đã bị xoá khỏi kho")
    if BACKEND in ("r2", "s3"):
        return RedirectResponse(store().public_url(key, name))
    return FileResponse(store().path(key), media_type=c.mime or "video/mp4", filename=name)


@router.post("/api/creatives/{cid}/retry")
def retry_creative(cid: int, db: Session = Depends(get_db)):
    c = db.get(Creative, cid)
    if not c:
        raise HTTPException(404)
    if c.type == "video":  # videos are never kept in the vault: a dead signed link only needs a fresh one to stream
        if c.status != "stored" and not source_alive(c.source_url):
            try:  # ask TikTok again by ad id (~10 s)
                ok = media.refresh_source(c)
            except Exception as e:
                raise HTTPException(502, f"Không lấy được link mới: {e}"[:200])
            if not ok:
                raise HTTPException(410, "Nguồn không còn video này — mở trang quảng cáo gốc")
            c.status, c.attempts, c.error = "pending", 0, None
            db.commit()
        return creative_dict(c)
    c.status, c.attempts, c.error = "pending", 0, None
    db.commit()
    media.enqueue([cid], force=True)  # explicit request: save this one image
    return creative_dict(c)


@router.get("/api/creatives/{cid}/thumb")
def creative_thumb(cid: int):
    """Thumbnail on first view: fetch the source once (server side — no hotlink block, right Referer), save ≤480 px
    JPEG, serve it; later views hit /api/media/thumbs/… directly."""
    key = media.grab_thumb(cid)
    if not key:
        raise HTTPException(404, "ảnh gốc không còn")
    if BACKEND in ("r2", "s3"):
        return RedirectResponse(store().public_url(key))
    return FileResponse(store().path(key), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.api_route("/api/media/{key:path}", methods=["GET", "HEAD"])
def serve_media(key: str, dl: str | None = None):
    """Local storage only (R2 serves directly / via presigned URL). Supports HTTP Range for video seeking."""
    if not key.startswith(("creatives/", "thumbs/")):
        raise HTTPException(404)
    try:
        p = store().path(key)
    except (AttributeError, ValueError):
        raise HTTPException(404)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                        filename=dl, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ============================================================ Product media / votes / discovery
@router.get("/api/products/{pid}/discovery")
def product_discovery(pid: int, db: Session = Depends(get_db)):
    p = db.get(Product, pid)
    if not p:
        raise HTTPException(404)
    creatives = db.scalars(select(Creative).where(Creative.product_id == pid).order_by(Creative.type.desc(), Creative.id)).all()
    ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.product_id == pid))}
    advs = {a.id: a.name for a in db.scalars(select(Advertiser).where(Advertiser.id.in_({a.advertiser_id for a in ads.values()})))}
    votes = db.scalars(select(Vote).where(Vote.product_id == pid, Vote.user_id == db.info.get("user_id")).order_by(Vote.id.desc())).all()
    prov = Counter(s for (s,) in db.execute(select(AdSource.source).join(Ad, Ad.id == AdSource.ad_id).where(Ad.product_id == pid)))
    fams = Counter(c.family_id for c in creatives if c.family_id)
    return {
        "potential": p.potential or {}, "classification": p.classification, "market_scores": p.market_scores or {},
        "funnel_mix": p.funnel_mix or {}, "sources": dict(prov),
        "creatives": [creative_dict(c, ads.get(c.ad_id), advs.get(ads[c.ad_id].advertiser_id) if c.ad_id in ads else None)
                      | {"family_size": fams.get(c.family_id, 1)} for c in creatives],
        "families": len(fams),
        "votes": [{"user": v.user, "team": v.team, "market": v.market, "decision": v.decision, "reasons": v.reasons,
                   "note": v.note, "at": v.created_at} for v in votes],
        "vote_summary": dict(Counter(v.decision for v in votes)),
    }


class VoteIn(BaseModel):
    user: str
    decision: str
    team: str | None = None
    market: str | None = None
    reasons: list[str] = []
    note: str | None = None


@router.post("/api/products/{pid}/vote")
def vote(pid: int, body: VoteIn, db: Session = Depends(get_db)):
    if body.decision not in VOTE_VALUE:
        raise HTTPException(400, f"decision ∈ {list(VOTE_VALUE)}")
    if not db.get(Product, pid):
        raise HTTPException(404)
    body.user = str(db.info.get("user_id") or "admin-integration")
    old = db.scalar(select(Vote).where(Vote.product_id == pid, Vote.user_id == db.info.get("user_id"), Vote.market == body.market))
    if old:
        old.decision, old.reasons, old.note, old.team, old.created_at = body.decision, body.reasons, body.note, body.team, datetime.utcnow()
    else:
        db.add(Vote(product_id=pid, user_id=db.info.get("user_id"), **body.model_dump()))
    db.flush()
    from ..services.engine import ScoreContext, rescore_products

    ScoreContext.invalidate()  # taste model learns from the new vote
    rescore_products(db, [pid])
    db.commit()
    return {"ok": True}


@router.get("/api/discovery/meta")
def discovery_meta():
    return {"vote_values": VOTE_VALUE, "vote_reasons": VOTE_REASONS}


TABS = {
    "new": "New Discoveries", "hidden": "Hidden Products", "novelty": "High Novelty", "wave": "High Wave",
    "creative": "Creative Goldmine", "favorites": "MKT Favorites", "experimental": "Experimental Opportunities",
    "breakout": "Breakout Products", "scaling": "Recently Scaling", "risk": "High Risk", "saved": "Saved By Team",
    "mess": "Hợp MKT Mess", "ladi": "Hợp MKT Ladi",
}


def fits_funnel(p: Product, funnel: str | None) -> bool:
    """Team filter: MKT Mess / MKT Ladi — product counts when ≥30% of its competitor ads use that funnel."""
    if not funnel:
        return True
    mix = p.funnel_mix or {}
    tot = sum(mix.values())
    return tot > 0 and mix.get(funnel, 0) / tot >= 0.3


@router.get("/api/discovery/tab/{tab}")
def discovery_tab(tab: str, country: str | None = None, funnel: str | None = None, limit: int = Query(60, le=500), db: Session = Depends(get_db)):
    if tab not in TABS:
        raise HTTPException(404)
    return cached(("tab", tab, country, funnel, limit), lambda: _discovery_tab(tab, country, funnel, limit, db))


def _discovery_tab(tab: str, country: str | None, funnel: str | None, limit: int, db: Session) -> dict:
    products = [p for p in db.scalars(select(Product)).all() if p.category != "non_product" and fits_funnel(p, funnel)]
    products = [p for p in products if product_in_scope(p, country)]
    vec = lambda p, k: ((p.potential or {}).get("vector") or {}).get(k) or 0
    now = datetime.utcnow()
    from ..workspace.models import AdInteraction
    loved = {pid for (pid,) in db.execute(select(Vote.product_id).where(Vote.user_id == db.info.get("user_id"), Vote.decision.in_(["LOVE", "TEST"])))}
    saved = set(db.scalars(select(Ad.product_id).join(AdInteraction, Ad.id == AdInteraction.ad_id).where(AdInteraction.user_id == db.info.get("user_id"), AdInteraction.saved.is_(True))))
    fx = {
        "new": (lambda p: p.first_seen_at and p.first_seen_at > now - timedelta(days=7), lambda p: p.first_seen_at),
        "hidden": (lambda p: (p.features or {}).get("advertiser_count", 99) <= 10 and vec(p, "wave_potential") >= 45, lambda p: vec(p, "opportunity")),
        "novelty": (lambda p: vec(p, "novelty") >= 65, lambda p: vec(p, "novelty")),
        "wave": (lambda p: vec(p, "wave_potential") >= 55, lambda p: vec(p, "wave_potential")),
        "creative": (lambda p: vec(p, "creative_potential") >= 55, lambda p: vec(p, "creative_potential")),
        "favorites": (lambda p: p.id in loved, lambda p: vec(p, "mkt_appeal")),
        "experimental": (lambda p: p.classification == "EXPERIMENTAL", lambda p: vec(p, "opportunity")),
        "breakout": (lambda p: p.classification == "BREAKOUT", lambda p: vec(p, "opportunity")),
        "scaling": (lambda p: (p.features or {}).get("new_ads_7d", 0) >= 5 and (p.features or {}).get("creative_growth_7d", 0) >= 0.4,
                    lambda p: (p.features or {}).get("new_ads_7d", 0)),
        "risk": (lambda p: vec(p, "compliance_risk") >= 40, lambda p: vec(p, "compliance_risk")),
        "saved": (lambda p: p.id in saved, lambda p: p.last_seen_at),
        "mess": (lambda p: (p.funnel_mix or {}).get("mess", 0) >= max(1, sum((p.funnel_mix or {}).values()) * 0.3), lambda p: vec(p, "opportunity")),
        "ladi": (lambda p: (p.funnel_mix or {}).get("ladi", 0) >= max(1, sum((p.funnel_mix or {}).values()) * 0.3), lambda p: vec(p, "opportunity")),
    }
    flt, key = fx[tab]
    rows = sorted([p for p in products if flt(p)], key=lambda p: key(p) or 0, reverse=True)[:limit]
    cov, lp = covers(db, rows), landing_links(db, rows)
    return {"tab": tab, "title": TABS[tab], "rows": [product_card(p, cov.get(p.id)) | {"loved_untested": p.id in loved,
                                                                                       "landing_url": lp.get(p.id)}
                                                     for p in rows], "tabs": TABS}


@router.get("/api/discovery/radar")
def radar(country: str | None = None, funnel: str | None = None, db: Session = Depends(get_db)):
    return cached(("radar", country, funnel), lambda: _radar(country, funnel, db))


def _radar(country: str | None, funnel: str | None, db: Session) -> dict:
    pts = []
    for p in db.scalars(select(Product)):
        v = (p.potential or {}).get("vector")
        if not v or p.category == "non_product" or not fits_funnel(p, funnel):
            continue
        if not product_in_scope(p, country):
            continue
        pts.append({"id": p.id, "name": p.canonical_name, "x": v["market_demand"], "y": v.get("momentum", v["wave_potential"]),
                    "opportunity": v["opportunity"], "quadrant": p.classification, "decision": p.recommendation})
    return {"points": pts, "counts": dict(Counter(p["quadrant"] for p in pts))}


@router.get("/api/discovery/market-dna")
def dna(db: Session = Depends(get_db)):
    return market_dna(db)


# ============================================================ Unified Collector admin (§21)
from ..platforms import connector_network
from ..connector_identity import identity as connector_identity, source_key
from ..models import ADS_ONLY


def _mask(cfg: dict, fields: list[dict]) -> dict:
    secret = {f["key"] for f in fields if f.get("secret")}
    return {k: ("••••" if k in secret and v else v) for k, v in (cfg or {}).items()}


def _fresh(ts):
    from ..realtime import freshness

    return freshness(ts)


@router.get("/api/collector")
def collector_overview(db: Session = Depends(get_db)):
    cat = {c["adapter"]: c for c in ConnectorFactory.catalogue()}
    conns = [c for c in db.scalars(select(Connector).where(Connector.adapter.is_not(None)).order_by(Connector.id)) if c.adapter in cat]
    if db.info.get("role") == "MKT":
        return {"connectors": [{"id": c.id, "name": c.name, "adapter": c.adapter, "enabled": c.enabled,
            "supports_search": cat.get(c.adapter, {}).get("supports_search", False), "network": connector_network(c.adapter, c.config),
            "health": c.health, "kind": c.kind, "brand": connector_identity(c.adapter, c.config)} for c in conns],
            "catalogue": [], "tracked_queries": [], "sources": []}
    raw_today = sum(1 for _ in store().list("raw/")) if BACKEND == "local" else None
    return {
        "catalogue": list(cat.values()), "push_sources": PUSH_SOURCES,
        "connectors": [{
            "id": c.id, "name": c.name, "adapter": c.adapter, "kind": c.kind, "group": c.group, "enabled": c.enabled,
            "status": c.status, "health": c.health, "tier": c.tier, "freshness": _fresh(c.last_sync_at), "every_minutes": c.every_minutes,
            "last_sync_at": c.last_sync_at, "last_sync_count": c.last_sync_count, "last_duration_ms": c.last_duration_ms,
            "last_error": c.last_error, "config": _mask(c.config, cat.get(c.adapter, {}).get("config_fields", [])),
            "supports_search": cat.get(c.adapter, {}).get("supports_search", False),
            "config_fields": cat.get(c.adapter, {}).get("config_fields", []),
            "network": connector_network(c.adapter, c.config),
            "brand": connector_identity(c.adapter, c.config),
        } for c in conns],
        "sources": source_summary(db, conns),
        "tracked_queries": [{"id": q.id, "connector_id": q.connector_id, "query": q.query, "countries": q.countries, "page_ids": q.page_ids,
                             "every_minutes": q.every_minutes, "enabled": q.enabled, "last_run_at": q.last_run_at,
                             "last_count": q.last_count, "last_new": q.last_new, "last_error": q.last_error,
                             "freshness": _fresh(q.last_run_at), "in_target": any(c in targets() or c == "ALL" for c in (q.countries or []))}
                            for q in db.scalars(select(TrackedQuery).order_by(TrackedQuery.id.desc()))],
        "storage": {"backend": BACKEND, "raw_files": raw_today, "data_dir": str(DATA_DIR) if BACKEND == "local" else None},
        "creatives": dict(db.execute(select(Creative.status, func.count(Creative.id)).group_by(Creative.status)).all()),
        "ingest_token_required": bool(INGEST_TOKEN),
    }


def source_summary(db: Session, conns: list) -> list[dict]:
    """One row per network: records held, new in 24h, connectors on / healthy — the 'is every platform flowing?' view."""
    from ..platforms import CHANNELS, NETWORKS, REMOVED_NETWORKS

    since = datetime.utcnow() - timedelta(days=1)
    total = dict(db.execute(select(Ad.network, func.count()).group_by(Ad.network)).all())
    fresh = dict(db.execute(select(Ad.network, func.count()).where(Ad.first_seen_at > since).group_by(Ad.network)).all())
    out = []
    for key, (label, channel, color) in NETWORKS.items():
        cs = [c for c in conns if connector_network(c.adapter, c.config) == key]
        if (key == "other" and not total.get(key)) or key in REMOVED_NETWORKS:
            continue
        out.append({"network": key, "label": label, "channel": channel, "channel_label": CHANNELS[channel], "color": color,
                    "records": total.get(key, 0), "new_24h": fresh.get(key, 0), "connectors": len(cs),
                    "enabled": sum(1 for c in cs if c.enabled),
                    "healthy": sum(1 for c in cs if c.enabled and c.health in ("healthy", "slow")),
                    "errors": [f"{c.name}: {c.last_error}"[:160] for c in cs if c.enabled and c.last_error][:2],
                    "last_sync_at": max((c.last_sync_at for c in cs if c.last_sync_at), default=None)})
    return out


class CollectorIn(BaseModel):
    adapter: str
    name: str | None = None
    config: dict = {}
    enabled: bool = True
    every_minutes: int | None = None
    tier: int = 2


@router.post("/api/collector/connectors")
def add_connector(body: CollectorIn, db: Session = Depends(get_db)):
    cls = ConnectorFactory.connectors.get(body.adapter)
    if not cls:
        raise HTTPException(400, "unknown adapter")
    if body.config.get("source"):
        try:
            body.config = dict(body.config) | {"source": source_key(body.config["source"])}
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    if body.adapter == "export" and any(str((c.config or {}).get("source") or "").strip().lower() == body.config.get("source")
                                       for c in db.scalars(select(Connector).where(Connector.adapter == "export"))):
        raise HTTPException(409, "Nguồn này đã có connector nhập file; dùng cấu hình hiện có để tránh đọc trùng thư mục")
    brand = connector_identity(body.adapter, body.config)
    c = Connector(name=body.name or (brand["name"] if brand else cls.name), provider=brand["provider"] if brand else body.adapter, adapter=body.adapter, kind=cls.kind, group=cls.group,
                  config=body.config, enabled=body.enabled, every_minutes=body.every_minutes, tier=body.tier, status="ready")
    db.add(c)
    db.commit()
    return {"id": c.id}


class CollectorPatch(BaseModel):
    config: dict | None = None
    enabled: bool | None = None
    every_minutes: int | None = None
    name: str | None = None


@router.patch("/api/collector/connectors/{cid}")
def patch_connector(cid: int, body: CollectorPatch, db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    if body.config is not None:
        merged = dict(c.config or {})
        merged.update({k: v for k, v in body.config.items() if v != "••••"})
        if merged.get("source"):
            try:
                merged["source"] = source_key(merged["source"])
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
        if c.adapter == "export" and any(str((other.config or {}).get("source") or "").strip().lower() == merged.get("source")
                                         for other in db.scalars(select(Connector).where(Connector.adapter == "export", Connector.id != cid))):
            raise HTTPException(409, "Nguồn đã được connector khác theo dõi; không dùng chung thư mục nhập file")
        c.config = merged
        brand = connector_identity(c.adapter, merged)
        if brand:
            c.provider, c.name = brand["provider"], brand["name"]
    if body.enabled is not None:
        c.enabled = body.enabled
    if body.every_minutes is not None:
        c.every_minutes = body.every_minutes or None
    if body.name:
        c.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/api/collector/connectors/{cid}")
def delete_connector(cid: int, db: Session = Depends(get_db)):
    from ..services.connectors import DEFAULT_CONNECTORS

    c = db.get(Connector, cid)
    if c and c.name in {d[1] for d in DEFAULT_CONNECTORS}:  # defaults are re-seeded by name on startup → disable instead
        c.enabled = False
        db.commit()
        return {"ok": True, "disabled": True}
    if c:
        for q in db.scalars(select(TrackedQuery).where(TrackedQuery.connector_id == cid)):
            db.delete(q)
        db.delete(c)
        db.commit()
    return {"ok": True}


@router.get("/api/collector/connectors/{cid}/status")
def connector_status(cid: int, db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    return {"source": c.adapter, "status": c.health, "last_sync": c.last_sync_at, "records": c.last_sync_count, "error": c.last_error}


@router.post("/api/collector/connectors/{cid}/test")
def connector_test(cid: int, db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if not c:
        raise HTTPException(404)
    try:
        res = ConnectorFactory.get(c.adapter, c.config or {}).health_check()
        c.health = res.get("status", "healthy")
        c.last_error = None
    except Exception as e:
        res = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        c.health, c.last_error = "error", res["error"][:500]
    db.commit()
    return res


@router.post("/api/collector/connectors/{cid}/sync")
def connector_sync(cid: int, bg: BackgroundTasks):
    bg.add_task(realtime.run_connector, cid)
    return {"queued": True}


class QueryIn(BaseModel):
    connector_id: int
    query: str | None = None
    page_ids: list[str] = []
    countries: list[str] = ["ALL"]
    every_minutes: int = 60
    limit: int = 60
    user: str | None = None


@router.post("/api/collector/queries")
def add_query(body: QueryIn, db: Session = Depends(get_db)):
    q = TrackedQuery(connector_id=body.connector_id, query=body.query, page_ids=body.page_ids,
                     countries=[c.upper() for c in body.countries], every_minutes=max(15, body.every_minutes),
                     limit=body.limit, created_by=body.user)
    db.add(q)
    db.commit()
    return {"id": q.id}


@router.delete("/api/collector/queries/{qid}")
def del_query(qid: int, db: Session = Depends(get_db)):
    q = db.get(TrackedQuery, qid)
    if q:
        db.delete(q)
        db.commit()
    return {"ok": True}


@router.post("/api/collector/queries/{qid}/run")
def run_query(qid: int, bg: BackgroundTasks, db: Session = Depends(get_db)):
    q = db.get(TrackedQuery, qid)
    if not q:
        raise HTTPException(404)
    db.commit()  # end the request's write transaction: the background run opens its own session and would wait on it
    bg.add_task(realtime.run_connector, q.connector_id, q)
    return {"queued": True}


@router.post("/api/collector/export-upload")
async def export_upload(source: str = Form(...), country: str | None = Form(None), file: UploadFile = File(...),
                        bg: BackgroundTasks = None, db: Session = Depends(get_db)):
    """Drop a Pipiads / Minea / BigSpy export into its watch folder and process it now."""
    src = re.sub(r"[^a-z0-9_]+", "", source.lower()) or "export"
    folder = DATA_DIR / "import" / src
    folder.mkdir(parents=True, exist_ok=True)
    (folder / re.sub(r"[^\w.-]+", "_", file.filename or "export.csv")).write_bytes(await file.read())
    c = db.scalar(select(Connector).where(Connector.adapter == "export", Connector.config["source"].as_string() == src))
    if not c:
        brand = connector_identity("export", {"source": src})
        c = Connector(name=brand["name"] if brand else f"Export · {src}", provider=brand["provider"] if brand else "export", adapter="export", kind="export", group="ad_intel",
                      config={"source": src, "country": country}, enabled=True, every_minutes=5, tier=2, status="ready")
        db.add(c)
    db.commit()  # also ends the read transaction when the connector already existed (see run_query)
    bg.add_task(realtime.run_connector, c.id)
    return {"connector_id": c.id, "queued": True}


# ============================================================ Ads library view (one card per ad, like Meta Ad Library)
@router.get("/api/ads")
def list_ads(q: str | None = None, country: str | None = None, funnel: str | None = None, platform: str | None = None,
             media_type: str | None = None, active: bool | None = None, product_id: int | None = None,
             advertiser_id: int | None = None, source: str | None = None, sort: str = "newest",
             network: str | None = None, channel: str | None = None, strict: bool = False, expand: bool = True,
             limit: int = Query(40, le=200), offset: int = 0, ad_id: int | None = None, db: Session = Depends(get_db)):
    """`q`: keywords split by | , (OR), words of one keyword AND. Not `strict`: a keyword also matches its translations /
    synonyms / related concepts (lexicon.py), listed after the exact hits."""
    from ..platforms import REMOVED_NETWORKS

    shown = or_(Ad.network.is_(None), Ad.network.not_in(REMOVED_NETWORKS))
    cq = select(Ad).where(Ad.is_internal.is_(False), shown, ad_filter(db))
    if ad_id:
        cq = cq.where(Ad.id == ad_id)
    kws = split_keywords(q)

    def kw_filter(t):
        if strict:  # the product itself: title / product name / landing page — not a word buried in the story of the copy
            return or_(Ad.title.ilike(f"%{t}%"), Ad.raw_product_name.ilike(f"%{t}%"), Ad.landing_url.ilike(f"%{t}%"))
        return or_(Ad.search_text.contains(t), Ad.ad_text.ilike(f"%{t}%"))

    tiers = [keyword_tiers(k, query_intent.expand(db, k)) if expand and not strict else [and_(*[kw_filter(t) for t in k.split()])] for k in kws]
    kw_cond = or_(*[c for t in tiers for c in t]) if kws else None
    tier_rank = case(*[(or_(*[t[i] for t in tiers if len(t) > i]), i) for i in range(2) if any(len(t) > i for t in tiers)],
                     else_=2) if kws else None
    if any(len(t) > 2 for t in tiers):  # limited expansion: related concepts only when exact + synonym are few
        strong = or_(*[c for t in tiers for c in t[:2]])
        if db.scalar(select(func.count()).select_from(select(Ad.id).where(strong, shown).limit(RELATED_MIN).subquery())) >= RELATED_MIN:
            kw_cond = strong
    if kw_cond is not None:
        cq = cq.where(kw_cond)
    _f = None if ad_id else ad_scope_filter(country)
    if _f is not None:
        cq = cq.where(_f)
    if funnel:
        cq = cq.where(Ad.funnel == funnel)
    if platform:
        cq = cq.where(Ad.platform == platform)
    if media_type:
        cq = cq.where(Ad.media_type == media_type)
    if active is not None:
        cq = cq.where(Ad.is_active.is_(active))
    if product_id:
        cq = cq.where(Ad.product_id == product_id)
    if advertiser_id:
        cq = cq.where(Ad.advertiser_id == advertiser_id)
    if source:
        cq = cq.where(Ad.source == source)
    # tab counts per channel (ads / commerce / organic) for the keyword + market filters
    base = select(Ad.channel).where(Ad.is_internal.is_(False), shown, ad_filter(db))
    if kw_cond is not None:
        base = base.where(kw_cond)
    if _f is not None:
        base = base.where(_f)
    sub = base.subquery()
    channels = {k or "ads": n for k, n in db.execute(select(sub.c.channel, func.count()).group_by(sub.c.channel)).all()}
    if channel:
        cq = cq.where(Ad.channel == channel) if channel != "ads" else cq.where(or_(Ad.channel == "ads", Ad.channel.is_(None)))
    # chip counts per network for the current filters (before the network filter itself)
    sub = cq.subquery()
    facets = {k or "meta": n for k, n in db.execute(select(sub.c.network, func.count()).group_by(sub.c.network)).all()}
    if network:
        cq = cq.where(Ad.network == network)
    total = db.scalar(select(func.count()).select_from(cq.subquery()))
    order = {"newest": Ad.first_seen_at.desc(), "longest": Ad.first_seen_at.asc(), "variants": Ad.variants.desc().nullslast(),
             "recent": Ad.id.desc(), "rank": Ad.rank.asc().nullslast(), "reviews": Ad.review_count.desc().nullslast(),
             "sold": Ad.sold_count.desc().nullslast(), "views": Ad.views.desc().nullslast(),
             "price": Ad.price.asc().nullslast()}.get(sort, Ad.first_seen_at.desc())
    ads = db.scalars(cq.order_by(*([tier_rank] if tier_rank is not None else []), order, Ad.id.desc()).offset(offset).limit(limit)).all()
    ids = [a.id for a in ads]
    crs: dict[int, list[Creative]] = {}
    if ids:
        for c in db.scalars(select(Creative).where(Creative.ad_id.in_(ids)).order_by(Creative.position, Creative.id)):
            crs.setdefault(c.ad_id, []).append(c)
    advs = {a.id: a for a in db.scalars(select(Advertiser).where(Advertiser.id.in_({a.advertiser_id for a in ads if a.advertiser_id})))} if ads else {}
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({a.product_id for a in ads if a.product_id})))} if ads else {}
    from urllib.parse import urlparse

    rows = []
    for a in ads:
        adv = advs.get(a.advertiser_id)
        p = prods.get(a.product_id)
        host = urlparse(a.landing_url or "").netloc.removeprefix("www.") if a.landing_url else None
        rows.append(ad_brief(a, adv.name if adv else None) | {
            "advertiser_id": a.advertiser_id, "advertiser_url": adv.page_url if adv else None, "page_id": a.page_id,
            "platforms": a.platforms or ([a.platform] if a.platform else []), "countries": a.countries or [],
            "cta_type": a.cta_type, "landing_domain": host, "external_id": a.external_id,
            "media_type": a.media_type,
            "network": a.network or "meta", "channel": a.channel or "ads", "views": a.views,
            "price": a.price, "currency": a.currency, "original_price": a.original_price, "rating": a.rating,
            "review_count": a.review_count, "sold_count": a.sold_count, "rank": a.rank,
            "snapshot_url": a.snapshot_url,
            "product": {"id": p.id, "name": naming.display_name(p), "code": p.product_code, "name_status": p.name_status} if p else None,
            "ad_type": a.ad_type,
            "creatives": [creative_dict(c) for c in crs.get(a.id, [])],
        })
    return {"total": total, "rows": rows, "has_more": offset + len(rows) < total, "facets": facets, "channels": channels}


# ============================================================ Ingest from the browser extension (no API needed)
class MetaItemsIn(BaseModel):
    items: list[dict]  # raw Ad Library items (collated_results[]) captured from the page the user is browsing
    country: str | None = None
    saved_by: str | None = None


@router.post("/api/ingest/meta-library", dependencies=[Depends(require_token)])
def ingest_meta_library(body: MetaItemsIn, db: Session = Depends(get_db)):
    """The extension forwards what Meta's own Ad Library page loaded for the user. Mapped with the same
    code path as the server-side connector, so ads dedupe against it (same ad_archive_id)."""
    from ..collector.adapters.meta import map_library_ad

    recs = []
    for it in body.items[:500]:
        if isinstance(it, dict) and it.get("ad_archive_id"):
            recs.append(map_library_ad(it, "meta_library", (body.country or "").upper() or None))
    with ingest_lock():  # serialize writers (see realtime.py)
        stats = ingest_ads(db, recs, saved_by=body.saved_by)
    realtime.after_business_events(db, set(stats["product_ids"]))
    return stats


# ============================================================ Storage retention
@router.get("/api/storage/usage")
def storage_usage(db: Session = Depends(get_db)):
    from .. import retention

    return retention.usage(db)


@router.post("/api/storage/cleanup")
def storage_cleanup(dry_run: bool = True, reset: bool = False, db: Session = Depends(get_db)):
    """dry_run=true (default) only reports what would be removed. reset=true wipes all unprotected videos now."""
    from .. import retention

    return retention.run_cleanup(db, dry_run=dry_run, reset=reset)


@router.post("/api/creatives/{cid}/pin")
def pin_creative(cid: int, pinned: bool = True, db: Session = Depends(get_db)):
    c = db.get(Creative, cid)
    if not c:
        raise HTTPException(404)
    c.pinned = pinned
    db.commit()
    return {"ok": True, "pinned": pinned}


# ============================================================ Live market view (computed from current ads, not snapshots)
def _trend(new: int, prev: int) -> tuple[str, float | None]:
    if not prev:
        return ("NEW" if new else "–"), None
    g = new / prev - 1
    arrow = "↑↑↑" if g >= 1 else "↑↑" if g >= 0.4 else "↑" if g >= 0.1 else "↓↓" if g <= -0.5 else "↓" if g <= -0.1 else "→"
    return arrow, round(g, 3)


@router.get("/api/markets/live")
def markets_live(window: int = 7, funnel: str | None = None, db: Session = Depends(get_db)):
    """Per-country state computed live from ads: active ads, sellers, products, ads launched in the last
    `window` days vs the window before (from ad start dates, every ad network), funnel mix, strong ads, data freshness."""
    from ..realtime import freshness

    now = datetime.utcnow()
    w, w2 = now - timedelta(days=window), now - timedelta(days=2 * window)
    q = select(Ad.id, Ad.country, Ad.countries, Ad.is_active, Ad.first_seen_at, Ad.advertiser_id, Ad.product_id, Ad.funnel,
               Ad.force_score, Ad.last_verified_at).where(Ad.is_internal.is_(False), ADS_ONLY)
    if funnel:
        q = q.where(Ad.funnel == funnel)
    cats = dict(db.execute(select(Product.id, Product.category)).all())
    tgt = set(targets()) | {"ALL"}
    per: dict[str, dict] = {}
    for _id, c1, cs, active, first, adv, pid, fn, force, ver in db.execute(q):
        for m in set((cs or []) + ([c1] if c1 else [])):
            d = per.setdefault(m, {"active": 0, "adv": set(), "prod": set(), "new": 0, "prev": 0, "funnel": Counter(),
                                   "cat": Counter(), "strong": 0, "verified": None, "total": 0})
            d["total"] += 1
            if first and first > w:
                d["new"] += 1
            elif first and first > w2:
                d["prev"] += 1
            if not active:
                continue
            d["active"] += 1
            d["adv"].add(adv)
            d["prod"].add(pid)
            d["funnel"][fn or "other"] += 1
            d["cat"][cats.get(pid) or "other"] += 1
            d["strong"] += 1 if (force or 0) >= 45 else 0
            if ver and (d["verified"] is None or ver > d["verified"]):
                d["verified"] = ver

    def row(code, d):
        arrow, g = _trend(d["new"], d["prev"])
        tot = sum(d["funnel"].values()) or 1
        return {"country": code, "region": next((r for r, cs in REGIONS.items() if code in cs), None), "in_target": code in tgt,
                "active_ads": d["active"], "advertisers": len(d["adv"] - {None}), "products": len(d["prod"] - {None}),
                "new_ads": d["new"], "prev_new_ads": d["prev"], "trend": arrow, "growth": g, "strong_ads": d["strong"],
                "mess_share": round(d["funnel"].get("mess", 0) / tot, 3), "ladi_share": round(d["funnel"].get("ladi", 0) / tot, 3),
                "top_categories": [k for k, _ in d["cat"].most_common(3)], "last_verified": d["verified"],
                "freshness": freshness(d["verified"])}

    rows = sorted((row(k, v) for k, v in per.items()), key=lambda r: -r["active_ads"])
    regions = []
    for r, codes in REGIONS.items():
        sub_rows = [x for x in rows if x["country"] in codes and x["in_target"]]
        if not sub_rows:
            continue
        new, prev = sum(x["new_ads"] for x in sub_rows), sum(x["prev_new_ads"] for x in sub_rows)
        arrow, g = _trend(new, prev)
        regions.append({"region": r, "label": REGION_LABEL[r], "countries": len(sub_rows),
                        "active_ads": sum(x["active_ads"] for x in sub_rows), "new_ads": new, "trend": arrow, "growth": g,
                        "strong_ads": sum(x["strong_ads"] for x in sub_rows)})
    return {"window": window, "generated_at": now, "targets": sorted(tgt),
            "rows": [x for x in rows if x["in_target"]], "outside": [x for x in rows if not x["in_target"]], "regions": regions}


@router.get("/api/radar/products")
def product_radar(scope: str | None = None, window: int = 7, funnel: str | None = None, sort: str = "score",
                  limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    """Blueprint §23 Product Radar: Product | Trend | Ads | Sellers | Age | Score for a market + time window."""
    rows = cached(("product_radar", scope, window, funnel, sort), lambda: _product_radar_rows(scope, window, funnel, sort, db))
    page = rows[offset:offset + limit]
    covers_ = {c.id: c for c in db.scalars(select(Creative).where(Creative.id.in_([r["cover_creative_id"] for r in page if r["cover_creative_id"]])))}
    for r in page:
        c = covers_.get(r["cover_creative_id"])
        r["thumb"] = media_url(c.thumb_key) if c and c.thumb_key else None
    return {"scope": scope or "TARGETS", "window": window, "total": len(rows), "rows": page,
            "regions": {k: REGION_LABEL[k] for k in REGIONS}, "targets": targets()}


def _product_radar_rows(scope: str | None, window: int, funnel: str | None, sort: str, db: Session) -> list[dict]:
    now = datetime.utcnow()
    w, w2 = now - timedelta(days=window), now - timedelta(days=2 * window)
    aq = select(Ad).where(Ad.is_internal.is_(False), ADS_ONLY, Ad.product_id.is_not(None))
    f = ad_scope_filter(scope)
    if f is not None:
        aq = aq.where(f)
    if funnel:
        aq = aq.where(Ad.funnel == funnel)
    by: dict[int, list[Ad]] = {}
    for a in db.scalars(aq):
        by.setdefault(a.product_id, []).append(a)
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(by))))}
    rows = []
    for pid, ads in by.items():
        p = prods.get(pid)
        if not p or p.category == "non_product":
            continue
        live = [a for a in ads if a.is_active]
        if not live:
            continue
        new = sum(1 for a in ads if a.first_seen_at and a.first_seen_at > w)
        prev = sum(1 for a in ads if a.first_seen_at and w2 < a.first_seen_at <= w)
        arrow, g = _trend(new, prev)
        first = min(a.first_seen_at for a in ads if a.first_seen_at)
        v = (p.potential or {}).get("vector") or {}
        groups = Counter(a.variation_key for a in live if a.variation_key)
        rows.append({
            "id": p.id, "name": p.canonical_name, "code": p.product_code, "category": p.category,
            "markets": sorted({m for a in live for m in (a.countries or [a.country]) if m} & (set(targets()) | {"ALL"})),
            "trend": arrow, "growth": g, "new_ads": new, "prev_new_ads": prev, "ads": len(live),
            "sellers": len({a.advertiser_id for a in live} - {None}), "age_days": (now - first).days,
            "score": round(v.get("opportunity", p.opportunity_score or 0), 1),
            "force_max": max((a.force_score or 0) for a in live), "strong_ads": sum(1 for a in live if (a.force_score or 0) >= 45),
            "top_tier": max(live, key=lambda a: a.force_score or 0).force_tier,
            "max_variation": max(groups.values(), default=1),
            "mess": sum(1 for a in live if a.funnel == "mess"), "ladi": sum(1 for a in live if a.funnel == "ladi"),
            "decision": p.recommendation, "classification": p.classification, "cover_creative_id": p.cover_creative_id,
            "last_verified": max((a.last_verified_at for a in live if a.last_verified_at), default=None),
        })
    key = {"score": lambda r: r["score"], "trend": lambda r: (r["new_ads"] - r["prev_new_ads"], r["new_ads"], r["growth"] or 0),
           "ads": lambda r: r["ads"], "sellers": lambda r: r["sellers"], "force": lambda r: r["force_max"],
           "age": lambda r: -r["age_days"], "new": lambda r: r["new_ads"]}.get(sort, lambda r: r["score"])
    rows.sort(key=key, reverse=True)
    return rows


class QueryPatch(BaseModel):
    enabled: bool | None = None
    every_minutes: int | None = None


@router.patch("/api/collector/queries/{qid}")
def patch_query(qid: int, body: QueryPatch, db: Session = Depends(get_db)):
    q = db.get(TrackedQuery, qid)
    if not q:
        raise HTTPException(404)
    if body.enabled is not None:
        q.enabled = body.enabled
    if body.every_minutes:
        q.every_minutes = max(15, body.every_minutes)
    db.commit()
    return {"ok": True}


@router.post("/api/collector/liveness")
def run_liveness(max_advertisers: int = 12):
    """Re-check now which ads are still running (normally every 20 min by the scheduler)."""
    return realtime.verify_liveness(max_advertisers=max(1, min(max_advertisers, 50)))


@router.get("/api/collector/scheduler")
def scheduler_status(db: Session = Depends(get_db)):
    """What the background scheduler is doing right now and what is overdue."""
    now = datetime.utcnow()
    overdue = [q for q in db.scalars(select(TrackedQuery).where(TrackedQuery.enabled.is_(True)))
               if not q.last_run_at or (now - q.last_run_at).total_seconds() / 60 >= q.every_minutes]
    return {"running": sorted(realtime._running), "max_parallel": realtime.MAX_PARALLEL_JOBS,
            "overdue_queries": len(overdue), "last_liveness": realtime._last_liveness, "last_cleanup": realtime._last_cleanup,
            "last_full_rescore": realtime._last_daily}
