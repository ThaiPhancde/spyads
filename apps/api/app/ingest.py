"""Unified ingest (spy_app_chat_summary §9-§14).

AdRecord[] (from any connector, the Chrome extension or POST /ingest/ads)
  → raw batch file (storage)            — audit / reprocess
  → dedup (same source id, or same creative across sources) + provenance (ad_sources)
  → product resolution (entity resolution)
  → enrichment: funnel (Mess / Ladi …), hook / angle / offer, category, market
  → Creative rows (pending download)    — media worker stores them right away
  → incremental re-score of the affected products only
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .collector.contract import AdRecord, validate
from .events import publish
from .models import Ad, AdSource, Creative, Product
from .services import enrichment as E
from .services.connectors import _advertiser, _dt, _store
from .services.entity_resolution import resolve_product
from .storage import write_raw_batch

# ------------------------------------------------------------ funnel: MKT Mess vs MKT Ladi
MESS_CTA = {"MESSAGE_PAGE", "SEND_MESSAGE", "WHATSAPP_MESSAGE", "MESSENGER", "INSTAGRAM_MESSAGE", "CONTACT_US_MESSENGER",
            "SEND_WHATSAPP_MESSAGE", "CHAT_WITH_US", "SEND_INSTAGRAM_MESSAGE", "WHATSAPP"}
MESS_HOSTS = ("m.me", "wa.me", "api.whatsapp.com", "whatsapp.com", "messenger.com", "ig.me", "zalo.me", "t.me", "line.me")
FORM_CTA = {"SIGN_UP", "GET_QUOTE", "APPLY_NOW", "SUBSCRIBE", "GET_OFFER", "REQUEST_TIME", "BOOK_NOW"}
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "fb.com", "fb.me", "tiktok.com")
APP_HOSTS = ("play.google.com", "apps.apple.com", "itunes.apple.com")
LADI_HOSTS = ("ladipage", "ladi.me", "ladi.demo", "myshopify.com", "shopbase", "pagefly", "zipify", "youcan.shop",
              "lightfunnels", "easysell", "sellfy", "tiktok.shop")


def classify_funnel(cta_type: str | None, link: str | None, text: str | None = None) -> str:
    cta = (cta_type or "").upper()
    host = (urlparse(link).netloc.lower().removeprefix("www.") if link else "")
    if cta in MESS_CTA or any(host == h or host.endswith("." + h) for h in MESS_HOSTS):
        return "mess"
    if host and any(h in host for h in APP_HOSTS):
        return "app"
    if cta in FORM_CTA and (not host or any(h in host for h in SOCIAL_HOSTS)):
        return "form"
    if host and not any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
        return "ladi"
    if re.search(r"inbox|nhắn tin|ib |whatsapp|واتساب|راسلنا", (text or "").lower()):
        return "mess"
    return "other"


# ------------------------------------------------------------ product name guess
GENERIC_TITLES = {"shop now", "learn more", "order now", "buy now", "send message", "mua ngay", "xem thêm", "اطلب الآن"}
GENERIC_CTA = re.compile(r"^(try|shop|buy|order|get|grab|claim|discover|learn|see|watch|limited|sale|free|new)\b|risk.?free|% ?off|"
                         r"mua ngay|đặt ngay|giảm giá|khuyến mãi|ưu đãi|freeship|^chat (in|with|on)|^send (us )?(a )?message|^message us|^contact us|^unlock|^book now|اطلب|خصم|عرض", re.I)
SLUG_SKIP = {"products", "product", "p", "collections", "shop", "item", "items", "pages", "dp", "vn", "en", "ar", "sa"}


def _slug_name(url: str | None) -> str | None:
    if not url:
        return None
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    for p in reversed(parts):
        if p.lower() in SLUG_SKIP or p.isdigit() or len(p) < 6:
            continue
        words = [w for w in re.split(r"[-_+]+", re.sub(r"\.(html?|php)$", "", p)) if w and not w.isdigit()]
        if len(words) >= 2:
            return " ".join(words[:8])
    return None


def guess_product_name(rec: AdRecord) -> str:
    url = rec.product_url or rec.landing_page or ""
    marketplace = re.search(r"alibaba|aliexpress|amazon\.|noon\.com|shopee|lazada|tiktok\.com", urlparse(url).netloc)
    if re.search(r"/(products?|p|item|dp)/", urlparse(url).path) and not marketplace:  # e-commerce product page → slug is the product
        slug = _slug_name(url)
        if slug:
            return slug
    t = re.sub(r"[^\w\s&+%'.,!?-]|_", " ", (rec.product_name or rec.title or "")).strip(" -.,")
    t = re.sub(r"\s+", " ", t)
    if (len(t.split()) > 9 or t.endswith((".", "!", "?")) or len(t) < 4 or GENERIC_CTA.search(t)):
        t = ""  # headline sentence / CTA, not a product name
    if t and t.lower() not in GENERIC_TITLES and not re.fullmatch(r"[\w.-]+\.(com|net|store|shop|sa|vn|ae)", t.lower()):
        return t[:120]
    slug = None if marketplace else _slug_name(rec.product_url or rec.landing_page)
    if slug:
        return slug
    text = re.sub(r"#\w+|https?://\S+|[^\w\s؀-ۿÀ-ỹ]", " ", rec.ad_text or "")
    words = text.split()
    if words:
        return " ".join(words[:7])
    return f"{rec.advertiser or 'Unknown'} creative"


def _norm_text(s: str | None) -> str:
    return re.sub(r"\W+", "", (s or "").lower())[:120]


def cross_source_key(rec: AdRecord) -> str | None:
    """Same creative reported by different providers: advertiser + copy + landing path."""
    if not (rec.ad_text or rec.landing_page):
        return None
    lp = urlparse(rec.landing_page or "")
    base = f"{(rec.page_id or rec.advertiser or '').lower()}|{_norm_text(rec.ad_text)}|{lp.netloc}{lp.path}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


# ------------------------------------------------------------ main entry
def ingest_ads(db: Session, records: list[AdRecord], saved_by: str | None = None) -> dict:
    from .db import write_lock

    with write_lock():
        return _ingest_ads(db, records, saved_by)


def _ingest_ads(db: Session, records: list[AdRecord], saved_by: str | None = None) -> dict:
    stats = {"received": len(records), "new_ads": 0, "updated_ads": 0, "merged_cross_source": 0, "invalid": 0,
             "new_products": 0, "creatives_queued": 0, "errors": []}
    if not records:
        return {**stats, "product_ids": [], "ad_ids": []}
    by_source: dict[str, list[dict]] = {}
    for r in records:
        by_source.setdefault(r.source, []).append(r.to_dict())
    raw_keys = {src: write_raw_batch(src, "ad", recs) for src, recs in by_source.items()}

    # optional LLM pass: product name + "is this a physical product?" for ads without a usable title / product URL
    hints: dict[int, dict] = {}
    weak = [i for i, r in enumerate(records) if not re.search(r"/(products?|p|item|dp)/", urlparse(r.landing_page or "").path)]
    if weak:
        ex = E.extract_products([{"text": records[i].ad_text, "title": records[i].title, "url": records[i].landing_page} for i in weak])
        if ex:
            hints = {i: e for i, e in zip(weak, ex)}

    product_ids: set[int] = set()
    ad_ids: list[int] = []
    new_creatives: list[int] = []
    now = datetime.utcnow()
    for idx, rec in enumerate(records):
        h = hints.get(idx)
        if h:
            if h.get("product_name"):
                rec.product_name = h["product_name"]
            rec.category = rec.category or ("non_product" if not h.get("is_physical_product", True) or h.get("category") == "service" else h.get("category"))
        errs = validate(rec)
        if errs:
            stats["invalid"] += 1
            stats["errors"].append(f"{rec.source}:{rec.source_ad_id}: {'; '.join(errs)}")
            continue
        try:
            with db.begin_nested():
                ad, created, product_created = _upsert_ad(db, rec, raw_keys.get(rec.source), saved_by, now, stats)
                ad_ids.append(ad.id)
                if ad.product_id:
                    product_ids.add(ad.product_id)
                stats["new_ads" if created else "updated_ads"] += 1
                stats["new_products"] += int(product_created)
                new_creatives += _attach_creatives(db, ad, rec)
                db.flush()
        except Exception as e:
            stats["errors"].append(f"{rec.source}:{rec.source_ad_id}: {type(e).__name__}: {e}")
    db.flush()
    from .services import adsignals

    adsignals.refresh(db, ad_ids)
    stats["creatives_queued"] = len(new_creatives)
    db.commit()
    from . import media

    media.enqueue(new_creatives)
    stats["errors"] = stats["errors"][:20]
    return {**stats, "product_ids": sorted(product_ids), "ad_ids": ad_ids}


def _upsert_ad(db: Session, rec: AdRecord, raw_key: str | None, saved_by: str | None, now: datetime, stats: dict):
    prov = db.scalar(select(AdSource).where(AdSource.source == rec.source, AdSource.source_ad_id == rec.source_ad_id))
    ad = db.get(Ad, prov.ad_id) if prov else None
    if ad is None:
        ad = db.scalar(select(Ad).where(Ad.source == rec.source, Ad.external_id == rec.source_ad_id))
    xkey = cross_source_key(rec)
    if ad is None and xkey:
        ad = db.scalar(select(Ad).where(Ad.creative_fingerprint == xkey))
        if ad is not None:
            stats["merged_cross_source"] += 1
    last = _dt(rec.last_seen, now)
    if ad is not None:  # refresh
        new_markets = {c.upper() for c in (rec.countries or []) + ([rec.country] if rec.country else []) if c}
        merged = sorted(set(ad.countries or []) | new_markets | ({ad.country} if ad.country else set()))
        if merged != sorted(ad.countries or []):
            ad.countries = merged
        ad.last_seen_at = max(ad.last_seen_at, min(last, now))
        if rec.active is not None:
            if rec.active and not ad.is_active:  # seen running again after we marked it stopped
                ad.reactivated_at, ad.inactive_at = now, None
                publish("AD_REACTIVATED", {"ad_id": ad.id}, product_id=ad.product_id, db=db)
            if not rec.active and ad.is_active:
                ad.inactive_at = min(last, now)
            ad.is_active = bool(rec.active)
        if rec.active:
            ad.last_verified_at, ad.verify_misses = now, 0
        for f in ("impressions_text", "spend_text", "reach", "page_likes"):
            if getattr(rec, f) is not None:
                setattr(ad, f, getattr(rec, f))
        for f in ("likes", "views"):
            v = getattr(rec, f)
            if v is not None:
                setattr(ad, f, max(int(v), getattr(ad, f) or 0))
        if rec.comments is not None:
            ad.comments_count = max(int(rec.comments), ad.comments_count or 0)
        if rec.shares is not None:
            ad.shares = max(int(rec.shares), ad.shares or 0)
        if rec.variants:
            ad.variants = rec.variants
        if prov:
            prov.last_collected_at = now
        else:
            db.add(AdSource(ad_id=ad.id, source=rec.source, source_ad_id=rec.source_ad_id, platform=rec.platform,
                            raw_key=raw_key, saved_by=rec.saved_by or saved_by))
        return ad, False, False

    country = (rec.country or (rec.countries[0] if rec.countries else None) or "").upper()[:2] or None
    name = guess_product_name(rec)
    category = rec.category or E.classify_category(name, rec.ad_text)
    product, p_created, _ = resolve_product(db, {
        "name": name, "price": rec.price, "currency": rec.currency, "category": category, "country": country,
        "landing_url": rec.landing_page, "image_url": next((m.preview_url or m.url for m in rec.media if m.type == "image" or m.preview_url), None),
    }, source=rec.source)
    adv = _advertiser(db, rec.advertiser, rec.platform, country, rec.advertiser_url)
    first = _dt(rec.first_seen, now)
    st = _store(db, rec.landing_page, product, adv, country, rec.price, first)
    cr = E.classify_creative(" ".join(filter(None, [rec.title, rec.ad_text])))
    mk = E.classify_market(country, rec.platform, category, rec.price, rec.currency, rec.ad_text)
    product.currency = product.currency or mk["currency"]
    product.language = product.language or mk["language"]
    funnel = classify_funnel(rec.cta_type, rec.landing_page, rec.ad_text)
    first_media = rec.media[0] if rec.media else None
    ad = Ad(
        external_id=rec.source_ad_id, source=rec.source, platform=rec.platform or "facebook", product_id=product.id,
        advertiser_id=adv.id if adv else None, store_id=st.id if st else None, country=country, language=mk["language"],
        raw_product_name=name, ad_text=rec.ad_text, landing_url=rec.landing_page,
        media_type=rec.creative_type or (first_media.type if first_media else None), media_url=first_media.url if first_media else None,
        price=rec.price, likes=int(rec.likes or 0), comments_count=int(rec.comments or 0), shares=int(rec.shares or 0),
        views=int(rec.views) if rec.views is not None else None,
        first_seen_at=min(first, now), last_seen_at=min(last, now), is_active=True if rec.active is None else bool(rec.active),
        last_verified_at=now, inactive_at=None if rec.active is not False else min(last, now),
        impressions_text=rec.impressions_text, spend_text=rec.spend_text, reach=rec.reach, page_likes=rec.page_likes,
        hook=cr["hook"], angle=cr["angle"], offer=cr["offer"], creative_fingerprint=xkey,
        page_id=rec.page_id, title=rec.title, cta_type=rec.cta_type, cta_text=rec.cta_text, funnel=funnel,
        variants=rec.variants, countries=rec.countries or ([country] if country else []), snapshot_url=rec.snapshot_url,
        saved_by=rec.saved_by or saved_by, platforms=rec.platforms or ([rec.platform] if rec.platform else []),
        search_text=" ".join(filter(None, [name, rec.title, rec.ad_text, rec.advertiser, rec.landing_page])).lower()[:4000],
    )
    db.add(ad)
    db.flush()
    db.add(AdSource(ad_id=ad.id, source=rec.source, source_ad_id=rec.source_ad_id, platform=rec.platform,
                    raw_key=raw_key, saved_by=rec.saved_by or saved_by))
    if ad.first_seen_at < product.first_seen_at:
        product.first_seen_at = ad.first_seen_at
    if p_created:
        publish("PRODUCT_DISCOVERED", {"name": product.canonical_name, "source": rec.source, "country": country},
                product_id=product.id, db=db)
    return ad, True, p_created


def _attach_creatives(db: Session, ad: Ad, rec: AdRecord) -> list[int]:
    have = {u for (u,) in db.execute(select(Creative.source_url).where(Creative.ad_id == ad.id))}
    out = []
    for i, m in enumerate(rec.media[:6]):
        if m.url in have:
            continue
        c = Creative(ad_id=ad.id, product_id=ad.product_id, type=m.type, position=i, source=rec.source,
                     source_platform=rec.platform, source_ad_id=rec.source_ad_id, source_url=m.url,
                     preview_source_url=m.preview_url, width=m.width, height=m.height, duration_sec=m.duration_sec,
                     first_seen_at=ad.first_seen_at)
        db.add(c)
        db.flush()
        out.append(c.id)
    return out
