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
from .db import read_only
from .markets import known_country
from .models import Ad, AdSource, Creative, Product
from .platforms import channel_of, network_of
from .services import enrichment as E
from .services.connectors import _advertiser, _dt, _store
from .services.entity_resolution import resolve_product
from .storage import write_raw_batch

COMMERCE_FIELDS = ("rating", "review_count", "sold_count", "rank", "original_price", "currency")
# platforms whose source_ad_id is the marketplace's own item id (1688 offerId…): one listing whichever provider found it
MARKETPLACE_ITEM_IDS = ("1688", "taobao", "aliexpress")

# ------------------------------------------------------------ funnel: MKT Mess vs MKT Ladi
MESS_CTA = {"MESSAGE_PAGE", "SEND_MESSAGE", "WHATSAPP_MESSAGE", "MESSENGER", "INSTAGRAM_MESSAGE", "CONTACT_US_MESSENGER",
            "SEND_WHATSAPP_MESSAGE", "CHAT_WITH_US", "SEND_INSTAGRAM_MESSAGE", "WHATSAPP"}
MESS_HOSTS = ("m.me", "wa.me", "api.whatsapp.com", "whatsapp.com", "messenger.com", "ig.me", "zalo.me", "t.me", "line.me")
FORM_CTA = {"SIGN_UP", "GET_QUOTE", "APPLY_NOW", "SUBSCRIBE", "GET_OFFER", "REQUEST_TIME", "BOOK_NOW"}
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "fb.com", "fb.me", "tiktok.com")
APP_HOSTS = ("play.google.com", "apps.apple.com", "itunes.apple.com")
MARKETPLACE_HOSTS = ("amazon.", "noon.com", "aliexpress.", "temu.com", "ebay.")  # a listing, not a landing page
LADI_HOSTS = ("ladipage", "ladi.me", "ladi.demo", "myshopify.com", "shopbase", "pagefly", "zipify", "youcan.shop",
              "lightfunnels", "easysell", "sellfy", "tiktok.shop")


MESS_RE = re.compile(r"messag|messenger|whatsapp|chat|call now|inbox|nhắn tin|zalo|واتساب|راسلنا", re.I)
LADI_RE = re.compile(r"shop[_ ]now|order[_ ]now|learn[_ ]more|sign[_ ]up|get[_ ]offer|buy[_ ]now|mua ngay|đặt (hàng|mua)|اطلب", re.I)


def _is_host(host: str, hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def classify_funnel(cta_type: str | None, link: str | None, text: str | None = None, cta_text: str | None = None) -> str:
    """CTA is the primary signal (what the advertiser asked for), the landing host the fallback."""
    cta = (cta_type or "").upper()
    host = (urlparse(link).netloc.lower().removeprefix("www.") if link else "")
    if cta in MESS_CTA or MESS_RE.search(f"{cta_type or ''} {cta_text or ''}") or _is_host(host, MESS_HOSTS):
        return "mess"
    external = host and not _is_host(host, SOCIAL_HOSTS) and not any(h in host for h in APP_HOSTS + MARKETPLACE_HOSTS)
    if external and LADI_RE.search(f"{cta_type or ''} {cta_text or ''}"):
        return "ladi"
    if host and any(h in host for h in APP_HOSTS):
        return "app"
    if host and any(h in host for h in MARKETPLACE_HOSTS):
        return "other"
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
    """LLM hint / link title → landing-page slug → advertiser + '(chưa đặt tên)'. Never the ad copy."""
    url = rec.product_url or rec.landing_page or ""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    marketplace = re.search(r"alibaba|aliexpress|amazon\.|noon\.com|shopee|lazada|temu\.|ebay\.", host) or _is_host(host, SOCIAL_HOSTS + MESS_HOSTS)
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
    slug = None if marketplace else _slug_name(url)
    if slug:
        return slug
    return f"{rec.advertiser or 'Unknown'} (chưa đặt tên)"


# ------------------------------------------------------------ listed price in the copy ("₱189", "199 SAR", "$19.99", "199.000đ", "199k")
_SYM = {"₱": "PHP", "P": "PHP", "PHP": "PHP", "SAR": "SAR", "ر.س": "SAR", "ريال": "SAR", "AED": "AED", "د.إ": "AED", "درهم": "AED",
        "QAR": "QAR", "KWD": "KWD", "OMR": "OMR", "BHD": "BHD", "$": "USD", "US$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR",
        "£": "GBP", "GBP": "GBP", "A$": "AUD", "AU$": "AUD", "AUD": "AUD", "NZ$": "NZD", "NZD": "NZD", "RM": "MYR", "RP": "IDR",
        "đ": "VND", "Đ": "VND", "₫": "VND", "VND": "VND", "VNĐ": "VND", "TR": "VND", "TRIỆU": "VND"}
_NUM = r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{1,2})?)"
_PRICE_RE = re.compile(r"(?<![\w.])(₱|PHP|P|SAR|AED|QAR|KWD|OMR|BHD|US\$|USD|\$|€|EUR|£|GBP|AU?\$|AUD|NZ\$|NZD|RM|Rp|ر\.س|د\.إ) ?" + _NUM + r"(?![\w%])"
                       r"|(?<![\w.])" + _NUM + r" ?(SAR|AED|QAR|KWD|OMR|BHD|PHP|VND|VNĐ|USD|EUR|GBP|AUD|NZD|ر\.س|د\.إ|ريال|درهم|đ|₫|[kK]|tr|triệu)(?![\w%])",
                       re.I)
_VI = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]")
_COUNT_WORD = re.compile(r"^\s*(lượt|khách|người|đơn|follow|view|sold|hạt|chai|hộp|sản phẩm|sp\b|đã bán|members?|customers?|units?|pcs)", re.I)


def parse_price(text: str | None, country: str | None = None) -> tuple[float, str | None] | None:
    """First listed price in the text → (amount, currency). '199k' / '2tr' / '10 triệu' are Vietnamese shorthand
    (×1000 / ×1e6) — currency VND when the copy is Vietnamese or the ad is in VN, else unknown."""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    sym, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    parts = re.split(r"[.,]", num)
    dec = parts.pop() if len(parts) > 1 and len(parts[-1]) != 3 else None  # "19.99" decimals; "199.000" thousands
    v = float("".join(parts) + (f".{dec}" if dec else ""))
    if v == 0:  # "Freeship 0Đ"
        return None
    s = sym.upper()
    ctx = text[max(0, m.start() - 14):m.end() + 10].lower()
    if s in ("K", "TR", "TRIỆU"):
        if _COUNT_WORD.search(text[m.end():m.end() + 14]):  # "2,4 triệu hạt", "15 triệu chai đã bán", "50k lượt mua"
            return None
        if s == "K":
            if v < 10:  # "4K video", "5K followers" — not a price
                return None
            if not (country == "VN" or _VI.search(text)):
                return None  # "99k" outside a Vietnamese ad: thousands of what? — no currency to convert, so no price
            return (v * 1000, "VND")
        if not re.search(r"giá|chỉ|còn|từ|trả|đồng|vnđ|₫", ctx):  # million-đồng amounts need price context ("chỉ hơn 9 triệu đồng")
            return None
        return (v * 1_000_000, "VND")
    if s == "P" and sym != "₱" and not (country in (None, "PH", "ALL") or "PH" in str(country)):
        return None  # bare "P499" is pesos only in a Philippine ad
    return (v, _SYM.get(s) or _SYM.get(sym))


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
    stats = {"received": len(records), "new_ads": 0, "updated_ads": 0, "merged_cross_source": 0, "invalid": 0,
             "new_products": 0, "creatives_queued": 0, "errors": []}
    if not records:
        return {**stats, "product_ids": [], "ad_ids": [], "listing_ids": []}
    by_source: dict[str, list[dict]] = {}
    for r in records:
        by_source.setdefault(r.source, []).append(r.to_dict())
    raw_keys = {src: write_raw_batch(src, "ad", recs) for src, recs in by_source.items()}

    # optional LLM pass: product name + "is this a physical product?" for NEW ads without a usable product URL
    # (a refresh keeps its name — calling the LLM for every re-seen ad burned the quota, audit §4)
    known: set[tuple[str, str]] = set()
    db.commit()  # end any open transaction: the lookup below must not take the write lock…
    with read_only():  # …and the LLM call after it (network, up to minutes) must run with no SQLite lock held at all
        for src in by_source:  # ponytail: one IN() per source, batches are a few hundred ids at most
            ids = [r.source_ad_id for r in records if r.source == src]
            known |= {(src, i) for i in db.scalars(select(AdSource.source_ad_id).where(AdSource.source == src, AdSource.source_ad_id.in_(ids)))}
            known |= {(src, i) for i in db.scalars(select(Ad.external_id).where(Ad.source == src, Ad.external_id.in_(ids)))}
            for plat in {r.platform for r in records if r.source == src and r.platform in MARKETPLACE_ITEM_IDS}:
                known |= {(src, i) for i in db.scalars(select(Ad.external_id).where(Ad.platform == plat, Ad.external_id.in_(ids)))}
    db.commit()
    hints: dict[int, dict] = {}
    weak = [i for i, r in enumerate(records) if (r.source, r.source_ad_id) not in known
            and not re.search(r"/(products?|p|item|dp)/", urlparse(r.landing_page or "").path)]
    if weak:
        ex = E.extract_products([{"text": records[i].ad_text, "title": records[i].title, "url": records[i].landing_page} for i in weak])
        if ex:
            hints = {i: e for i, e in zip(weak, ex)}

    product_ids: set[int] = set()
    ad_ids: list[int] = []
    listing_ids: list[int] = []  # commerce rows (marketplace listings) — a search counts ads, not listings
    new_creatives: list[int] = []
    now = datetime.utcnow()
    for idx, rec in enumerate(records):
        price_source = "source" if rec.price is not None else None
        if rec.price is None and (pc := parse_price(f"{rec.title or ''} {rec.ad_text or ''}", rec.country)):
            rec.price, rec.currency, price_source = pc[0], pc[1] or rec.currency, "ad_text"
        h = hints.get(idx)
        if h:
            if h.get("product_name"):
                rec.product_name = h["product_name"]
            rec.category = rec.category or ("non_product" if not h.get("is_physical_product", True) or h.get("category") in ("service", "food")
                                            else h.get("category"))
        errs = validate(rec)
        if errs:
            stats["invalid"] += 1
            stats["errors"].append(f"{rec.source}:{rec.source_ad_id}: {'; '.join(errs)}")
            continue
        try:
            with db.begin_nested():
                ad, created, product_created = _upsert_ad(db, rec, raw_keys.get(rec.source), saved_by, now, stats, price_source)
                mq = (rec.matched_query or "").lower().strip()
                if mq and mq not in (ad.search_text or ""):  # the source matched it for this keyword → our search must too
                    ad.search_text = f"{mq} {ad.search_text or ''}"[:4000]
                ad_ids.append(ad.id)
                if ad.channel == "commerce":
                    listing_ids.append(ad.id)
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
    return {**stats, "product_ids": sorted(product_ids), "ad_ids": ad_ids, "listing_ids": listing_ids}


def _upsert_ad(db: Session, rec: AdRecord, raw_key: str | None, saved_by: str | None, now: datetime, stats: dict,
               price_source: str | None = None):
    prov = db.scalar(select(AdSource).where(AdSource.source == rec.source, AdSource.source_ad_id == rec.source_ad_id))
    ad = db.get(Ad, prov.ad_id) if prov else None
    if ad is not None and ad.source == rec.source and ad.external_id != rec.source_ad_id:
        db.delete(prov)  # wrongly merged into another ad of the same source (old fingerprint rule): give it its own row
        db.flush()
        prov = ad = None
    if ad is None:
        ad = db.scalar(select(Ad).where(Ad.source == rec.source, Ad.external_id == rec.source_ad_id))
    if ad is None and rec.platform in MARKETPLACE_ITEM_IDS:  # marketplace item id is global: the same offer from another
        ad = db.scalar(select(Ad).where(Ad.platform == rec.platform, Ad.external_id == rec.source_ad_id).limit(1))  # provider
    xkey = cross_source_key(rec)
    if ad is None and xkey:  # same source + different ad id = a different ad (other video / audience), never merge
        ad = db.scalar(select(Ad).where(Ad.creative_fingerprint == xkey, Ad.source != rec.source,  # same network only:
                                        Ad.network == network_of(rec.platform, rec.source)).limit(1))  # a listing ≠ an ad
        if ad is not None:
            stats["merged_cross_source"] += 1
    last = _dt(rec.last_seen, now)
    if ad is not None:  # refresh
        new_markets = {c.upper() for c in (rec.countries or []) + ([rec.country] if rec.country else []) if c}
        merged = sorted(set(ad.countries or []) | new_markets | ({ad.country} if ad.country else set()))
        if merged != sorted(ad.countries or []):
            ad.countries = merged
        if not ad.country and (cc := known_country(rec)):  # first seen in a worldwide / liveness scan, now in a country search
            ad.country = cc
            if ad.product_id and (p := db.get(Product, ad.product_id)) and not p.country:
                p.country = cc
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
        for f in COMMERCE_FIELDS:  # listings: latest rank / price / rating win
            if getattr(rec, f) is not None:
                setattr(ad, f, getattr(rec, f))
        if rec.price and (ad.channel == "commerce" or ad.price is None):  # listings: latest price; ads: first price we ever see
            ad.price, ad.price_source = rec.price, price_source
            ad.currency = rec.currency or ad.currency
        if rec.advertiser and not ad.advertiser_id:  # listing merged from a provider without the supplier (1688 AK)
            if adv := _advertiser(db, rec.advertiser, rec.platform, ad.country, rec.advertiser_url):
                ad.advertiser_id = adv.id
        if prov:
            prov.last_collected_at = now
        else:
            db.add(AdSource(ad_id=ad.id, source=rec.source, source_ad_id=rec.source_ad_id, platform=rec.platform,
                            raw_key=raw_key, saved_by=rec.saved_by or saved_by))
        return ad, False, False

    country = known_country(rec)  # "ALL" = worldwide search: stays in `countries`, never truncated to "AL" (Albania)
    name = guess_product_name(rec)
    category = rec.category or E.classify_category(name, rec.ad_text)
    product, p_created, _ = resolve_product(db, {
        "name": name, "advertiser": rec.advertiser, "price": rec.price, "currency": rec.currency, "category": category, "country": country,
        "landing_url": rec.landing_page, "image_url": next((m.preview_url or m.url for m in rec.media if m.type == "image" or m.preview_url), None),
    }, source=rec.source)
    adv = _advertiser(db, rec.advertiser, rec.platform, country, rec.advertiser_url)
    first = _dt(rec.first_seen, now)
    st = _store(db, rec.landing_page, product, adv, country, rec.price, first)
    cr = E.classify_creative(" ".join(filter(None, [rec.title, rec.ad_text])))
    mk = E.classify_market(country, rec.platform, category, rec.price, rec.currency, rec.ad_text)
    product.currency = product.currency or mk["currency"]
    product.language = product.language or mk["language"]
    funnel = classify_funnel(rec.cta_type, rec.landing_page, rec.ad_text, rec.cta_text)
    first_media = rec.media[0] if rec.media else None
    ad = Ad(
        external_id=rec.source_ad_id, source=rec.source, platform=rec.platform or "facebook", product_id=product.id,
        advertiser_id=adv.id if adv else None, store_id=st.id if st else None, country=country, language=mk["language"],
        raw_product_name=name, ad_text=rec.ad_text, landing_url=rec.landing_page,
        media_type=rec.creative_type or (first_media.type if first_media else None), media_url=first_media.url if first_media else None,
        price=rec.price, price_source=price_source, likes=int(rec.likes or 0), comments_count=int(rec.comments or 0), shares=int(rec.shares or 0),
        views=int(rec.views) if rec.views is not None else None,
        first_seen_at=min(first, now), last_seen_at=min(last, now), is_active=True if rec.active is None else bool(rec.active),
        last_verified_at=now, inactive_at=None if rec.active is not False else min(last, now),
        impressions_text=rec.impressions_text, spend_text=rec.spend_text, reach=rec.reach, page_likes=rec.page_likes,
        hook=cr["hook"], angle=cr["angle"], offer=cr["offer"], creative_fingerprint=xkey,
        page_id=rec.page_id, title=rec.title, cta_type=rec.cta_type, cta_text=rec.cta_text, funnel=funnel,
        variants=rec.variants, countries=rec.countries or ([country] if country else []), snapshot_url=rec.snapshot_url,
        saved_by=rec.saved_by or saved_by, platforms=rec.platforms or ([rec.platform] if rec.platform else []),
        search_text=" ".join(filter(None, [name, rec.title, rec.ad_text, rec.advertiser, rec.landing_page])).lower()[:4000],
        network=(net := network_of(rec.platform, rec.source)), channel=channel_of(net),
        **{f: getattr(rec, f) for f in COMMERCE_FIELDS},
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


def bare_url(u: str | None) -> str:
    """CDN URL without its signature (fbcdn / tiktokcdn sign in the query, so it changes every fetch)."""
    return (u or "").split("#", 1)[0].split("?", 1)[0]


def _attach_creatives(db: Session, ad: Ad, rec: AdRecord) -> list[int]:
    have = {bare_url(c.source_url): c for c in db.scalars(select(Creative).where(Creative.ad_id == ad.id))}
    out = []
    for i, m in enumerate(rec.media[:6]):
        c = have.get(bare_url(m.url))
        if c is not None:
            if c.status != "stored" and c.source_url != m.url:
                c.source_url, c.preview_source_url = m.url, m.preview_url or c.preview_source_url  # fresh signature for a download still pending
                c.sd_source_url = m.sd_url or c.sd_source_url
            continue
        c = Creative(ad_id=ad.id, product_id=ad.product_id, type=m.type, position=i, source=rec.source,
                     source_platform=rec.platform, network=ad.network, source_ad_id=rec.source_ad_id, source_url=m.url,
                     preview_source_url=m.preview_url, sd_source_url=m.sd_url, width=m.width, height=m.height, duration_sec=m.duration_sec,
                     first_seen_at=ad.first_seen_at)
        db.add(c)
        db.flush()
        out.append(c.id)
    return out
