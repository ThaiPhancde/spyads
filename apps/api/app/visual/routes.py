"""Image search API (spec §9). Writes go through AdminGuard (X-Admin-Token) like every other POST.

  POST /api/search/images                 multipart: file | image_url | creative_id | ad_id | product_id | eu_listing_id (+ filters)
  POST /api/search/images/by-url          JSON {image_url, ...filters}
  POST /api/search/images/from-product    JSON {product_id | creative_id | ad_id | eu_listing_id, ...filters}
  GET  /api/search/images/status          index coverage + engine (a search is synchronous, < 1 s: no job ids)
  POST /api/products/visual-match         = from-product / by-url with mode=same_product
  POST /api/products/compare-prices-by-image   multipart or JSON → price table (same-SKU vs similar)
  GET  /api/products/{id}/similar | /price-comparison | /image-matches
  POST /api/products/image-matches/review JSON {source, source_id, target_source, target_id, status}
Errors: HTTP 4xx with {"code": ..., "message": ..., "request_id": ...}.
"""
from __future__ import annotations

import logging
import threading
import time
import hashlib
import uuid
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import media
from ..db import get_db
from ..eu_market.models import EUListing
from ..eu_market.service import fx_table
from ..models import Ad, Creative, Product
from ..routers.intel import split_keywords
from ..storage import store
from . import service
from .models import ProductImage, ProductMatch
from .service import ImageError, QueryImage

log = logging.getLogger("app.visual")
router = APIRouter()
RATE_PER_MIN = 30
_hits: dict[str, deque] = {}
_hits_lock = threading.Lock()


def _rate_limit(request: Request):
    """ponytail: per-IP sliding window in this process; put it behind a shared store if the API is ever replicated."""
    ip = request.client.host if request.client else "?"
    now = time.time()
    with _hits_lock:
        dq = _hits.setdefault(ip, deque())
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= RATE_PER_MIN:
            raise HTTPException(429, {"code": "rate_limited", "message": f"tối đa {RATE_PER_MIN} lượt tìm ảnh / phút"})
        dq.append(now)


def _err(rid: str, e: ImageError, status: int = 400):
    return HTTPException(status, {"code": e.code, "message": str(e), "request_id": rid})


class Filters(BaseModel):
    mode: str = "visual"
    keywords: str | None = None
    country: str | None = None
    sources: str | None = None  # comma list of networks / eu sources / kinds (ad, listing, eu_listing)
    price_min: float | None = None
    price_max: float | None = None
    sort: str = "similarity"
    limit: int = 40
    offset: int = 0
    crop: str | None = None  # "x,y,w,h" fractions


class ByUrl(Filters):
    image_url: str


class FromProduct(Filters):
    product_id: int | None = None
    creative_id: int | None = None
    ad_id: int | None = None
    eu_listing_id: int | None = None


def query_from_url(db: Session, url: str, crop) -> tuple[QueryImage, dict | None]:
    """An image URL → the picture. A product *page* URL (HTML) → its JSON-LD Product: the listing is saved into EU Market
    (with its picture indexed) and its picture becomes the query — spec §3.1 'URL sản phẩm'."""
    try:
        q = QueryImage(service.fetch_image(url), crop)
        q.public_url = url
        return q, None
    except ImageError as e:
        if e.code != "url_not_image":
            raise
    from ..eu_market.connectors import jsonld
    from ..eu_market.connectors.base import ConnectorError
    from ..eu_market.service import upsert_items

    try:
        it = jsonld.fetch_product(url)
    except ConnectorError as ce:
        raise ImageError(f"page_{ce.code}", f"không phải ảnh và trang không có JSON-LD Product: {ce.detail or ce.code}"[:200])
    if not it.get("thumbnail_url"):
        raise ImageError("page_no_image", "trang có Product nhưng không có ảnh")
    saved = None
    try:
        from ..eu_market.jobs import enabled as eu_enabled

        if eu_enabled():
            ids = upsert_items(db, [it])
            if ids:
                saved = {"eu_listing_id": ids[0], "title": it["title"], "price": it.get("price"), "currency": it.get("currency")}
                service.index_one("eu_listing", ids[0])
    except Exception as ex:  # noqa: BLE001 — the search still runs on the page's picture
        log.warning("jsonld listing not saved: %s", str(ex)[:150])
    q = QueryImage(service.fetch_image(it["thumbnail_url"]), crop)
    q.public_url = it["thumbnail_url"]
    return q, saved


def _crop(s: str | None):
    if not s:
        return None
    try:
        x, y, w, h = [max(0.0, min(1.0, float(v))) for v in s.split(",")]
    except ValueError:
        raise ImageError("bad_crop", s[:40])
    return (x, y, w, h) if w > 0 and h > 0 else None


def _creative_bytes(db: Session, c: Creative) -> bytes:
    key = c.thumb_key or media.grab_thumb(c.id)
    if not key:
        raise ImageError("no_image", f"creative {c.id} has no thumbnail")
    return store().get(key)


def query_from_entity(db: Session, f: FromProduct) -> tuple[QueryImage, tuple[str, int]]:
    """The picture of an entity already in SpyAds (spec §3.1 'chọn ảnh có sẵn') + what to exclude from the results."""
    crop = _crop(f.crop)
    if f.creative_id:
        c = db.get(Creative, f.creative_id)
        if not c:
            raise ImageError("not_found", "creative")
        return QueryImage(_creative_bytes(db, c), crop), ("creative", c.id)
    if f.ad_id:
        c = db.scalar(select(Creative).where(Creative.ad_id == f.ad_id, Creative.thumb_key.is_not(None)).order_by(Creative.position))
        c = c or db.scalar(select(Creative).where(Creative.ad_id == f.ad_id).order_by(Creative.position))
        if not c:
            raise ImageError("no_image", "ad has no creative")
        return QueryImage(_creative_bytes(db, c), crop), ("ad", f.ad_id)
    if f.product_id:
        p = db.get(Product, f.product_id)
        if not p:
            raise ImageError("not_found", "product")
        c = db.get(Creative, p.cover_creative_id) if p.cover_creative_id else None
        if not c or not c.thumb_key:
            c = db.scalar(select(Creative).where(Creative.product_id == p.id, Creative.thumb_key.is_not(None)).order_by(Creative.id.desc()))
        if not c:
            c = db.scalar(select(Creative).join(Ad, Ad.id == Creative.ad_id).where(Ad.product_id == p.id, Creative.thumb_key.is_not(None)).order_by(Creative.id.desc()))
        if not c:
            raise ImageError("no_image", "product has no stored picture")
        return QueryImage(_creative_bytes(db, c), crop), ("product", p.id)
    if f.eu_listing_id:
        l = db.get(EUListing, f.eu_listing_id)
        if not l:
            raise ImageError("not_found", "eu listing")
        pi = db.scalar(select(ProductImage).where(ProductImage.source == "eu_listing", ProductImage.source_id == l.id, ProductImage.thumb_key.is_not(None)))
        data = store().get(pi.thumb_key) if pi else service.fetch_image(l.thumbnail_url) if l.thumbnail_url else None
        if not data:
            raise ImageError("no_image", "listing has no picture")
        return QueryImage(data, crop), ("eu_listing", l.id)
    raise ImageError("no_input", "give product_id, creative_id, ad_id or eu_listing_id")


def run(db: Session, q: QueryImage, f: Filters, exclude=None, rid: str = "") -> dict:
    t0 = time.time()
    _embed_query(q)
    res = service.search(db, q, mode=f.mode, keywords=split_keywords(f.keywords), country=f.country,
                         sources=[s for s in (f.sources or "").split(",") if s], price_min=f.price_min, price_max=f.price_max,
                         sort=f.sort, limit=min(max(f.limit, 1), 200), offset=max(f.offset, 0), exclude=exclude)
    res["request_id"], res["took_ms"] = rid, int((time.time() - t0) * 1000)
    log.info("image search %s mode=%s hits=%s tiers=%s %sms", rid, f.mode, res["total"], res["tiers"], res["took_ms"])
    return res


def _embed_query(q: QueryImage):
    try:
        from . import embed

        if embed.enabled():
            q.embedding = embed.embed_image(q.im)
    except Exception as e:  # noqa: BLE001 — the hash tier still answers
        log.warning("query embedding skipped: %s", str(e)[:120])


# ------------------------------------------------------------------ uploaded pictures, kept briefly (image_ref)
# One upload, many searches (local / live / prices / crop) without sending the file again. In memory only, 30 min,
# 64 entries max — never written to disk. ponytail: per-process; with several API replicas use the DB / Redis.
_REFS: dict[str, tuple[float, bytes]] = {}
_REFS_LOCK = threading.Lock()
REF_TTL_S = 1800


def stash(data: bytes) -> str:
    ref = hashlib.sha256(data).hexdigest()
    with _REFS_LOCK:
        now = time.time()
        for k in [k for k, (t, _) in _REFS.items() if now - t > REF_TTL_S]:
            _REFS.pop(k, None)
        while len(_REFS) >= 64:
            _REFS.pop(min(_REFS, key=lambda k: _REFS[k][0]))
        _REFS[ref] = (now, data)
    return ref


def unstash(ref: str) -> bytes:
    with _REFS_LOCK:
        hit = _REFS.get(ref)
    if not hit or time.time() - hit[0] > REF_TTL_S:
        raise ImageError("image_ref_expired", "ảnh tạm đã hết hạn — tải ảnh lên lại")
    return hit[1]


async def resolve(db: Session, file, image_url, image_ref, f: FromProduct):
    """file | image_ref | image_url (picture or product page) | entity → (QueryImage, exclude, saved_listing)."""
    crop = _crop(f.crop)
    if file is not None:
        data = await file.read(service.MAX_UPLOAD_BYTES + 1)
        q = QueryImage(data, crop)
        q.ref = stash(data)
        return q, None, None
    if image_ref:
        q = QueryImage(unstash(image_ref), crop)
        q.ref = image_ref
        return q, None, None
    if image_url:
        q, saved = query_from_url(db, image_url.strip(), crop)
        return q, None, saved
    q, ex = query_from_entity(db, f)
    return q, ex, None


# ------------------------------------------------------------------ endpoints
@router.post("/api/search/images/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Validate + keep the picture for 30 min; later calls pass `image_ref` instead of the file."""
    _rate_limit(request)
    data = await file.read(service.MAX_UPLOAD_BYTES + 1)
    try:
        q = QueryImage(data)
    except ImageError as e:
        raise _err(uuid.uuid4().hex[:12], e)
    return {"image_ref": stash(data), "query": q.info(), "expires_in_s": REF_TTL_S}


@router.post("/api/search/images")
async def search_images(request: Request, file: UploadFile | None = File(None), image_url: str | None = Form(None),
                        creative_id: int | None = Form(None), ad_id: int | None = Form(None), product_id: int | None = Form(None),
                        eu_listing_id: int | None = Form(None), mode: str = Form("visual"), keywords: str | None = Form(None),
                        country: str | None = Form(None), sources: str | None = Form(None), price_min: float | None = Form(None),
                        price_max: float | None = Form(None), sort: str = Form("similarity"), limit: int = Form(40), offset: int = Form(0),
                        crop: str | None = Form(None), image_ref: str | None = Form(None), db: Session = Depends(get_db)):
    _rate_limit(request)
    rid = uuid.uuid4().hex[:12]
    f = FromProduct(mode=mode, keywords=keywords, country=country, sources=sources, price_min=price_min, price_max=price_max, sort=sort,
                    limit=limit, offset=offset, crop=crop, creative_id=creative_id, ad_id=ad_id, product_id=product_id, eu_listing_id=eu_listing_id)
    try:
        q, ex, saved = await resolve(db, file, image_url, image_ref, f)
    except ImageError as e:
        raise _err(rid, e, 404 if e.code in ("not_found", "image_ref_expired") else 400)
    res = run(db, q, f, ex, rid)
    if saved:
        res["saved_listing"] = saved
    return res


@router.post("/api/search/images/by-url")
def search_by_url(body: ByUrl, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request)
    rid = uuid.uuid4().hex[:12]
    try:
        q, saved = query_from_url(db, body.image_url.strip(), _crop(body.crop))
    except ImageError as e:
        raise _err(rid, e)
    res = run(db, q, body, None, rid)
    if saved:
        res["saved_listing"] = saved
    return res


@router.post("/api/search/images/from-product")
def search_from_product(body: FromProduct, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request)
    rid = uuid.uuid4().hex[:12]
    try:
        q, ex = query_from_entity(db, body)
    except ImageError as e:
        raise _err(rid, e, 404 if e.code == "not_found" else 400)
    return run(db, q, body, ex, rid)


@router.get("/api/search/images/status")
def index_status(db: Session = Depends(get_db)):
    return service.status(db)


@router.post("/api/products/visual-match")
def visual_match(body: FromProduct, request: Request, db: Session = Depends(get_db)):
    body.mode = "same_product"
    return search_from_product(body, request, db)


@router.post("/api/products/compare-prices-by-image")
async def compare_prices_by_image(request: Request, file: UploadFile | None = File(None), image_url: str | None = Form(None),
                                  creative_id: int | None = Form(None), ad_id: int | None = Form(None), product_id: int | None = Form(None),
                                  eu_listing_id: int | None = Form(None), keywords: str | None = Form(None), country: str | None = Form(None),
                                  sources: str | None = Form(None), crop: str | None = Form(None), image_ref: str | None = Form(None),
                                  lens_job_id: int | None = Form(None), db: Session = Depends(get_db)):
    """Local offers (ads / China source / EU listings) + — with `lens_job_id` — the priced results of that live web
    search (prices observed in search results unless verified on the page). Never calls the provider itself."""
    res = await search_images(request, file, image_url, creative_id, ad_id, product_id, eu_listing_id, "compare_prices", keywords, country,
                              sources, None, None, "price", 200, 0, crop, image_ref, db)
    fx = fx_table(db)
    rows = res["rows"]
    web = None
    if lens_job_id:
        from . import lens
        from .models import LensSearch

        s = db.get(LensSearch, lens_job_id)
        if s and s.status == "ok":
            web = lens.as_compare_rows(s, fx)
            rows = rows + [r for r in web if r["price"] is not None]
    out = service.compare_prices(rows, fx)
    out.update(query=res["query"], request_id=res["request_id"], engine=res["engine"], took_ms=res["took_ms"],
               web={"job_id": lens_job_id, "results": len(web or []), "priced": sum(1 for r in web or [] if r["price"] is not None)} if lens_job_id else None)
    return out


@router.get("/api/products/{pid}/similar")
def product_similar(pid: int, request: Request, limit: int = 40, sources: str | None = None, db: Session = Depends(get_db)):
    return search_from_product(FromProduct(product_id=pid, mode="similar_product", limit=limit, sources=sources), request, db)


@router.get("/api/products/{pid}/price-comparison")
def product_price_comparison(pid: int, request: Request, db: Session = Depends(get_db)):
    """Offers for this product's own picture (its cover creative) — spec §5. Only real prices; same-SKU vs similar."""
    rid = uuid.uuid4().hex[:12]
    try:
        q, _ = query_from_entity(db, FromProduct(product_id=pid))
    except ImageError as e:
        raise _err(rid, e, 404 if e.code == "not_found" else 400)
    res = run(db, q, Filters(mode="compare_prices", sort="price", limit=200), None, rid)
    out = service.compare_prices(res["rows"], fx_table(db))
    out.update(query=res["query"], request_id=rid, engine=res["engine"], took_ms=res["took_ms"], product_id=pid)
    return out


class WatchIn(BaseModel):
    """Watch the product cluster of a picture: hashes come from a previous search response (query.phash / dhash) or
    from an entity; EU product ids found as EXACT / LIKELY_SAME join product_ids so the existing price rules apply."""
    name: str
    owner: str = "team"
    phash: str | None = None
    dhash: str | None = None
    creative_id: int | None = None
    ad_id: int | None = None
    product_id: int | None = None
    eu_listing_id: int | None = None
    keywords: list[str] = []
    countries: list[str] = []
    eu_product_ids: list[int] = []
    rules: dict = {"price_drop_pct": 10}
    thumb: str | None = None


@router.post("/api/search/images/watch")
def watch_image(body: WatchIn, request: Request, db: Session = Depends(get_db)):
    from ..eu_market.models import EUWatchlist
    from ..eu_market.service import split_keywords as eu_split

    ph, dh, thumb = body.phash, body.dhash, body.thumb
    if not ph:
        rid = uuid.uuid4().hex[:12]
        try:
            q, _ = query_from_entity(db, FromProduct(creative_id=body.creative_id, ad_id=body.ad_id, product_id=body.product_id, eu_listing_id=body.eu_listing_id))
        except ImageError as e:
            raise _err(rid, e, 404 if e.code == "not_found" else 400)
        ph, dh = q.phash, q.dhash
    if not ph or len(ph) != 16:
        raise HTTPException(400, {"code": "no_image", "message": "cần phash (từ kết quả tìm) hoặc một entity có ảnh"})
    rules = {k: float(v) for k, v in (body.rules or {}).items() if k in ("price_drop_pct", "source_gap_pct", "consecutive_rises") and v}
    w = EUWatchlist(name=body.name.strip()[:255] or "Ảnh theo dõi", owner=body.owner or "team", keywords=eu_split(body.keywords),
                    countries=[c.upper() for c in body.countries], sources=[], product_ids=sorted(set(body.eu_product_ids)), rules=rules,
                    images=[{"phash": ph, "dhash": dh or ph, "thumb": thumb, "label": body.name.strip()[:80]}], last_synced_at=datetime.utcnow())
    db.add(w)
    db.commit()
    return {"id": w.id, "name": w.name, "images": w.images, "product_ids": w.product_ids, "keywords": w.keywords, "rules": w.rules,
            "note": "Cảnh báo IMAGE_MATCH_NEW khi kho có ảnh mới khớp; giảm giá / chênh nguồn theo rules trên product_ids EU."}


def _match_dict(m: ProductMatch) -> dict:
    return {"id": m.id, "source": m.source, "source_id": m.source_id, "target_source": m.target_source, "target_id": m.target_id,
            "match_type": m.match_type, "similarity_score": m.similarity_score, "evidence": m.match_evidence, "review_status": m.review_status,
            "created_at": m.created_at}


@router.get("/api/products/{pid}/image-matches")
def product_image_matches(pid: int, request: Request, db: Session = Depends(get_db)):
    """Reviewed matches stored for this product + the live hash/embedding matches of its picture."""
    stored = db.scalars(select(ProductMatch).where(((ProductMatch.source == "product") & (ProductMatch.source_id == pid))
                                                   | ((ProductMatch.target_source == "product") & (ProductMatch.target_id == pid)))).all()
    try:
        live = search_from_product(FromProduct(product_id=pid, mode="visual", limit=60), request, db)
    except HTTPException as e:
        live = {"rows": [], "error": e.detail}
    return {"product_id": pid, "reviewed": [_match_dict(m) for m in stored], "live": live}


class ReviewIn(BaseModel):
    source: str
    source_id: int
    target_source: str
    target_id: int
    status: str  # confirmed | rejected
    match_type: str = "likely_same_product"
    similarity_score: float | None = None
    evidence: dict = {}


@router.post("/api/products/image-matches/review")
def review_match(body: ReviewIn, db: Session = Depends(get_db)):
    if body.status not in ("confirmed", "rejected", "pending"):
        raise HTTPException(400, {"code": "bad_status", "message": "status = confirmed | rejected | pending"})
    m = db.scalar(select(ProductMatch).where(ProductMatch.source == body.source, ProductMatch.source_id == body.source_id,
                                             ProductMatch.target_source == body.target_source, ProductMatch.target_id == body.target_id))
    if not m:
        m = ProductMatch(source=body.source, source_id=body.source_id, target_source=body.target_source, target_id=body.target_id,
                         match_type=body.match_type, similarity_score=body.similarity_score, match_evidence=body.evidence)
        db.add(m)
    m.review_status = body.status
    if body.similarity_score is not None:
        m.similarity_score = body.similarity_score
    db.commit()
    return _match_dict(m)


# ------------------------------------------------------------------ live web discovery (lens.py)
@router.post("/api/search/images/live")
async def live_search(request: Request, file: UploadFile | None = File(None), image_url: str | None = Form(None),
                      image_ref: str | None = Form(None), creative_id: int | None = Form(None), ad_id: int | None = Form(None),
                      product_id: int | None = Form(None), eu_listing_id: int | None = Form(None), country: str | None = Form(None),
                      hl: str = Form("en"), lens_type: str = Form("all"), lens_q: str | None = Form(None), crop: str | None = Form(None),
                      cache_only: bool = Form(False), db: Session = Depends(get_db)):
    """Start (or reuse) a Google Lens web search for the picture. Cached / joined → no API call. cache_only=true never
    calls the provider (used when a page is merely opened). Poll GET /api/search/images/results/{job_id}."""
    from . import lens

    _rate_limit(request)
    rid = uuid.uuid4().hex[:12]
    f = FromProduct(crop=crop, creative_id=creative_id, ad_id=ad_id, product_id=product_id, eu_listing_id=eu_listing_id)
    try:
        q, _, _ = await resolve(db, file, image_url, image_ref, f)
    except ImageError as e:
        raise _err(rid, e, 404 if e.code in ("not_found", "image_ref_expired") else 400)
    try:
        job = lens.start(q, country=(country or "").strip() or None, hl=hl, typ=lens_type, lens_q=(lens_q or "").strip() or None,
                         image_url=getattr(q, "public_url", None), cache_only=cache_only)
    except lens.LensError as e:
        status = 402 if e.code.startswith("quota_exceeded") else 503 if e.code in ("missing_api_key", "live_search_disabled", "image_search_disabled") else 400
        raise HTTPException(status, {"code": e.code, "message": e.detail or e.code, "request_id": rid})
    log.info("live image search %s → job %s %s", rid, job["job_id"], job["status"])
    return {**job, "request_id": rid, "image_ref": getattr(q, "ref", None), "query": q.info()}


@router.get("/api/search/images/results/{job_id}")
def live_results(job_id: int, offset: int = 0, limit: int = 60, keywords: str | None = None, db: Session = Depends(get_db)):
    from . import lens
    from .models import LensSearch

    s = db.get(LensSearch, job_id)
    if not s:
        raise HTTPException(404, {"code": "not_found", "message": "job"})
    return lens.job_view(s, max(0, offset), min(max(limit, 1), 200), split_keywords(keywords))


@router.get("/api/search/images/quota")
def live_quota(db: Session = Depends(get_db)):
    from . import lens

    return lens.quota(db)


class ResultIn(BaseModel):
    index: int


@router.post("/api/search/images/results/{job_id}/verify")
def live_verify(job_id: int, body: ResultIn, db: Session = Depends(get_db)):
    """Check the observed price on the result's product page (public JSON-LD; no quota)."""
    from . import lens
    from .models import LensSearch

    s = db.get(LensSearch, job_id)
    if not s or s.status != "ok":
        raise HTTPException(404, {"code": "not_found", "message": f"không tìm thấy lượt tìm Internet #{job_id}" if not s else f"lượt tìm #{job_id} chưa xong / lỗi ({s.status})"})
    try:
        return lens.verify_price(db, s, body.index)
    except lens.LensError as e:
        raise HTTPException(404 if e.code == "not_found" else 400, {"code": e.code, "message": e.detail or e.code})


@router.post("/api/search/images/results/{job_id}/save")
def live_save(job_id: int, body: ResultIn, db: Session = Depends(get_db)):
    """Save a discovered web result into SpyAds (listing + picture in the local index)."""
    from . import lens
    from .models import LensSearch

    s = db.get(LensSearch, job_id)
    if not s or s.status != "ok":
        raise HTTPException(404, {"code": "not_found", "message": "job"})
    try:
        return lens.save_result(db, s, body.index)
    except lens.LensError as e:
        raise HTTPException(400, {"code": e.code, "message": e.detail})
