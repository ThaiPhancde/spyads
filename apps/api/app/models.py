"""Data model — Product is the central entity (blueprint §4, §6)."""
from datetime import date, datetime

from sqlalchemy import and_, or_, JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .platforms import REMOVED_NETWORKS


def now():
    return datetime.utcnow()


# ---------------------------------------------------------------- Product (§6)
class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # PRD_0001842
    canonical_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64))
    subcategory: Mapped[str | None] = mapped_column(String(64))
    brand: Mapped[str | None] = mapped_column(String(128))
    # unused since the demo seed was removed, but existing DBs have it NOT NULL: without it every new product INSERT failed
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    image_url: Mapped[str | None] = mapped_column(String(512))

    market: Mapped[str | None] = mapped_column(String(64))  # primary country
    country: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))

    price: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    search_trend: Mapped[float] = mapped_column(Float, default=0)  # % growth of search interest
    keyword_competition: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1 (Semrush KD)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    lifecycle_status: Mapped[str] = mapped_column(String(16), default="DISCOVERED")

    # Denormalized features & scores (written by scoring engine)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    external_win_score: Mapped[float] = mapped_column(Float, default=0)
    internal_win_score: Mapped[float | None] = mapped_column(Float)
    win_score: Mapped[float] = mapped_column(Float, default=0)
    rarity_score: Mapped[float] = mapped_column(Float, default=0)
    rare_winner_score: Mapped[float] = mapped_column(Float, default=0)
    saturation_score: Mapped[float] = mapped_column(Float, default=0)
    saturation_state: Mapped[str | None] = mapped_column(String(32))
    opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    customer_rejection_score: Mapped[float | None] = mapped_column(Float)
    ad_rejection_rate: Mapped[float | None] = mapped_column(Float)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str | None] = mapped_column(String(16))
    recommendation_reasons: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Product Potential Vector (discovery §7) + company fit (realtime §19)
    potential: Mapped[dict] = mapped_column(JSON, default=dict)
    market_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # per-market vector + decision
    classification: Mapped[str | None] = mapped_column(String(32))  # BREAKOUT / EXPERIMENTAL / STABLE / SKIP
    novelty_score: Mapped[float | None] = mapped_column(Float)
    wave_score: Mapped[float | None] = mapped_column(Float)
    creative_potential: Mapped[float | None] = mapped_column(Float)
    mkt_appeal: Mapped[float | None] = mapped_column(Float)
    compliance_risk: Mapped[float | None] = mapped_column(Float)
    company_fit_score: Mapped[float | None] = mapped_column(Float)
    funnel_mix: Mapped[dict] = mapped_column(JSON, default=dict)  # {"mess": n, "ladi": n, ...}
    cover_creative_id: Mapped[int | None] = mapped_column(Integer)
    in_target: Mapped[bool | None] = mapped_column(Boolean, index=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)

    aliases: Mapped[list["ProductAlias"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductAlias(Base):
    """Every raw name a product was seen under — output of entity resolution (§5)."""
    __tablename__ = "product_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(64))
    match_score: Mapped[float] = mapped_column(Float, default=1.0)
    product: Mapped[Product] = relationship(back_populates="aliases")


class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- Ads side
class Advertiser(Base):
    __tablename__ = "advertisers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    platform: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(64))
    page_url: Mapped[str | None] = mapped_column(String(512))
    is_competitor_watched: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    advertiser_id: Mapped[int | None] = mapped_column(ForeignKey("advertisers.id"))
    country: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[float | None] = mapped_column(Float)
    estimated_traffic: Mapped[float] = mapped_column(Float, default=0)
    traffic_growth: Mapped[float] = mapped_column(Float, default=0)  # % (Similarweb)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Ad(Base):
    __tablename__ = "ads"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(64))  # minea / meta_ad_library / internal ...
    platform: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    advertiser_id: Mapped[int | None] = mapped_column(ForeignKey("advertisers.id"), index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"))
    country: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))
    raw_product_name: Mapped[str | None] = mapped_column(String(255))
    ad_text: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(String(512))
    media_type: Mapped[str | None] = mapped_column(String(16))  # video / image / carousel
    media_url: Mapped[str | None] = mapped_column(String(512))
    price: Mapped[float | None] = mapped_column(Float)
    price_source: Mapped[str | None] = mapped_column(String(16))  # source (adapter) / ad_text (regex) / landing (fetched page)
    landing_checked_at: Mapped[datetime | None] = mapped_column(DateTime)  # services/spy.py: last landing-page fetch
    reviews_checked_at: Mapped[datetime | None] = mapped_column(DateTime)  # services/spy.py: last marketplace review fetch
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    # AI enrichment (§20)
    hook: Mapped[str | None] = mapped_column(String(64))
    angle: Mapped[str | None] = mapped_column(String(64))
    offer: Mapped[str | None] = mapped_column(String(128))
    creative_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    # Ad rejection (§15)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    # Common Data Contract extras
    page_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    cta_type: Mapped[str | None] = mapped_column(String(48))
    cta_text: Mapped[str | None] = mapped_column(String(128))
    funnel: Mapped[str | None] = mapped_column(String(16), index=True)  # mess | ladi | form | shop | app | other
    variants: Mapped[int | None] = mapped_column(Integer)  # Meta collation_count
    views: Mapped[int | None] = mapped_column(Integer)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_url: Mapped[str | None] = mapped_column(String(512))
    saved_by: Mapped[str | None] = mapped_column(String(128))
    search_text: Mapped[str | None] = mapped_column(Text)  # lower-cased text for keyword search
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    in_target: Mapped[bool | None] = mapped_column(Boolean, index=True)  # reached a TARGET_MARKETS country
    # liveness (blueprint §18): verified against the source, never inferred from silence
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    inactive_at: Mapped[datetime | None] = mapped_column(DateTime)
    reactivated_at: Mapped[datetime | None] = mapped_column(DateTime)
    verify_misses: Mapped[int | None] = mapped_column(Integer, default=0)  # consecutive liveness checks not seeing it
    # per-ad strength (sonda-imperial "índice de força"): days running, variants, placements
    force_score: Mapped[float | None] = mapped_column(Float, index=True)
    force_tier: Mapped[str | None] = mapped_column(String(12))  # legendary / strong / regular / testing
    variation_key: Mapped[str | None] = mapped_column(String(24), index=True)  # page + normalized copy
    # extra Ad Library fields (mostly filled for EU / political ads)
    impressions_text: Mapped[str | None] = mapped_column(String(64))
    spend_text: Mapped[str | None] = mapped_column(String(64))
    reach: Mapped[int | None] = mapped_column(Integer)
    page_likes: Mapped[int | None] = mapped_column(Integer)
    # multi-platform (platforms.py): network = meta / tiktok / snapchat / aliexpress / 1688 / taobao …; channel = ads | commerce | organic
    network: Mapped[str | None] = mapped_column(String(24), index=True)
    channel: Mapped[str | None] = mapped_column(String(12), index=True)
    # commerce listings (China source / competitor stores)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    sold_count: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)  # search position at the source
    original_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))  # of price / original_price, as the source reported it


# market aggregates (ad counts, advertisers, new ads…) read ad-library rows only — never marketplace listings or
# viral posts, which share the `ads` table for product matching (platforms.py). NULL = rows from before channels.
# ponytail: REMOVED_NETWORKS rows (google/amazon/… , purge_removed.py) are still in the DB — keep them out of every aggregate
ADS_ONLY = and_(or_(Ad.channel == "ads", Ad.channel.is_(None)), or_(Ad.network.is_(None), Ad.network.not_in(REMOVED_NETWORKS)))


def ad_markets(a) -> list[str]:
    """All markets an ad was seen in (an ad often runs in several countries)."""
    out = [c for c in (a.countries or []) if c]
    if a.country and a.country not in out:
        out.insert(0, a.country)
    return out


def ad_in_country(code: str):
    """SQL filter: ad reached `code` (primary country or any of its markets)."""
    from sqlalchemy import String, cast, or_

    code = code.upper()
    return or_(Ad.country == code, cast(Ad.countries, String).like(f'%"{code}"%'))


class AdSource(Base):
    """Source provenance (discovery §5): every provider that reported this ad, and when."""
    __tablename__ = "ad_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_ad_id: Mapped[str] = mapped_column(String(128), index=True)
    platform: Mapped[str | None] = mapped_column(String(32))
    raw_key: Mapped[str | None] = mapped_column(String(255))  # raw batch file in storage
    saved_by: Mapped[str | None] = mapped_column(String(128))
    first_collected_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_collected_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Creative(Base):
    """Creative Vault (discovery §4): one stored media asset (video/image), content-addressed by sha256."""
    __tablename__ = "creatives"
    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("ads.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    type: Mapped[str] = mapped_column(String(8))  # video | image
    position: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(64))
    source_platform: Mapped[str | None] = mapped_column(String(32))
    network: Mapped[str | None] = mapped_column(String(24), index=True)  # platforms.network_of → storage budget per network
    source_ad_id: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str] = mapped_column(Text)  # original CDN url (expires)
    preview_source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/stored/failed/expired
    error: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(255))
    thumb_key: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    phash: Mapped[str | None] = mapped_column(String(16), index=True)  # perceptual hash of poster/image
    mime: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    family_id: Mapped[int | None] = mapped_column(Integer, index=True)  # creative family (discovery §6)
    transcript: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    stored_at: Mapped[datetime | None] = mapped_column(DateTime)
    pinned: Mapped[bool | None] = mapped_column(Boolean, default=False)  # never auto-archive
    # Video on Demand (HLS adaptive ladder) — see app/hls.py
    hls_key: Mapped[str | None] = mapped_column(String(255))  # hls/<sha>/master.m3u8
    download_key: Mapped[str | None] = mapped_column(String(255))  # top rendition (playable fMP4) for "download"
    renditions: Mapped[list | None] = mapped_column(JSON)  # ["360p", "540p"]
    stored_bytes: Mapped[int | None] = mapped_column(Integer)  # bytes this asset occupies in storage
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)


class TrackedQuery(Base):
    """Saved product searches the scheduler re-runs (Tier 2: 15-60 min)."""
    __tablename__ = "tracked_queries"
    id: Mapped[int] = mapped_column(primary_key=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id"))
    query: Mapped[str | None] = mapped_column(String(255))
    page_ids: Mapped[list] = mapped_column(JSON, default=list)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    media_type: Mapped[str | None] = mapped_column(String(8))
    limit: Mapped[int] = mapped_column(Integer, default=60)
    every_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(128))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    last_new: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class SearchJob(Base):
    """Live search started from the Product Search page."""
    __tablename__ = "search_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str | None] = mapped_column(String(255))
    countries: Mapped[list] = mapped_column(JSON, default=list)
    adapters: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    found: Mapped[int] = mapped_column(Integer, default=0)
    new_ads: Mapped[int] = mapped_column(Integer, default=0)
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    limit: Mapped[int] = mapped_column(Integer, default=100)
    media_type: Mapped[str | None] = mapped_column(String(8))
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # per connector+country resume cursors
    has_more: Mapped[bool] = mapped_column(Boolean, default=False)
    # per source: {name: {"network", "fetched", "new", "failed", "error"}} — "found" alone mixed re-seen ads, duplicates
    # and rows that never reached the library, so 284 "found" could show as 22 products
    sources: Mapped[dict | None] = mapped_column(JSON)


class Vote(Base):
    """MKT Appeal votes (discovery §11): LOVE / TEST / WATCH / NORMAL / SKIP."""
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    user: Mapped[str] = mapped_column(String(128))
    team: Mapped[str | None] = mapped_column(String(16))  # mess | ladi
    market: Mapped[str | None] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(8))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("ads.id"))
    source: Mapped[str] = mapped_column(String(32), default="ad_comment")  # ad_comment/review/landing_review/inbox/call
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)  # "ali:<evaluationId>" / "lp:<hash>" — dedupe
    rating: Mapped[float | None] = mapped_column(Float)  # stars 1-5 when the source has them (ground truth for `overall`)
    country: Mapped[str | None] = mapped_column(String(8))
    # Aspect-based sentiment (§17)
    overall: Mapped[str | None] = mapped_column(String(16))
    aspects: Mapped[dict] = mapped_column(JSON, default=dict)
    purchase_intent: Mapped[str | None] = mapped_column(String(16))
    is_question: Mapped[bool] = mapped_column(Boolean, default=False)
    analyzed_by: Mapped[str | None] = mapped_column(String(16))


# ---------------------------------------------------------------- Internal (§12, §16)
class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    market: Mapped[str | None] = mapped_column(String(64))
    platform: Mapped[str | None] = mapped_column(String(32))
    creative: Mapped[str | None] = mapped_column(String(255))
    creative_type: Mapped[str | None] = mapped_column(String(64))  # UGC / Studio / Static
    angle: Mapped[str | None] = mapped_column(String(128))
    offer: Mapped[str | None] = mapped_column(String(128))
    funnel: Mapped[str | None] = mapped_column(String(32))
    sell_price: Mapped[float | None] = mapped_column(Float)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[date] = mapped_column(Date, default=date.today)
    ended_at: Mapped[date | None] = mapped_column(Date)

    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    landing_views: Mapped[int] = mapped_column(Integer, default=0)
    atc: Mapped[int] = mapped_column(Integer, default=0)
    checkout: Mapped[int] = mapped_column(Integer, default=0)
    purchase: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    confirmed_orders: Mapped[int] = mapped_column(Integer, default=0)
    shipped: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[int] = mapped_column(Integer, default=0)
    returned: Mapped[int] = mapped_column(Integer, default=0)
    ads_submitted: Mapped[int] = mapped_column(Integer, default=0)
    ads_rejected: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING/WIN/PROMISING/FAILED/WAITING
    failure_types: Mapped[list] = mapped_column(JSON, default=list)
    diagnosis: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"))
    country: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="crm")  # crm/pancake/shopify
    amount: Mapped[float] = mapped_column(Float, default=0)
    cogs: Mapped[float] = mapped_column(Float, default=0)
    shipping_cost: Mapped[float] = mapped_column(Float, default=0)
    customer_phone_hash: Mapped[str | None] = mapped_column(String(64))
    # pending → confirmed/cancelled → shipped → delivered/refused/failed → returned
    status: Mapped[str] = mapped_column(String(16), default="pending")
    refusal_reason_raw: Mapped[str | None] = mapped_column(Text)  # call-center note
    refusal_reason: Mapped[str | None] = mapped_column(String(64))  # classified (§16.1)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # Full lifecycle (realtime §11-14): NEW CONTACTING CONFIRMED PACKED SHIPPED IN_TRANSIT OUT_FOR_DELIVERY
    # DELIVERED FAILED REFUSED RETURNED + CANCELLED DUPLICATE FAKE_ORDER UNREACHABLE REFUNDED
    stage: Mapped[str | None] = mapped_column(String(24))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    carrier: Mapped[str | None] = mapped_column(String(48))
    carrier_status_raw: Mapped[str | None] = mapped_column(String(128))
    tracking_code: Mapped[str | None] = mapped_column(String(64), index=True)
    cod_fee: Mapped[float] = mapped_column(Float, default=0)
    sales_commission: Mapped[float] = mapped_column(Float, default=0)
    return_cost: Mapped[float] = mapped_column(Float, default=0)
    payment_fee: Mapped[float] = mapped_column(Float, default=0)
    # Attribution chain (realtime §17)
    campaign_id: Mapped[str | None] = mapped_column(String(64), index=True)
    adset_id: Mapped[str | None] = mapped_column(String(64))
    ad_external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    creative_ref: Mapped[str | None] = mapped_column(String(128))
    lead_id: Mapped[str | None] = mapped_column(String(64))
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    sales_agent: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- Snapshots (§18)
class ProductDailySnapshot(Base):
    __tablename__ = "product_daily_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    active_ads: Mapped[int] = mapped_column(Integer, default=0)
    new_ads: Mapped[int] = mapped_column(Integer, default=0)
    removed_ads: Mapped[int] = mapped_column(Integer, default=0)
    advertisers: Mapped[int] = mapped_column(Integer, default=0)
    new_advertisers: Mapped[int] = mapped_column(Integer, default=0)
    markets: Mapped[int] = mapped_column(Integer, default=0)
    new_markets: Mapped[int] = mapped_column(Integer, default=0)
    stores: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    positive_comments: Mapped[int] = mapped_column(Integer, default=0)
    negative_comments: Mapped[int] = mapped_column(Integer, default=0)
    traffic: Mapped[float] = mapped_column(Float, default=0)
    avg_price: Mapped[float | None] = mapped_column(Float)
    external_win_score: Mapped[float] = mapped_column(Float, default=0)
    internal_win_score: Mapped[float | None] = mapped_column(Float)
    rarity_score: Mapped[float] = mapped_column(Float, default=0)
    saturation_score: Mapped[float] = mapped_column(Float, default=0)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0)
    refusal_rate: Mapped[float | None] = mapped_column(Float)


class MarketDailySnapshot(Base):
    __tablename__ = "market_daily_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64))
    date: Mapped[date] = mapped_column(Date, index=True)
    products: Mapped[int] = mapped_column(Integer, default=0)
    new_products: Mapped[int] = mapped_column(Integer, default=0)
    active_ads: Mapped[int] = mapped_column(Integer, default=0)
    new_ads: Mapped[int] = mapped_column(Integer, default=0)
    advertisers: Mapped[int] = mapped_column(Integer, default=0)
    potential_winners: Mapped[int] = mapped_column(Integer, default=0)
    hidden_winners: Mapped[int] = mapped_column(Integer, default=0)
    avg_opportunity: Mapped[float] = mapped_column(Float, default=0)


class AdvertiserDailySnapshot(Base):
    __tablename__ = "advertiser_daily_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    active_ads: Mapped[int] = mapped_column(Integer, default=0)
    new_ads: Mapped[int] = mapped_column(Integer, default=0)
    products: Mapped[int] = mapped_column(Integer, default=0)
    countries: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- Alerts & Connectors
class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="info")  # info/warn/critical
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    advertiser_id: Mapped[int | None] = mapped_column(ForeignKey("advertisers.id"))
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))  # minea, meta_ad_library, csv ...
    kind: Mapped[str] = mapped_column(String(32))  # official_api/paid_provider_api/first_party_account/webhook/csv_import/manual_url
    group: Mapped[str] = mapped_column(String(32))  # ad_intel/transparency/web_intel/internal_ads/business/feedback
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="not_configured")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    adapter: Mapped[str | None] = mapped_column(String(32))  # ConnectorFactory key
    tier: Mapped[int] = mapped_column(Integer, default=2)  # realtime tier 0-3
    every_minutes: Mapped[int | None] = mapped_column(Integer)  # scheduler interval
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    health: Mapped[str] = mapped_column(String(16), default="unknown")  # healthy/slow/auth_expired/error/unknown


class RawRecord(Base):
    """Raw data lake (§2): payload exactly as received, before normalization."""
    __tablename__ = "raw_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    connector_id: Mapped[int | None] = mapped_column(ForeignKey("connectors.id"))
    entity_type: Mapped[str] = mapped_column(String(32))  # ad/order/comment/store/experiment
    payload: Mapped[dict] = mapped_column(JSON)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")


class AdMetric(Base):
    """Own ad-account performance per ad per day (Tier 1: Meta / TikTok ad-account APIs)."""
    __tablename__ = "ad_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), default="meta")
    account_id: Mapped[str | None] = mapped_column(String(64))
    campaign_id: Mapped[str | None] = mapped_column(String(64), index=True)
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    adset_id: Mapped[str | None] = mapped_column(String(64))
    ad_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_name: Mapped[str | None] = mapped_column(String(255))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)  # messaging conversations / leads
    purchases: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
