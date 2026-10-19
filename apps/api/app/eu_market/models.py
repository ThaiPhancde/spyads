"""EU Market tables (docs/SpyAds_EU_Market_Implementation.md §6).

Kept apart from the ad-spy tables on purpose: a marketplace listing / supplier offer is never an ad. Every observed
row carries source + observed_at + market_country + data_access (provenance); nothing here stores sales, revenue
or conversion numbers — the sources we use don't publish them.
Created by db.auto_migrate (imported from app.models), additive only.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def now():
    return datetime.utcnow()


class EUProduct(Base):
    """One real-world product, grouping the listings / supplier offers that sell it (EAN first, else exact title)."""
    __tablename__ = "eu_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_title: Mapped[str] = mapped_column(String(512))
    title_key: Mapped[str | None] = mapped_column(String(255), index=True)  # normalize.title_key — no-EAN grouping
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    ean: Mapped[str | None] = mapped_column(String(32), index=True)  # GTIN-13 (GTIN-12 zero-padded)
    image_url: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    marketer_tags: Mapped[list] = mapped_column(JSON, default=list)  # Rare Finds manual flags (rarity.MANUAL_TAGS)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)  # banned / counterfeit / dangerous → never suggested
    excluded_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class EUSupplier(Base):
    __tablename__ = "eu_suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    # manufacturer | distributor | wholesale_marketplace | dropshipper | liquidation
    supplier_type: Mapped[str] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(8))  # ships-from country
    website: Mapped[str | None] = mapped_column(String(512))
    moq: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[str | None] = mapped_column(String(32))
    vat_note: Mapped[str | None] = mapped_column(String(255))  # e.g. "wholesale prices exclude VAT"
    access_terms: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("source", "name"),)


class EUListing(Base):
    """A product as offered by one source in one country: marketplace_listing (retail) or supplier_offer (B2B)."""
    __tablename__ = "eu_listings"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("eu_products.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="marketplace_listing")  # | supplier_offer
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_product_id: Mapped[str] = mapped_column(String(128))
    market_country: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(512))
    seller_id: Mapped[str | None] = mapped_column(String(128), index=True)
    seller_name: Mapped[str | None] = mapped_column(String(255))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("eu_suppliers.id"), index=True)
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    ean: Mapped[str | None] = mapped_column(String(32), index=True)
    currency: Mapped[str | None] = mapped_column(String(8))
    price: Mapped[float | None] = mapped_column(Float)  # None = the source didn't give one (shown as "unknown")
    shipping_price: Mapped[float | None] = mapped_column(Float)
    price_includes_vat: Mapped[bool | None] = mapped_column(Boolean)  # retail incl. VAT; B2B wholesale usually not
    availability: Mapped[str] = mapped_column(String(24), default="unknown")
    url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)  # referenced, never downloaded
    search_text: Mapped[str | None] = mapped_column(Text)  # lower-cased title/brand/category/ean for local search
    query: Mapped[str | None] = mapped_column(String(255))  # keyword that first found it
    data_access: Mapped[str] = mapped_column(String(32), default="authorized_api")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # 1.0 = straight from an authorized API
    raw_metadata_ref: Mapped[dict] = mapped_column(JSON, default=dict)  # the source payload, as received
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now)  # first observation
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    __table_args__ = (UniqueConstraint("source", "source_product_id", "market_country"),)


class EUPriceSnapshot(Base):
    __tablename__ = "eu_price_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("eu_listings.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    shipping_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class EUTrendSignal(Base):
    """A raw observed value (rating count, offer count, buybox rank…), never a sales number."""
    __tablename__ = "eu_trend_signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("eu_products.id"), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("eu_listings.id"), index=True)
    country: Mapped[str | None] = mapped_column(String(8))
    signal_type: Mapped[str] = mapped_column(String(32), index=True)  # rating_count | offer_count | search_rank | …
    value: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class EUProductMatch(Base):
    """Retail product ↔ supplier offer link (Wholesale & Suppliers). EAN = auto-confirmed, title = needs review."""
    __tablename__ = "eu_product_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("eu_products.id"), index=True)
    supplier_listing_id: Mapped[int] = mapped_column(ForeignKey("eu_listings.id"), index=True)
    match_method: Mapped[str] = mapped_column(String(16))  # ean | title
    score: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("product_id", "supplier_listing_id"),)


class EUAdMatch(Base):
    """EU product ↔ an ad already in the SpyAds index (ads.id). Weak matches stay `pending` until a marketer reviews."""
    __tablename__ = "eu_ad_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("eu_products.id"), index=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), index=True)
    match_method: Mapped[str] = mapped_column(String(16))  # url | ean | title
    score: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("product_id", "ad_id"),)


class EUWatchlist(Base):
    __tablename__ = "eu_watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), default="team", index=True)  # marketer name (no per-user auth yet)
    name: Mapped[str] = mapped_column(String(255))
    keywords: Mapped[list] = mapped_column(JSON, default=list)  # re-synced by jobs.sync_catalog
    countries: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    # {"price_drop_pct": 10, "source_gap_pct": 30, "consecutive_rises": 3}
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    # product cluster watched by picture (image search §8): [{"phash", "dhash", "thumb", "label"}] — new indexed images
    # within IMAGE_SIMILAR_DIST of any of them mark the watchlist
    images: Mapped[list | None] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)


class EUSyncRun(Base):
    __tablename__ = "eu_sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    connector: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(24), default="running")  # running | ok | <error code>
    items_read: Mapped[int] = mapped_column(Integer, default=0)
    items_written: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    next_cursor: Mapped[str | None] = mapped_column(String(255))


class EUFxRate(Base):
    """ECB euro reference rates: 1 EUR = rate × currency, published per business day."""
    __tablename__ = "eu_fx_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    rate: Mapped[float] = mapped_column(Float)
    rate_date: Mapped[str] = mapped_column(String(10))  # ECB publication date (YYYY-MM-DD)
    source: Mapped[str] = mapped_column(String(16), default="ecb")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("currency", "rate_date"),)




class EUBotJob(Base):
    """One keyword search delegated to an external browser agent (Grok Bot routine) that POSTs results back.
    status: queued → sent → done | empty | captcha | blocked | failed | timeout. Only the token's sha256 is stored."""
    __tablename__ = "eu_bot_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str | None] = mapped_column(String(16), default="grok")  # serp (SerpApi Google Shopping) | grok (bot routine)
    keyword: Mapped[str] = mapped_column(String(255))
    keyword_key: Mapped[str] = mapped_column(String(255), index=True)  # folded keyword: cache / in-flight dedupe key
    limit: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    deadline_at: Mapped[datetime] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    items_received: Mapped[int] = mapped_column(Integer, default=0)
    items_written: Mapped[int] = mapped_column(Integer, default=0)
    listing_ids: Mapped[list] = mapped_column(JSON, default=list)
