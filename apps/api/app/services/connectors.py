"""Connector Layer (blueprint §3) + Normalization / Dedup / Entity resolution hand-off.

Flow: adapter.fetch() → RawRecord (data lake) → normalize_pending() → domain tables.

Canonical record shapes (what every adapter / CSV / webhook must produce):
  ad:          external_id, source, platform, advertiser, product_name, ad_text, landing_url, country,
               media_type, media_url, price, currency, likes, comments_count, shares, first_seen, last_seen,
               is_active, category, brand, is_internal, rejected, rejection_reason
  store:       domain, product_code | product_name, country, price, estimated_traffic, traffic_growth
  product_signal: product_code | product_name, search_trend, keyword_competition   (Semrush / keyword tools)
"""
import csv
import hashlib
import io
from datetime import date, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ad, Advertiser, Comment, Connector, Product, RawRecord, Store
from . import enrichment as E
from .entity_resolution import resolve_product

ENTITY_TYPES = ["ad", "store", "product_signal"]

# Default connectors (Unified Collector). Only the public Meta Ad Library works without credentials;
# the others are templates the admin fills in (Data & Connectors screen). No simulated sources.
DEFAULT_CONNECTORS = [
    # adapter, name, enabled, every_minutes, tier, config
    ("meta_library", "Meta Ad Library (public) — tìm kiếm & theo dõi", True, None, 2, {"countries": "SA,AE,VN", "max_per_query": 60, "search_limit": 40}),
    ("meta_graph", "Meta Ad Library API (official, EU/UK)", False, 360, 2, {}),
    ("apify_meta", "Apify · Facebook Ads Library (production volume)", False, 60, 2, {}),
    ("tiktok_commercial", "TikTok Commercial Content API (official, EU)", False, 360, 2, {}),
    ("apify_actor", "Apify · TikTok ads / Creative Center", False, 60, 2, {"source": "tiktok"}),
    ("http", "Pipiads API (Enterprise)", False, 60, 2, {"source": "pipiads"}),
    ("export", "Export · pipiads", True, 5, 2, {"source": "pipiads"}),
    ("export", "Export · minea", True, 5, 2, {"source": "minea"}),
    # the TikTok ad volume source: ≤ 500 top ads per country, MP4 + likes, incl. SA / AE. Runs on request headers pasted
    # from DevTools (ads.tiktok.com/business/creativecenter, logged in to a free account) — the app never signs requests
    ("tiktok_top_ads", "TikTok Creative Center · Top Ads (free account) — PH/US/EU/SA/AE", False, 360, 2,
     {"countries": "PH,US,GB,DE,FR,AU,SA,AE", "period": 30, "order_by": "ctr", "pages": 25, "details": 20}),
    # no login: headless Edge/Chrome reads the public page (≈150 ads / country at period 180) — needs a browser on the API host
    ("tiktok_top_ads_headless", "TikTok Creative Center · Top Ads (không cần đăng nhập) — PH/US/…", True, 720, 2,
     {"countries": "PH,US,SA", "period": 180}),
    # ad libraries beyond Meta (EU transparency) — collector/adapters/ad_libraries.py
    # Snap searches brand names only and rate-limits non-EU IPs (429): opt-in, used for brand / competitor lookups
    ("snapchat_ads_library", "Snapchat Ads Library (free) — theo tên brand", False, None, 2, {"countries": "FR,DE"}),
    # no proxy needed (X-CCL-STR session, see the adapter); covers ads delivered in the EU/EEA/UK/CH
    ("tiktok_ad_library", "TikTok Ad Library (free) — quảng cáo đối thủ EU/UK", True, None, 2, {"countries": "all", "days": 90, "details": 24}),
    # China source searched together with the ads (supplier price → margin) — collector/adapters/marketplaces.py
    ("aliexpress_search", "AliExpress (free) — giá nhập & đã bán", True, None, 2, {}),
    # Apify actors with typed mappings (collector/adapters/apify.py) — token from APIFY_TOKEN in .env, billed per item:
    # live search only (no schedule) and capped per search by `max_items`
    # 1688 hybrid (docs/ToolSpy_1688_Hybrid_Connector.md): official AK API first (ALI_1688_AK in .env, free); when the
    # search also selects apify_1688 it becomes this one's fallback instead of a second run (realtime._run_search)
    ("ali1688", "1688 — API chính thức (AK) · fallback Apify", True, None, 2, {}),
    ("apify_1688", "1688 — giá xưởng TQ (Apify)", True, None, 2, {}),
    ("apify_taobao", "Taobao / Tmall — giá & đã bán TQ (Apify)", True, None, 2, {}),
    ("apify_tiktok_top_ads", "TikTok Ads · Creative Center Top Ads (Apify) — PH/US/EU/ME", True, None, 2, {}),
    # same TikTok Ad Library as the free connector above (EU/UK only), slower: off unless the free one breaks
    ("apify_tiktok_ads", "TikTok Ads · Ad Library EU/UK (Apify)", False, None, 2, {}),
]
FREE_READY = ("meta_library", "export", "snapchat_ads_library", "tiktok_ad_library", "tiktok_top_ads_headless",
              "aliexpress_search", "apify_1688", "apify_taobao", "apify_tiktok_top_ads", "apify_tiktok_ads")


def ensure_default_connectors(db: Session):
    """Seed default connectors; on existing DBs only add defaults that are missing (matched by name)."""
    from ..collector.factory import ConnectorFactory

    have = set(db.scalars(select(Connector.name).where(Connector.adapter.is_not(None))))
    for adapter, name, enabled, every, tier, cfg in DEFAULT_CONNECTORS:
        if name in have:
            continue
        cls = ConnectorFactory.connectors[adapter]
        db.add(Connector(name=name, provider=adapter, adapter=adapter, kind=cls.kind, group=cls.group,
                         config=cfg, enabled=enabled, every_minutes=every, tier=tier,
                         status="ready" if adapter in FREE_READY else "not_configured"))
    # adapters dropped from the code (Shopify stores …): switched off, rows kept
    for c in db.scalars(select(Connector).where(Connector.adapter.is_not(None), Connector.enabled.is_(True))):
        if c.adapter not in ConnectorFactory.connectors:
            c.enabled, c.status = False, "not_configured"
    db.flush()


# ------------------------------------------------------------ Raw lake
def _dedupe_key(entity_type: str, rec: dict) -> str | None:
    if entity_type == "ad" and rec.get("external_id"):
        return f"ad:{rec.get('source')}:{rec['external_id']}"
    return None


def store_raw(db: Session, entity_type: str, records: list[dict], connector_id: int | None = None) -> int:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")
    n = 0
    for rec in records:
        db.add(RawRecord(connector_id=connector_id, entity_type=entity_type, payload=rec,
                         dedupe_key=_dedupe_key(entity_type, rec)))
        n += 1
    db.flush()
    return n


# ------------------------------------------------------------ Parsing helpers
def _f(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return default


def _i(v, default=0):
    x = _f(v)
    return int(x) if x is not None else default


def _b(v, default=False):
    if v in (None, ""):
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "active")


def _dt(v, default=None) -> datetime | None:
    if not v:
        return default
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return default


def _product_by_ref(db: Session, rec: dict, create: bool = True, source: str | None = None) -> Product | None:
    if rec.get("product_code"):
        p = db.scalar(select(Product).where(Product.product_code == rec["product_code"]))
        if p:
            return p
    if rec.get("product_id"):
        p = db.get(Product, _i(rec["product_id"]))
        if p:
            return p
    name = rec.get("product_name")
    if not name:
        return None
    if not create:
        p, _ = None, 0
        from .entity_resolution import find_best_match, MATCH_THRESHOLD
        p, s = find_best_match(db, {"name": name})
        return p if s >= MATCH_THRESHOLD else None
    p, _, _ = resolve_product(db, {
        "name": name, "price": _f(rec.get("price")), "currency": rec.get("currency"),
        "category": rec.get("category") or E.classify_category(name), "country": (rec.get("country") or "").upper() or None,
    }, source=source)
    return p


def _advertiser(db: Session, name: str | None, platform: str | None, country: str | None, url: str | None) -> Advertiser | None:
    if not name:
        return None
    adv = db.scalar(select(Advertiser).where(Advertiser.name == name, Advertiser.platform == platform))
    if not adv:
        adv = Advertiser(name=name, platform=platform, country=country, page_url=url)
        db.add(adv)
        db.flush()
    return adv


def _store(db: Session, landing_url: str | None, product: Product, advertiser: Advertiser | None, country, price, seen) -> Store | None:
    if not landing_url:
        return None
    domain = urlparse(landing_url).netloc.lower().removeprefix("www.")
    if not domain:
        return None
    st = db.scalar(select(Store).where(Store.domain == domain, Store.product_id == product.id))
    if not st:
        st = Store(domain=domain, product_id=product.id, advertiser_id=advertiser.id if advertiser else None,
                   country=country, price=price, first_seen_at=seen or datetime.utcnow())
        db.add(st)
        db.flush()
    return st


# ------------------------------------------------------------ Normalizers
def _norm_ad(db: Session, rec: dict, source_hint: str | None):
    source = rec.get("source") or source_hint or "unknown"
    ext_id = str(rec.get("external_id") or hashlib.sha1(f"{rec.get('ad_text')}{rec.get('landing_url')}".encode()).hexdigest()[:16])
    now = datetime.utcnow()
    existing = db.scalar(select(Ad).where(Ad.source == source, Ad.external_id == ext_id))
    if existing:  # dedupe → refresh activity & engagement
        existing.last_seen_at = max(existing.last_seen_at, _dt(rec.get("last_seen"), now))
        existing.is_active = _b(rec.get("is_active"), True)
        existing.likes = max(existing.likes, _i(rec.get("likes")))
        existing.comments_count = max(existing.comments_count, _i(rec.get("comments_count")))
        existing.shares = max(existing.shares, _i(rec.get("shares")))
        return existing
    country = (rec.get("country") or "").upper() or None
    name = rec.get("product_name") or (rec.get("ad_text") or "")[:60]
    price = _f(rec.get("price"))
    category = rec.get("category") or E.classify_category(name, rec.get("ad_text"))
    product = _product_by_ref(db, {**rec, "product_name": name, "category": category, "country": country}, source=source)
    platform = rec.get("platform") or "meta"
    adv = _advertiser(db, rec.get("advertiser"), platform, country, rec.get("advertiser_url"))
    first = _dt(rec.get("first_seen"), now)
    st = _store(db, rec.get("landing_url"), product, adv, country, price, first)
    cr = E.classify_creative(rec.get("ad_text"))
    mk = E.classify_market(country, platform, category, price, rec.get("currency"), rec.get("ad_text"))
    if not product.currency and mk["currency"]:
        product.currency = mk["currency"]
    if not product.language and mk["language"]:
        product.language = mk["language"]
    ad = Ad(
        external_id=ext_id, source=source, platform=platform, product_id=product.id,
        advertiser_id=adv.id if adv else None, store_id=st.id if st else None, country=country,
        language=rec.get("language") or mk["language"], raw_product_name=name, ad_text=rec.get("ad_text"),
        landing_url=rec.get("landing_url"), media_type=rec.get("media_type"), media_url=rec.get("media_url"),
        price=price, likes=_i(rec.get("likes")), comments_count=_i(rec.get("comments_count")),
        shares=_i(rec.get("shares")), first_seen_at=first, last_seen_at=_dt(rec.get("last_seen"), now),
        is_active=_b(rec.get("is_active"), True), is_internal=_b(rec.get("is_internal")),
        hook=cr["hook"], angle=cr["angle"], offer=cr["offer"],
        creative_fingerprint=E.creative_fingerprint(rec.get("ad_text"), rec.get("media_url")),
        rejected=_b(rec.get("rejected")), rejection_reason=rec.get("rejection_reason"),
    )
    db.add(ad)
    if first < product.first_seen_at:
        product.first_seen_at = first
    return ad


def _norm_store(db: Session, rec: dict):
    product = _product_by_ref(db, rec)
    domain = (rec.get("domain") or "").lower().removeprefix("www.")
    st = db.scalar(select(Store).where(Store.domain == domain, Store.product_id == product.id))
    if not st:
        st = Store(domain=domain, product_id=product.id, country=(rec.get("country") or "").upper() or None)
        db.add(st)
    st.price = _f(rec.get("price"), st.price)
    st.estimated_traffic = _f(rec.get("estimated_traffic"), st.estimated_traffic or 0)
    st.traffic_growth = _f(rec.get("traffic_growth"), st.traffic_growth or 0)


def _norm_signal(db: Session, rec: dict):
    product = _product_by_ref(db, rec, create=False)
    if not product:
        return
    if rec.get("search_trend") not in (None, ""):
        product.search_trend = _f(rec["search_trend"])
    if rec.get("keyword_competition") not in (None, ""):
        kd = _f(rec["keyword_competition"])
        product.keyword_competition = kd / 100 if kd > 1 else kd


def normalize_pending(db: Session, limit: int = 20000) -> dict:
    """Raw lake → normalized tables, with dedup, entity resolution and AI enrichment."""
    raws = db.scalars(select(RawRecord).where(RawRecord.processed.is_(False)).order_by(RawRecord.id).limit(limit)).all()
    stats = {t: 0 for t in ENTITY_TYPES} | {"errors": 0}
    connectors = {c.id: c.provider for c in db.scalars(select(Connector)).all()}
    # process order matters: ads/stores create products before signals reference them
    order = {"ad": 0, "store": 1, "product_signal": 2}
    for r in sorted(raws, key=lambda r: (order.get(r.entity_type, 9), r.id)):
        try:
            with db.begin_nested():
                rec = r.payload
                if r.entity_type == "ad":
                    _norm_ad(db, rec, connectors.get(r.connector_id))
                elif r.entity_type == "store":
                    _norm_store(db, rec)
                elif r.entity_type == "product_signal":
                    _norm_signal(db, rec)
                db.flush()
            stats[r.entity_type] = stats.get(r.entity_type, 0) + 1  # legacy raw rows (order/comment/…) count, never normalize
            r.processed = True
        except Exception as ex:  # keep raw row for inspection
            r.processed, r.error = True, f"{type(ex).__name__}: {ex}"
            stats["errors"] += 1
    db.flush()
    return stats


def enrich_comments(db: Session, comments: list[Comment] | None = None) -> tuple[int, str]:
    comments = comments if comments is not None else db.scalars(select(Comment).where(Comment.overall.is_(None))).all()
    if not comments:
        return 0, "-"
    results, engine = E.analyze_comments([c.text for c in comments])
    for c, r in zip(comments, results):
        c.overall, c.aspects, c.purchase_intent, c.is_question = r["overall"], r["aspects"], r["purchase_intent"], r["is_question"]
        c.analyzed_by = engine
    db.flush()
    return len(comments), engine


# ------------------------------------------------------------ CSV
def parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k} for row in csv.DictReader(io.StringIO(text))]


CSV_TEMPLATES = {
    "ad": "external_id,source,platform,advertiser,product_name,ad_text,landing_url,country,media_type,media_url,price,currency,likes,comments_count,shares,first_seen,last_seen,is_active,category",
    "store": "domain,product_code,product_name,country,price,estimated_traffic,traffic_growth",
    "product_signal": "product_code,product_name,search_trend,keyword_competition",
}


def manual_url_record(url: str, product_name: str, country: str | None, price: float | None, advertiser: str | None,
                      ad_text: str | None) -> dict:
    return {
        "external_id": hashlib.sha1(url.encode()).hexdigest()[:16], "source": "manual", "platform": "meta",
        "advertiser": advertiser or urlparse(url).netloc, "product_name": product_name, "ad_text": ad_text or product_name,
        "landing_url": url, "country": country, "price": price, "first_seen": date.today().isoformat(),
        "last_seen": datetime.utcnow().isoformat(), "is_active": True,
    }
