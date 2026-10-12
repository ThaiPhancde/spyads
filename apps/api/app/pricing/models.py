"""Price memory + durable enrichment queue. Additive tables (db.auto_migrate via app.models)."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

# price classification (spec §XI) — a verified price names the seller + page it was read from; a reference never does
VERIFIED_COMPETITOR, VERIFIED_MARKETPLACE, MARKET_REFERENCE = "VERIFIED_COMPETITOR", "VERIFIED_MARKETPLACE", "MARKET_REFERENCE"
HISTORICAL_PRICE, ESTIMATED_RANGE, NOT_FOUND = "HISTORICAL_PRICE", "ESTIMATED_RANGE", "NOT_FOUND"


def now():
    return datetime.utcnow()


class PriceObservation(Base):
    """One price seen once: who sold it, where, when, how we read it. Never overwritten — a new look = a new row,
    so price history (monitoring) is the table itself."""
    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(32), index=True)  # "ad:123" | "product:45" | "listing:7" — what the card shows
    cluster_key: Mapped[str | None] = mapped_column(String(64), index=True)  # "product:45" — ads of one product share references
    market_country: Mapped[str | None] = mapped_column(String(8), index=True)  # where the buyer is (the ad's market)
    source_country: Mapped[str | None] = mapped_column(String(8))  # where the price page sells
    price_type: Mapped[str] = mapped_column(String(24), index=True)
    price_amount: Mapped[float | None] = mapped_column(Float)
    price_low: Mapped[float | None] = mapped_column(Float)  # MARKET_REFERENCE / ESTIMATED_RANGE
    price_high: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    original_price: Mapped[float | None] = mapped_column(Float)  # compare-at / strike-through
    shipping_amount: Mapped[float | None] = mapped_column(Float)
    seller: Mapped[str | None] = mapped_column(String(255))  # host or shop name — verified prices are per seller
    provider: Mapped[str] = mapped_column(String(24))  # ad_text | landing | direct | brightdata | serpapi_shopping | serpapi_lens
    extraction_method: Mapped[str | None] = mapped_column(String(32))  # jsonld | meta | shopify_json | dom | markdown | serp_snippet | api
    source_url: Mapped[str | None] = mapped_column(Text)
    match_confidence: Mapped[float] = mapped_column(Float, default=1.0)  # is this the same product (+ variant)?
    price_confidence: Mapped[float] = mapped_column(Float, default=1.0)  # is this number the product's price?
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # snippet / jsonld offer / variant — provenance
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class PriceJob(Base):
    """Enrichment queue, one row per subject (dedupe). Survives restarts: a `running` row older than 10 min is re-queued."""
    __tablename__ = "price_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued | running | done | error
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher first (opened detail = 10, visible card = 1, monitor = 0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{step, ok, code, ms}] — why a price is / isn't there
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("subject"),)
