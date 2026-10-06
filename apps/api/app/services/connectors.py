"""Connector Layer (blueprint §3) + Normalization / Dedup / Entity resolution hand-off.

Flow: adapter.fetch() → RawRecord (data lake) → normalize_pending() → domain tables.

Canonical record shapes (what every adapter / CSV / webhook must produce):
  ad:          external_id, source, platform, advertiser, product_name, ad_text, landing_url, country,
               media_type, media_url, price, currency, likes, comments_count, shares, first_seen, last_seen,
               is_active, category, brand, is_internal, rejected, rejection_reason
  comment:     text, product_code | product_name | ad_external_id, source, created_at
  order:       external_id, product_code | product_name, experiment_id, country, amount, cogs,
               shipping_cost, status, refusal_note, refunded, phone, created_at, source
  store:       domain, product_code | product_name, country, price, estimated_traffic, traffic_growth
  experiment:  product_code | product_name, name, market, platform, creative, creative_type, angle, offer,
               funnel, sell_price, unit_cost, started_at, ended_at, spend, impressions, clicks,
               landing_views, atc, checkout, purchase, revenue, confirmed_orders, shipped, delivered,
               refused, returned, ads_submitted, ads_rejected
  product_signal: product_code | product_name, search_trend, keyword_competition   (Semrush / Google Trends)
"""
import csv
import hashlib
import io
import random
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ad, Advertiser, Comment, Connector, Experiment, Order, Product, RawRecord, Store
from . import enrichment as E
from .entity_resolution import resolve_product

ENTITY_TYPES = ["ad", "comment", "order", "store", "experiment", "product_signal"]

# ------------------------------------------------------------ Provider catalogue (§3.1)
PROVIDERS = {
    # group: ad_intel
    "minea": ("Minea", "paid_provider_api", "ad_intel"),
    "foreplay": ("Foreplay", "paid_provider_api", "ad_intel"),
    "bigspy": ("BigSpy", "paid_provider_api", "ad_intel"),
    "pipiads": ("PiPiAds", "paid_provider_api", "ad_intel"),
    "demo_spy": ("Demo Spy Feed (simulated)", "paid_provider_api", "ad_intel"),
    # transparency
    "meta_ad_library": ("Meta Ad Library", "official_api", "transparency"),
    "tiktok_creative_center": ("TikTok Creative Center", "official_api", "transparency"),
    # web intel
    "similarweb": ("Similarweb", "paid_provider_api", "web_intel"),
    "semrush": ("Semrush", "paid_provider_api", "web_intel"),
    # internal ads
    "meta_ads": ("Meta Ads (own account)", "first_party_account", "internal_ads"),
    "tiktok_ads": ("TikTok Ads (own account)", "first_party_account", "internal_ads"),
    "google_ads": ("Google Ads (own account)", "first_party_account", "internal_ads"),
    # business
    "crm": ("CRM / Orders", "webhook", "business"),
    "pancake": ("Pancake POS", "webhook", "business"),
    "shipping": ("COD / Shipping carrier", "webhook", "business"),
    # feedback
    "comments": ("Comments / Reviews / Inbox", "webhook", "feedback"),
    # generic
    "generic_rest": ("Generic REST API (field mapping)", "paid_provider_api", "ad_intel"),
    "csv": ("CSV Import", "csv_import", "business"),
    "manual": ("Manual URL import", "manual_url", "ad_intel"),
}


# Default connectors (Unified Collector). Only the public Meta Ad Library works without credentials;
# the others are templates the admin fills in (Data & Connectors screen). No simulated sources.
DEFAULT_CONNECTORS = [
    # adapter, name, enabled, every_minutes, tier, config
    ("meta_library", "Meta Ad Library (public) — tìm kiếm & theo dõi", True, None, 2, {"countries": "SA,AE,VN", "max_per_query": 60, "search_limit": 40}),
    ("meta_graph", "Meta Ad Library API (official, EU/UK)", False, 360, 2, {}),
    ("apify_meta", "Apify · Facebook Ads Library (production volume)", False, 60, 2, {}),
    ("meta_ads", "Meta Ads — tài khoản công ty (spend/CPA realtime)", False, 5, 1, {}),
    ("tiktok_commercial", "TikTok Commercial Content API (official, EU)", False, 360, 2, {}),
    ("apify_actor", "Apify · TikTok ads / Creative Center", False, 60, 2, {"source": "tiktok"}),
    ("http", "Pipiads API (Enterprise)", False, 60, 2, {"source": "pipiads"}),
    ("export", "Export · pipiads", True, 5, 2, {"source": "pipiads"}),
    ("export", "Export · minea", True, 5, 2, {"source": "minea"}),
]


def ensure_default_connectors(db: Session):
    if db.scalar(select(Connector.id).where(Connector.adapter.is_not(None)).limit(1)):
        return
    from ..collector.factory import ConnectorFactory

    for adapter, name, enabled, every, tier, cfg in DEFAULT_CONNECTORS:
        cls = ConnectorFactory.connectors[adapter]
        db.add(Connector(name=name, provider=adapter, adapter=adapter, kind=cls.kind, group=cls.group, config=cfg,
                         enabled=enabled, every_minutes=every, tier=tier,
                         status="ready" if adapter in ("meta_library", "export") else "not_configured"))
    db.flush()


# ------------------------------------------------------------ Raw lake
def _dedupe_key(entity_type: str, rec: dict) -> str | None:
    if entity_type == "ad" and rec.get("external_id"):
        return f"ad:{rec.get('source')}:{rec['external_id']}"
    if entity_type == "order" and rec.get("external_id"):
        return f"order:{rec.get('source', 'crm')}:{rec['external_id']}"
    if entity_type == "comment" and rec.get("text"):
        h = rec.get("external_id") or hashlib.sha1(f"{rec.get('product_code') or rec.get('product_name') or rec.get('ad_external_id')}|{rec['text']}|{rec.get('created_at')}".encode()).hexdigest()[:20]
        return f"comment:{h}"
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


def _norm_comment(db: Session, rec: dict, pending_comments: list):
    product = None
    ad_id = None
    if rec.get("ad_external_id"):
        ad = db.scalar(select(Ad).where(Ad.external_id == str(rec["ad_external_id"])))
        if ad:
            product, ad_id = db.get(Product, ad.product_id), ad.id
    product = product or _product_by_ref(db, rec, create=False)
    c = Comment(product_id=product.id if product else None, ad_id=ad_id, source=rec.get("source") or "ad_comment",
                text=rec["text"], created_at=_dt(rec.get("created_at"), datetime.utcnow()))
    db.add(c)
    pending_comments.append(c)


ORDER_STATUS_MAP = {
    "new": "pending", "pending": "pending", "chờ xác nhận": "pending", "confirmed": "confirmed", "đã xác nhận": "confirmed",
    "cancelled": "cancelled", "canceled": "cancelled", "hủy": "cancelled", "shipped": "shipped", "đang giao": "shipped",
    "in_transit": "shipped", "delivered": "delivered", "đã giao": "delivered", "thành công": "delivered",
    "refused": "refused", "từ chối": "refused", "rejected": "refused", "hoàn": "returned", "returned": "returned",
    "failed": "failed", "giao thất bại": "failed",
}


def _norm_order(db: Session, rec: dict, pending_refusals: list):
    src = rec.get("source") or "crm"
    if rec.get("external_id"):
        o = db.scalar(select(Order).where(Order.external_id == str(rec["external_id"]), Order.source == src))
        if o:  # status update from carrier / CRM
            o.status = ORDER_STATUS_MAP.get(str(rec.get("status", o.status)).lower(), o.status)
            if rec.get("refusal_note"):
                o.refusal_reason_raw = rec["refusal_note"]
                pending_refusals.append(o)
            o.refunded = _b(rec.get("refunded"), o.refunded)
            return
    product = _product_by_ref(db, rec)
    if not product:
        raise ValueError("order needs product_code or product_name")
    o = Order(
        external_id=str(rec.get("external_id")) if rec.get("external_id") else None, product_id=product.id,
        experiment_id=_i(rec.get("experiment_id"), None) or None, country=(rec.get("country") or "").upper() or None,
        source=src, amount=_f(rec.get("amount"), 0), cogs=_f(rec.get("cogs"), 0), shipping_cost=_f(rec.get("shipping_cost"), 0),
        customer_phone_hash=hashlib.sha1(str(rec["phone"]).encode()).hexdigest()[:16] if rec.get("phone") else None,
        status=ORDER_STATUS_MAP.get(str(rec.get("status", "pending")).lower(), "pending"),
        refusal_reason_raw=rec.get("refusal_note") or None, refunded=_b(rec.get("refunded")),
        created_at=_dt(rec.get("created_at"), datetime.utcnow()),
    )
    db.add(o)
    if o.refusal_reason_raw:
        pending_refusals.append(o)


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


EXP_INT = ["impressions", "clicks", "landing_views", "atc", "checkout", "purchase", "confirmed_orders", "shipped",
           "delivered", "refused", "returned", "ads_submitted", "ads_rejected"]


def _norm_experiment(db: Session, rec: dict):
    product = _product_by_ref(db, rec)
    e = None
    if rec.get("id"):
        e = db.get(Experiment, _i(rec["id"]))
    if not e and rec.get("name"):
        e = db.scalar(select(Experiment).where(Experiment.name == rec["name"], Experiment.product_id == product.id))
    if not e:
        e = Experiment(product_id=product.id, name=rec.get("name") or f"Test {product.canonical_name}")
        db.add(e)
    for k in ("market", "platform", "creative", "creative_type", "angle", "offer", "funnel", "notes"):
        if rec.get(k):
            setattr(e, k, rec[k])
    for k in ("sell_price", "unit_cost", "spend", "revenue"):
        if rec.get(k) not in (None, ""):
            setattr(e, k, _f(rec[k]))
    for k in EXP_INT:
        if rec.get(k) not in (None, ""):
            setattr(e, k, _i(rec[k]))
    if rec.get("started_at"):
        e.started_at = _dt(rec["started_at"]).date()
    if rec.get("ended_at"):
        e.ended_at = _dt(rec["ended_at"]).date()


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
    stats = {t: 0 for t in ENTITY_TYPES} | {"errors": 0, "duplicates": 0}
    pending_comments: list[Comment] = []
    pending_refusals: list[Order] = []
    seen_keys: set[str] = set()
    connectors = {c.id: c.provider for c in db.scalars(select(Connector)).all()}
    # process order matters: ads/stores/experiments create products before orders/comments reference them
    order = {"ad": 0, "store": 1, "experiment": 2, "product_signal": 3, "order": 4, "comment": 5}
    for r in sorted(raws, key=lambda r: (order.get(r.entity_type, 9), r.id)):
        try:
            if r.dedupe_key and r.entity_type == "comment":
                if r.dedupe_key in seen_keys or db.scalar(select(RawRecord.id).where(
                        RawRecord.dedupe_key == r.dedupe_key, RawRecord.processed.is_(True), RawRecord.error.is_(None))):
                    stats["duplicates"] += 1
                    r.processed = True
                    continue
                seen_keys.add(r.dedupe_key)
            with db.begin_nested():
                rec = r.payload
                if r.entity_type == "ad":
                    _norm_ad(db, rec, connectors.get(r.connector_id))
                elif r.entity_type == "comment":
                    _norm_comment(db, rec, pending_comments)
                elif r.entity_type == "order":
                    _norm_order(db, rec, pending_refusals)
                elif r.entity_type == "store":
                    _norm_store(db, rec)
                elif r.entity_type == "experiment":
                    _norm_experiment(db, rec)
                elif r.entity_type == "product_signal":
                    _norm_signal(db, rec)
                db.flush()
            stats[r.entity_type] += 1
            r.processed = True
        except Exception as ex:  # keep raw row for inspection
            r.processed, r.error = True, f"{type(ex).__name__}: {ex}"
            stats["errors"] += 1
    db.flush()
    stats["enriched_comments"], stats["comment_engine"] = enrich_comments(db, pending_comments)
    stats["classified_refusals"] = classify_order_refusals(db, pending_refusals)
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


def classify_order_refusals(db: Session, orders: list[Order] | None = None) -> int:
    orders = orders if orders is not None else db.scalars(
        select(Order).where(Order.refusal_reason_raw.is_not(None), Order.refusal_reason.is_(None))).all()
    if not orders:
        return 0
    reasons = E.classify_refusals([o.refusal_reason_raw for o in orders])
    for o, r in zip(orders, reasons):
        o.refusal_reason = r
    db.flush()
    return len(orders)


# ------------------------------------------------------------ CSV
def parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k} for row in csv.DictReader(io.StringIO(text))]


CSV_TEMPLATES = {
    "ad": "external_id,source,platform,advertiser,product_name,ad_text,landing_url,country,media_type,media_url,price,currency,likes,comments_count,shares,first_seen,last_seen,is_active,category",
    "comment": "product_code,product_name,ad_external_id,source,text,created_at",
    "order": "external_id,product_code,product_name,experiment_id,country,amount,cogs,shipping_cost,status,refusal_note,refunded,phone,created_at,source",
    "store": "domain,product_code,product_name,country,price,estimated_traffic,traffic_growth",
    "experiment": "product_code,product_name,name,market,platform,creative,creative_type,angle,offer,funnel,sell_price,unit_cost,started_at,ended_at,spend,impressions,clicks,landing_views,atc,checkout,purchase,revenue,confirmed_orders,shipped,delivered,refused,returned,ads_submitted,ads_rejected",
    "product_signal": "product_code,product_name,search_trend,keyword_competition",
}


# ------------------------------------------------------------ Adapters
class ConnectorNotConfigured(Exception):
    pass


def _get_path(obj, path: str):
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            obj = obj[int(part)] if part.isdigit() and int(part) < len(obj) else None
        else:
            obj = obj.get(part)
    return obj


def fetch_meta_ad_library(cfg: dict) -> list[dict]:
    """Official Meta Ad Library API (ads_archive). Needs an access token with ads_read."""
    token = cfg.get("access_token")
    if not token:
        raise ConnectorNotConfigured("Thiếu access_token (Meta Ad Library API).")
    params = {
        "access_token": token,
        "search_terms": cfg.get("search_terms", ""),
        "ad_reached_countries": str(cfg.get("countries", ["SA"])).replace("'", '"'),
        "ad_active_status": "ACTIVE",
        "ad_type": "ALL",
        "fields": "id,page_name,ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,"
                  "ad_delivery_start_time,ad_delivery_stop_time,ad_snapshot_url,publisher_platforms",
        "limit": cfg.get("limit", 100),
    }
    r = httpx.get(f"https://graph.facebook.com/{cfg.get('api_version', 'v21.0')}/ads_archive", params=params, timeout=60)
    r.raise_for_status()
    out = []
    for a in r.json().get("data", []):
        body = (a.get("ad_creative_bodies") or [""])[0]
        title = (a.get("ad_creative_link_titles") or [None])[0]
        out.append({
            "external_id": a["id"], "source": "meta_ad_library", "platform": "meta", "advertiser": a.get("page_name"),
            "product_name": title or body[:60], "ad_text": body, "landing_url": (a.get("ad_creative_link_captions") or [None])[0],
            "country": (cfg.get("countries") or ["SA"])[0], "media_url": a.get("ad_snapshot_url"),
            "first_seen": a.get("ad_delivery_start_time"), "last_seen": a.get("ad_delivery_stop_time") or datetime.utcnow().isoformat(),
            "is_active": not a.get("ad_delivery_stop_time"),
        })
    return out


def fetch_generic_rest(cfg: dict) -> list[dict]:
    """Config: {url, method?, headers?, params?, items_path, entity_type, mapping: {canonical_field: 'dotted.path'}}"""
    if not cfg.get("url") or not cfg.get("mapping"):
        raise ConnectorNotConfigured("Cần cấu hình url + mapping (xem docs connector).")
    r = httpx.request(cfg.get("method", "GET"), cfg["url"], headers=cfg.get("headers"), params=cfg.get("params"),
                      json=cfg.get("body"), timeout=60)
    r.raise_for_status()
    items = _get_path(r.json(), cfg["items_path"]) if cfg.get("items_path") else r.json()
    return [{k: _get_path(it, path) for k, path in cfg["mapping"].items()} for it in (items or [])]


def fetch_demo_spy(db: Session, cfg: dict) -> list[dict]:
    """Simulated spy-tool feed so the whole pipeline can be exercised without paid API keys."""
    from ..seed import simulate_daily_feed

    return simulate_daily_feed(db, random.Random(cfg.get("seed")))


def sync_connector(db: Session, c: Connector) -> int:
    cfg = c.config or {}
    entity_type = cfg.get("entity_type", "ad")
    try:
        if c.provider == "meta_ad_library":
            records = fetch_meta_ad_library(cfg)
        elif c.provider == "demo_spy":
            records = fetch_demo_spy(db, cfg)
        elif cfg.get("url") or c.provider == "generic_rest":
            records = fetch_generic_rest(cfg)
        elif c.kind in ("webhook", "csv_import", "manual_url"):
            raise ConnectorNotConfigured("Connector dạng push — dữ liệu vào qua webhook / CSV / manual import.")
        else:
            raise ConnectorNotConfigured(
                f"{c.name}: thêm API credentials + url/mapping trong config (dùng adapter Generic REST) "
                "hoặc import CSV export của tool.")
        n = store_raw(db, entity_type, records, c.id)
        c.status, c.last_error, c.last_sync_at, c.last_sync_count = "ok", None, datetime.utcnow(), n
        return n
    except ConnectorNotConfigured as ex:
        c.status, c.last_error = "not_configured", str(ex)
        return 0
    except (httpx.HTTPError, ValueError, KeyError) as ex:
        c.status, c.last_error = "error", f"{type(ex).__name__}: {ex}"
        return 0


def manual_url_record(url: str, product_name: str, country: str | None, price: float | None, advertiser: str | None,
                      ad_text: str | None) -> dict:
    return {
        "external_id": hashlib.sha1(url.encode()).hexdigest()[:16], "source": "manual", "platform": "meta",
        "advertiser": advertiser or urlparse(url).netloc, "product_name": product_name, "ad_text": ad_text or product_name,
        "landing_url": url, "country": country, "price": price, "first_seen": date.today().isoformat(),
        "last_seen": datetime.utcnow().isoformat(), "is_active": True,
    }
