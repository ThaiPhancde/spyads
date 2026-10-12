"""Image search core: normalise → hash (→ embed) → candidates from the in-process index → hydrate owners → tiers.

Query path (API thread): < 1 s, no model unless embed.py is enabled. Index path (worker): index_pending() in batches.
ponytail: candidate search is numpy over every indexed hash in RAM (80k rows ≈ 2 MB, ~1 ms); fine to ~5M rows, then
pgvector / a BK-tree. Embedding search has its own ceiling (embed.py).
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import os
import socket
import statistics
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..eu_market.models import EUListing
from ..eu_market.service import fx_table, to_eur
from ..media import _thumb_jpeg
from ..models import Ad, Advertiser, Creative, Product
from ..platforms import NETWORKS
from ..storage import store
from .models import ProductImage

log = logging.getLogger("app.visual")
MAX_UPLOAD_BYTES = int(os.getenv("IMAGE_SEARCH_MAX_MB", "8")) * 1024 * 1024
MAX_PIXELS = int(os.getenv("IMAGE_SEARCH_MAX_PIXELS", "30000000"))  # 30 MP
FETCH_TIMEOUT = float(os.getenv("IMAGE_FETCH_TIMEOUT_S", "10"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ToolSpy-ImageSearch/1.0"
# tiers (spec §3.2): never call a look-alike "the same product"
EXACT, LIKELY_SAME, SIMILAR = "EXACT", "LIKELY_SAME_PRODUCT", "VISUALLY_SIMILAR"
# hash distances (0-64 bits). Calibrated on the ground-truth set in test_image_search.py (see docs/IMAGE_SEARCH.md):
EXACT_DIST = int(os.getenv("IMAGE_EXACT_DIST", "6"))      # resized / recompressed copies of the same picture
SIMILAR_DIST = int(os.getenv("IMAGE_SIMILAR_DIST", "14"))  # crops / overlays / colour edits of the same picture
WHOLESALE_NETWORKS = {"1688"}  # China source listed per piece at MOQ — wholesale, VAT not included
COMMERCE_NETWORKS = {k for k, v in NETWORKS.items() if v[1] == "commerce"}


class ImageError(ValueError):
    """code = stable machine-readable reason (spec §9: clear error codes)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


# ------------------------------------------------------------------ normalise + hash
def open_image(data: bytes) -> Image.Image:
    """Real MIME check = Pillow decodes it; size / pixel caps before any decoding work."""
    if not data:
        raise ImageError("empty_image")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError("too_large", f"image > {MAX_UPLOAD_BYTES // 1048576} MB")
    try:
        im = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError("not_an_image", str(e)[:120])
    if im.format not in ("JPEG", "PNG", "WEBP", "GIF", "BMP", "MPO"):
        raise ImageError("unsupported_format", im.format or "?")
    if im.width * im.height > MAX_PIXELS:
        raise ImageError("too_many_pixels", f"{im.width}x{im.height}")
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001 — broken EXIF is not a reason to refuse the picture
        pass
    return im.convert("RGB")


def hashes(im: Image.Image) -> dict:
    import imagehash

    return {"phash": str(imagehash.phash(im)), "dhash": str(imagehash.dhash(im))}


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


class QueryImage:
    """The user's picture, normalised exactly like an indexed thumbnail (≤480 px JPEG) so hashes are comparable."""

    def __init__(self, data: bytes, crop: tuple[float, float, float, float] | None = None):
        im = open_image(data)
        if crop:  # fractions x, y, w, h of the uploaded picture (ImageCropDialog)
            x, y, w, h = crop
            box = (int(x * im.width), int(y * im.height), int((x + w) * im.width), int((y + h) * im.height))
            if box[2] - box[0] >= 16 and box[3] - box[1] >= 16:
                im = im.crop(box)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        self.crop = crop
        self.upload_src = im.copy()  # cropped picture ≤1600 px: what a live web search (lens.py) uploads
        self.upload_src.thumbnail((1600, 1600))
        self.thumb = _thumb_jpeg(buf.getvalue())
        self.im = Image.open(io.BytesIO(self.thumb)).convert("RGB")
        self.width, self.height = im.size
        self.sha256_raw = hashlib.sha256(data).hexdigest() if not crop else None
        self.sha256 = hashlib.sha256(self.thumb).hexdigest()
        h = hashes(self.im)
        self.phash, self.dhash = h["phash"], h["dhash"]
        self.embedding: np.ndarray | None = None

    def info(self) -> dict:
        return {"sha256": self.sha256, "phash": self.phash, "dhash": self.dhash, "width": self.width, "height": self.height,
                "embedding": self.embedding is not None}


# ------------------------------------------------------------------ SSRF-safe fetch
def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def check_public_host(host: str):
    """Every resolved address must be public (spec §9: no internal / private targets)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ImageError("url_unresolvable", host)
    for *_, sa in infos:
        if _is_private(ipaddress.ip_address(sa[0])):
            raise ImageError("url_private_address", host)


def fetch_image(url: str, max_bytes: int = MAX_UPLOAD_BYTES, timeout: float = FETCH_TIMEOUT, max_redirects: int = 3) -> bytes:
    """Download one picture: http(s) only, public addresses only (re-checked on every redirect hop), ≤ max_redirects,
    byte cap enforced while streaming, image/* content-type. ponytail: DNS is resolved twice (check + httpx) — a
    rebinding race is theoretical here; pin the IP via a custom transport if this ever faces the open internet."""
    for _ in range(max_redirects + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ImageError("url_invalid", url[:120])
        check_public_host(u.hostname)
        with httpx.Client(follow_redirects=False, timeout=timeout, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.5"}) as c:
            with c.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = str(httpx.URL(url).join(r.headers["location"]))
                    continue
                if r.status_code != 200:
                    raise ImageError("url_http_error", f"HTTP {r.status_code}")
                ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ct and not ct.startswith("image/") and ct != "application/octet-stream":
                    raise ImageError("url_not_image", ct)
                buf = bytearray()
                for chunk in r.iter_bytes(65536):
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise ImageError("too_large", f"> {max_bytes // 1048576} MB")
                return bytes(buf)
    raise ImageError("url_too_many_redirects")


# ------------------------------------------------------------------ in-process hash index
class HashIndex:
    """All done rows' hashes as numpy arrays; reloaded when the table's (count, max id, max indexed_at) changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ids = np.zeros(0, dtype=np.int64)
        self.ph = np.zeros(0, dtype=np.uint64)
        self.dh = np.zeros(0, dtype=np.uint64)
        self.stamp = None
        self.checked_at = 0.0

    def refresh(self, db: Session, force: bool = False):
        if not force and time.time() - self.checked_at < 30:
            return
        with self.lock:
            self.checked_at = time.time()
            stamp = db.execute(select(func.count(ProductImage.id), func.max(ProductImage.id), func.max(ProductImage.indexed_at))
                               .where(ProductImage.processing_status == "done")).one()
            if tuple(stamp) == self.stamp:
                return
            rows = db.execute(select(ProductImage.id, ProductImage.phash, ProductImage.dhash)
                              .where(ProductImage.processing_status == "done", ProductImage.phash.is_not(None))).all()
            self.ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
            self.ph = np.fromiter((int(r[1], 16) for r in rows), dtype=np.uint64, count=len(rows))
            self.dh = np.fromiter((int(r[2] or "0", 16) for r in rows), dtype=np.uint64, count=len(rows))
            self.stamp = tuple(stamp)
            log.info("hash index loaded: %s images", len(rows))

    def near(self, phash: str, dhash: str, max_dist: int) -> dict[int, int]:
        """{product_image_id: distance} for every row whose pHash *or* dHash is within max_dist bits."""
        if not len(self.ids):
            return {}
        with self.lock:
            dp = np.bitwise_count(self.ph ^ np.uint64(int(phash, 16)))
            dd = np.bitwise_count(self.dh ^ np.uint64(int(dhash, 16)))
            d = np.minimum(dp, dd)
            idx = np.nonzero(d <= max_dist)[0]
            return {int(self.ids[i]): int(d[i]) for i in idx}


HASH_INDEX = HashIndex()


# ------------------------------------------------------------------ search
def _embedding_hits(q: QueryImage, db: Session, k: int) -> dict[int, float]:
    try:
        from . import embed
    except Exception:  # noqa: BLE001
        return {}
    if q.embedding is None or not embed.enabled():
        return {}
    return embed.INDEX.topk(db, q.embedding, k)


def search(db: Session, q: QueryImage, *, mode: str = "visual", keywords: list[str] | None = None, country: str | None = None,
           sources: list[str] | None = None, price_min: float | None = None, price_max: float | None = None,
           sort: str = "similarity", limit: int = 40, offset: int = 0, exclude: tuple[str, int] | None = None) -> dict:
    """mode: exact | visual | same_product | similar_product | ads_match | find_suppliers | compare_prices."""
    HASH_INDEX.refresh(db)
    cands: dict[int, dict] = {}
    shas = [s for s in (q.sha256, q.sha256_raw) if s]
    for pid in db.scalars(select(ProductImage.id).where(ProductImage.image_sha256.in_(shas), ProductImage.processing_status == "done")):
        cands[pid] = {"exact": True, "dist": 0}
    for pid, d in HASH_INDEX.near(q.phash, q.dhash, SIMILAR_DIST).items():
        c = cands.setdefault(pid, {})
        c["dist"] = min(c.get("dist", 99), d)
    for pid, cos in _embedding_hits(q, db, 300).items():
        cands.setdefault(pid, {})["cos"] = cos
    if not cands:
        return _page([], q, mode, limit, offset, keywords)
    items = hydrate(db, cands, exclude=exclude)
    items = [i for i in items if _passes(i, keywords, country, sources, price_min, price_max, mode)]
    items.sort(key=_sort_key(sort))
    return _page(items, q, mode, limit, offset, keywords)


def tier_of(c: dict, meta_agree: bool = False) -> tuple[str, float]:
    """(tier, similarity 0..1). Hash distance wins when it is tight; embedding cosine fills the rest."""
    d, cos = c.get("dist", 99), c.get("cos")
    if c.get("exact") or d <= EXACT_DIST:
        return EXACT, round(1 - d / 64, 3)
    if cos is not None:
        try:
            from . import embed

            same, sim = embed.SAME_COS, embed.SIMILAR_COS
        except Exception:  # noqa: BLE001
            same, sim = 0.95, 0.80
        if cos >= same and (meta_agree or d <= SIMILAR_DIST):
            return LIKELY_SAME, round(cos, 3)
        if cos >= sim or d <= SIMILAR_DIST:
            return SIMILAR, round(max(cos, 1 - d / 64), 3)
        return SIMILAR, round(cos, 3)
    if d <= SIMILAR_DIST:
        return (LIKELY_SAME if meta_agree and d <= EXACT_DIST + 4 else SIMILAR), round(1 - d / 64, 3)
    return SIMILAR, round(1 - d / 64, 3)


def _thumb_url(key: str | None) -> str | None:
    from ..routers.intel import media_url

    return media_url(key) if key else None


def hydrate(db: Session, cands: dict[int, dict], exclude: tuple[str, int] | None = None) -> list[dict]:
    """ProductImage ids → one result row per owning entity, with the owner's *own* thumbnail, price and link."""
    pis = db.scalars(select(ProductImage).where(ProductImage.id.in_(list(cands)))).all()
    by_src: dict[str, dict[int, ProductImage]] = {}
    for pi in pis:
        by_src.setdefault(pi.source, {})[pi.source_id] = pi
    fx = fx_table(db)
    out: list[dict] = []
    # --- creatives → ad (+ advertiser, product)
    cids = list(by_src.get("creative", {}))
    if cids:
        crs = db.scalars(select(Creative).where(Creative.id.in_(cids))).all()
        ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.id.in_([c.ad_id for c in crs if c.ad_id]))).all()} if crs else {}
        advs = {a.id: a.name for a in db.scalars(select(Advertiser).where(Advertiser.id.in_([a.advertiser_id for a in ads.values() if a.advertiser_id]))).all()} if ads else {}
        pids = {c.product_id for c in crs if c.product_id} | {a.product_id for a in ads.values() if a.product_id}
        prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(pids)))).all()} if pids else {}

        def owner_product(c: Creative):
            a = ads.get(c.ad_id) if c.ad_id else None
            return (a.product_id if a and a.product_id else c.product_id)

        def is_exact(c: Creative) -> bool:
            cc = cands[by_src["creative"][c.id].id]
            return bool(cc.get("exact")) or cc.get("dist", 99) <= EXACT_DIST

        exact_products = {owner_product(c) for c in crs if is_exact(c)} - {None}
        for c in crs:
            pi = by_src["creative"][c.id]
            a = ads.get(c.ad_id) if c.ad_id else None
            prod = prods.get(owner_product(c))
            if exclude and (exclude == ("creative", c.id) or (a and exclude == ("ad", a.id)) or (prod and exclude == ("product", prod.id))):
                continue
            meta_agree = bool(prod and prod.id in exact_products)
            tier, sim = tier_of(cands[pi.id], meta_agree)
            net = c.network or (a.network if a else None) or "meta"
            commerce = net in COMMERCE_NETWORKS or (a is not None and a.channel == "commerce")
            price = a.price if a else None
            cur = (a.currency or ("USD" if price is not None else None)) if a else None
            title = (a.title or a.raw_product_name or (a.ad_text or "")[:140]) if a else (prod.canonical_name if prod else None)
            out.append({
                "key": f"creative:{c.id}", "kind": "listing" if commerce else "ad", "source": net, "source_label": NETWORKS.get(net, (net,))[0],
                "country": (a.country if a else None) or (prod.country if prod else None), "title": title,
                "thumb": _thumb_url(pi.thumb_key or c.thumb_key), "image_url": pi.image_url, "tier": tier, "similarity": sim,
                "hash_distance": cands[pi.id].get("dist"), "cosine": cands[pi.id].get("cos"),
                "price": price, "currency": cur, "price_eur": to_eur(price, cur, fx) if price is not None else None,
                "price_source": (a.price_source if a else None), "price_includes_vat": None, "shipping_price": None,
                "price_basis": "wholesale_per_unit" if net in WHOLESALE_NETWORKS else ("retail" if commerce else "ad_offer"),
                "seller": advs.get(a.advertiser_id) if a and a.advertiser_id else None, "url": (a.landing_url or a.snapshot_url) if a else c.source_url,
                "observed_at": (a.last_seen_at if a else None) or c.collected_at, "first_seen_at": (a.first_seen_at if a else None) or c.first_seen_at,
                "active": bool(a.is_active) if a else None, "funnel": a.funnel if a else None, "creative_id": c.id, "ad_id": a.id if a else None,
                "product_id": prod.id if prod else None, "product_name": prod.canonical_name if prod else None, "product_image_id": pi.id,
                "image_sha256": pi.image_sha256, "eu_listing_id": None, "price_history_url": None,
                "matched_text": " ".join(x for x in ((a.search_text or "") if a else "", (prod.canonical_name or "") if prod else "") if x).lower(),
            })
    # --- EU listings
    lids = list(by_src.get("eu_listing", {}))
    if lids:
        for l in db.scalars(select(EUListing).where(EUListing.id.in_(lids))).all():
            pi = by_src["eu_listing"][l.id]
            if exclude == ("eu_listing", l.id):
                continue
            tier, sim = tier_of(cands[pi.id], meta_agree=False)
            wholesale = l.kind == "supplier_offer"
            out.append({
                "key": f"eu_listing:{l.id}", "kind": "eu_listing", "source": l.source, "source_label": l.source, "country": l.market_country,
                "title": l.title, "thumb": _thumb_url(pi.thumb_key) or l.thumbnail_url, "image_url": l.thumbnail_url, "tier": tier, "similarity": sim,
                "hash_distance": cands[pi.id].get("dist"), "cosine": cands[pi.id].get("cos"),
                "price": l.price, "currency": l.currency, "price_eur": to_eur(l.price, l.currency, fx), "price_source": l.data_access,
                "price_includes_vat": l.price_includes_vat, "shipping_price": l.shipping_price,
                "price_basis": "wholesale_per_unit" if wholesale else "retail", "seller": l.seller_name, "url": l.url,
                "observed_at": l.last_seen_at, "first_seen_at": l.observed_at, "active": l.availability != "out_of_stock", "funnel": None,
                "creative_id": None, "ad_id": None, "product_id": None, "eu_listing_id": l.id, "eu_product_id": l.product_id,
                "product_name": None, "product_image_id": pi.id, "image_sha256": pi.image_sha256, "ean": l.ean, "brand": l.brand,
                "price_history_url": f"/api/eu-market/prices?listing_id={l.id}", "matched_text": (l.search_text or "").lower(),
            })
    return out


def _passes(i: dict, keywords, country, sources, price_min, price_max, mode) -> bool:
    if keywords and not any(all(w in i["matched_text"] or w in (i.get("title") or "").lower() for w in k.split()) for k in keywords):
        return False
    if country and (i.get("country") or "").upper() != country.upper():
        return False
    if sources and i["source"] not in sources and i["kind"] not in sources:
        return False
    p = (i.get("price_eur") or {}).get("eur") if i.get("price_eur") else i.get("price")
    if price_min is not None and (p is None or p < price_min):
        return False
    if price_max is not None and (p is None or p > price_max):
        return False
    if mode == "exact" and i["tier"] != EXACT:
        return False
    if mode == "same_product" and i["tier"] not in (EXACT, LIKELY_SAME):
        return False
    if mode == "ads_match" and i["kind"] != "ad":
        return False
    if mode == "find_suppliers" and not (i["kind"] == "listing" or (i["kind"] == "eu_listing" and i["price_basis"] == "wholesale_per_unit")):
        return False
    if mode == "compare_prices" and i.get("price") is None:
        return False
    return True


def _price_num(i: dict):
    return (i.get("price_eur") or {}).get("eur") if i.get("price_eur") else i.get("price")


def _sort_key(sort: str):
    if sort == "price":
        return lambda i: (_price_num(i) is None, _price_num(i) or 0)
    if sort == "observed":
        return lambda i: -(i["observed_at"].timestamp() if i.get("observed_at") else 0)
    rank = {EXACT: 0, LIKELY_SAME: 1, SIMILAR: 2}
    return lambda i: (rank[i["tier"]], -i["similarity"], -(i["observed_at"].timestamp() if i.get("observed_at") else 0))


def _page(items: list[dict], q: QueryImage, mode: str, limit: int, offset: int, keywords) -> dict:
    for i in items:
        i.pop("matched_text", None)
    tiers = {t: sum(1 for i in items if i["tier"] == t) for t in (EXACT, LIKELY_SAME, SIMILAR)}
    return {"query": q.info(), "mode": mode, "keywords": keywords or [], "total": len(items), "offset": offset, "limit": limit,
            "tiers": tiers, "rows": items[offset:offset + limit], "engine": engine_info()}


def engine_info() -> dict:
    try:
        from . import embed

        emb = embed.status()
    except Exception as e:  # noqa: BLE001
        emb = {"enabled": False, "reason": str(e)[:100]}
    return {"hash": True, "embedding": emb}


# ------------------------------------------------------------------ price comparison (spec §5)
def compare_prices(items: list[dict], fx: dict) -> dict:
    """Same-SKU offers (EXACT / LIKELY_SAME) vs similar ones, retail vs wholesale, VAT known vs unknown. Nothing is
    estimated: an offer with no price is listed without one; "total" exists only when shipping is known."""
    same = [i for i in items if i["tier"] in (EXACT, LIKELY_SAME)]
    similar = [i for i in items if i["tier"] == SIMILAR]

    def eur(i):
        return (i.get("price_eur") or {}).get("eur")

    def stats(rows):
        vals = [eur(i) for i in rows if eur(i) is not None]
        if not vals:
            return {"offers": len(rows), "with_price_eur": 0}
        lo, hi = min(vals), max(vals)
        obs = [i["observed_at"] for i in rows if i.get("observed_at")]
        return {"offers": len(rows), "with_price_eur": len(vals), "min_eur": lo, "max_eur": hi, "median_eur": round(statistics.median(vals), 2),
                "spread_pct": round((hi - lo) / lo * 100, 1) if lo else None,
                "freshest": max(obs) if obs else None, "stalest": min(obs) if obs else None}

    def split(rows):
        return {"retail_incl_vat": stats([i for i in rows if i["price_basis"] != "wholesale_per_unit" and i.get("price_includes_vat") is True]),
                "retail_vat_unknown": stats([i for i in rows if i["price_basis"] != "wholesale_per_unit" and i.get("price_includes_vat") is not True]),
                "wholesale_excl_vat": stats([i for i in rows if i["price_basis"] == "wholesale_per_unit"])}

    now = datetime.utcnow()
    for i in same + similar:
        i["total_known"] = i.get("price") is not None and i.get("shipping_price") is not None
        i["total"] = round(i["price"] + i["shipping_price"], 2) if i["total_known"] else None
        i["stale"] = bool(i.get("observed_at")) and (now - i["observed_at"]).days > 14
    same_priced = [i for i in same if i.get("price") is not None]
    return {"same_product": {"rows": same, "stats": stats(same_priced), "by_basis": split(same_priced)},
            "similar_products": {"rows": similar, "stats": stats([i for i in similar if i.get("price") is not None])},
            "fx": {k: {"rate": v[0], "date": v[1]} for k, v in fx.items() if k != "EUR"},
            "note": "Giá là giá niêm yết thật tại nguồn (ad / listing); quy đổi EUR theo tỷ giá ECB có ngày. 'total' chỉ khi biết phí ship. Không ước lượng."}


# ------------------------------------------------------------------ indexing (worker)
INDEX_BATCH = int(os.getenv("IMAGE_INDEX_BATCH", "400"))
EU_FETCH_BATCH = int(os.getenv("IMAGE_INDEX_EU_BATCH", "40"))


def _row_from_bytes(data: bytes, **kw) -> ProductImage:
    im = open_image(data)
    h = hashes(im)
    return ProductImage(image_sha256=hashlib.sha256(data).hexdigest(), phash=h["phash"], dhash=h["dhash"], width=im.width, height=im.height,
                        indexed_at=datetime.utcnow(), processing_status="done", **kw)


def index_pending(limit: int = INDEX_BATCH, eu_limit: int = EU_FETCH_BATCH) -> dict:
    """Creatives with a stored thumbnail but no index row (local file → hash, no network), then EU listings with a
    thumbnail_url (one small download each, SSRF-guarded). Checkpoint = rows written; idempotent per (source, id)."""
    done = failed = 0
    with SessionLocal() as db:
        have = select(ProductImage.source_id).where(ProductImage.source == "creative")
        rows = db.execute(select(Creative.id, Creative.thumb_key, Creative.source_url, Creative.collected_at)
                          .where(Creative.thumb_key.is_not(None), Creative.id.not_in(have)).order_by(Creative.id.desc()).limit(limit)).all()
        for cid, tkey, src_url, seen in rows:
            try:
                data = store().get(tkey)
                db.add(_row_from_bytes(data, source="creative", source_id=cid, thumb_key=tkey, image_url=src_url, observed_at=seen))
                done += 1
            except FileNotFoundError:
                db.add(ProductImage(source="creative", source_id=cid, thumb_key=tkey, processing_status="no_image", indexed_at=datetime.utcnow()))
            except Exception as e:  # noqa: BLE001
                failed += 1
                db.add(ProductImage(source="creative", source_id=cid, thumb_key=tkey, processing_status="failed", error=str(e)[:250], indexed_at=datetime.utcnow()))
            if (done + failed) % 100 == 0:
                db.commit()
        db.commit()
    eu = index_eu_listings(eu_limit) if eu_limit else {}
    return {"creatives": done, "failed": failed, **eu}


def _eu_row(lid: int, url: str, seen) -> ProductImage:
    """One EU listing picture: small download (SSRF-guarded, ≤ 4 MB) → ≤480 px JPEG in the store → hashes."""
    try:
        raw = fetch_image(url, max_bytes=4 * 1024 * 1024)
        thumb = _thumb_jpeg(raw)
        sha = hashlib.sha256(thumb).hexdigest()
        tkey = f"thumbs/{sha[:2]}/{sha}.jpg"
        if not store().exists(tkey):
            store().put(tkey, thumb, "image/jpeg")
        return _row_from_bytes(thumb, source="eu_listing", source_id=lid, thumb_key=tkey, image_url=url, observed_at=seen)
    except Exception as e:  # noqa: BLE001
        return ProductImage(source="eu_listing", source_id=lid, image_url=url, processing_status="failed", error=str(e)[:250], indexed_at=datetime.utcnow())


def index_eu_listings(limit: int = EU_FETCH_BATCH) -> dict:
    done = failed = 0
    with SessionLocal() as db:
        have = select(ProductImage.source_id).where(ProductImage.source == "eu_listing")
        rows = db.execute(select(EUListing.id, EUListing.thumbnail_url, EUListing.last_seen_at)
                          .where(EUListing.thumbnail_url.is_not(None), EUListing.id.not_in(have)).order_by(EUListing.id.desc()).limit(limit)).all()
        for lid, url, seen in rows:
            row = _eu_row(lid, url, seen)
            db.add(row)
            done += row.processing_status == "done"
            failed += row.processing_status == "failed"
        db.commit()
    return {"eu_listings": done, "eu_failed": failed}


def index_one(source: str, source_id: int) -> ProductImage | None:
    """Index a single owner on demand (the picture the user searched from / just added is not indexed yet)."""
    with SessionLocal() as db:
        pi = db.scalar(select(ProductImage).where(ProductImage.source == source, ProductImage.source_id == source_id))
        if pi and pi.processing_status == "done":
            return pi
        if source == "creative":
            c = db.get(Creative, source_id)
            if not c or not c.thumb_key:
                return None
            row = _row_from_bytes(store().get(c.thumb_key), source="creative", source_id=c.id, thumb_key=c.thumb_key, image_url=c.source_url, observed_at=c.collected_at)
        elif source == "eu_listing":
            l = db.get(EUListing, source_id)
            if not l or not l.thumbnail_url:
                return None
            row = _eu_row(l.id, l.thumbnail_url, l.last_seen_at)
        else:
            return None
        if pi:  # a failed row from an earlier try: replace it
            db.delete(pi)
            db.flush()
        db.add(row)
        db.commit()
        return row if row.processing_status == "done" else None


# ------------------------------------------------------------------ watchlists by picture (spec §8 Watchlists)
def evaluate_image_watchlists(db: Session, now: datetime | None = None) -> dict:
    """For every active EU watchlist with `images`, alert on index rows added since its last check that are within
    SIMILAR_DIST of a watched picture: a new ad / listing / EU offer for the watched product cluster. Real price
    changes come from the existing EU rules (the watchlist's product_ids) — nothing is estimated here."""
    from ..eu_market.models import EUWatchlist
    from ..models import Alert

    now = now or datetime.utcnow()
    HASH_INDEX.refresh(db, force=True)
    made, checked = 0, 0
    for w in db.scalars(select(EUWatchlist).where(EUWatchlist.active.is_(True))).all():
        imgs = [i for i in (w.images or []) if i.get("phash")]
        if not imgs:
            continue
        checked += 1
        since = w.last_synced_at or w.created_at
        hits: dict[int, int] = {}
        for i in imgs:
            for pid, d in HASH_INDEX.near(i["phash"], i.get("dhash") or i["phash"], SIMILAR_DIST).items():
                hits[pid] = min(hits.get(pid, 99), d)
        if hits:
            fresh = db.scalars(select(ProductImage).where(ProductImage.id.in_(list(hits)), ProductImage.indexed_at > since)).all()
            rows = hydrate(db, {pi.id: {"dist": hits[pi.id]} for pi in fresh}) if fresh else []
            for r in rows:
                key = f"IMG:{w.id}:{r['product_image_id']}"[:128]
                if db.scalar(select(Alert.id).where(Alert.dedupe_key == key)):
                    continue
                what = {"ad": "quảng cáo", "listing": "listing nguồn hàng", "eu_listing": "offer EU"}.get(r["kind"], r["kind"])
                price = f" · {r['price']} {r['currency']}" if r.get("price") is not None else ""
                db.add(Alert(type="IMAGE_MATCH_NEW", severity="info", product_id=r.get("product_id"),
                             title=f"[{w.name}] {what} mới khớp ảnh theo dõi ({r['tier']})"[:255],
                             message=f"{r['source_label']} {r.get('country') or ''}: {(r.get('title') or '')[:120]}{price} · giống {int(r['similarity'] * 100)}%",
                             dedupe_key=key, data={"eu_watchlist_id": w.id, "product_image_id": r["product_image_id"], "kind": r["kind"],
                                                   "creative_id": r.get("creative_id"), "ad_id": r.get("ad_id"), "eu_listing_id": r.get("eu_listing_id"),
                                                   "url": r.get("url"), "thumb": r.get("thumb"), "tier": r["tier"]}))
                made += 1
        w.last_synced_at = now
    db.commit()
    return {"watchlists": checked, "alerts": made}


def status(db: Session) -> dict:
    cre_total = db.scalar(select(func.count(Creative.id)).where(Creative.thumb_key.is_not(None))) or 0
    eu_total = db.scalar(select(func.count(EUListing.id)).where(EUListing.thumbnail_url.is_not(None))) or 0
    by = {f"{s}:{st}": n for s, st, n in db.execute(select(ProductImage.source, ProductImage.processing_status, func.count()).group_by(ProductImage.source, ProductImage.processing_status))}
    emb = db.scalar(select(func.count(ProductImage.id)).where(ProductImage.embedding.is_not(None))) or 0
    return {"creatives": {"with_thumbnail": cre_total, "indexed": by.get("creative:done", 0), "failed": by.get("creative:failed", 0), "no_image": by.get("creative:no_image", 0)},
            "eu_listings": {"with_thumbnail": eu_total, "indexed": by.get("eu_listing:done", 0), "failed": by.get("eu_listing:failed", 0)},
            "embeddings": emb, "engine": engine_info(), "thresholds": {"exact_dist": EXACT_DIST, "similar_dist": SIMILAR_DIST},
            "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // 1048576, "max_pixels": MAX_PIXELS}}
