"""Image index tables (spec §7). Additive; created by db.auto_migrate via app.models."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def now():
    return datetime.utcnow()


class ProductImage(Base):
    """One indexed image, linked to the row that owns it: source=creative → creatives.id, source=eu_listing → eu_listings.id.
    Hashes/embedding are computed from the stored ≤480 px thumbnail (thumb_key), the same normalisation a query gets."""
    __tablename__ = "product_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    image_url: Mapped[str | None] = mapped_column(Text)  # where the image came from (provenance; may expire)
    thumb_key: Mapped[str | None] = mapped_column(String(255))  # stored thumbnail (storage.store())
    image_sha256: Mapped[str | None] = mapped_column(String(64), index=True)  # of the thumbnail bytes
    phash: Mapped[str | None] = mapped_column(String(16), index=True)  # imagehash.phash, 64 bit hex
    dhash: Mapped[str | None] = mapped_column(String(16), index=True)  # imagehash.dhash, 64 bit hex
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)  # float16[dim], L2-normalised (embed.py)
    emb_model: Mapped[str | None] = mapped_column(String(48))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)  # when the owner row was collected
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime)
    processing_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | done | failed | no_image
    error: Mapped[str | None] = mapped_column(String(255))
    __table_args__ = (UniqueConstraint("source", "source_id"),)


class LensSearch(Base):
    """One live web image search (lens.py) = the cache entry and the async job at once. cache_key covers image sha256 +
    crop + provider + country + language + type + extra query; results are normalized dicts; raw is the provider payload
    (cleared after LENS_RAW_RETENTION_DAYS)."""
    __tablename__ = "lens_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(24), default="serpapi_lens")
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # country, hl, type, q, crop, input (upload | url)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running | ok | error
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_detail: Mapped[str | None] = mapped_column(Text)
    results: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict | None] = mapped_column(JSON)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class ExternalApiCall(Base):
    """Every call (and cache hit) toward a paid external API — the quota ledger (ImageSearchQuotaManager)."""
    __tablename__ = "external_api_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # search | upload | account | cache_hit | refused
    counted: Mapped[bool] = mapped_column(Boolean, default=False)  # consumes the provider's monthly quota
    ok: Mapped[bool | None] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(48))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    search_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tokens: Mapped[int | None] = mapped_column(Integer)  # LLM calls: total tokens the provider reported
    units: Mapped[float | None] = mapped_column(Float)   # LLM calls: Cloudflare neurons
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class ProductMatch(Base):
    """A reviewed link between two indexed entities (spec §7 ProductMatch). Written by marketer review (confirm / reject)
    and by the auto rules that are safe (same sha256 across sources = exact). Never guessed from looks alone."""
    __tablename__ = "product_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    target_source: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    match_type: Mapped[str] = mapped_column(String(24))  # exact | likely_same_product | visually_similar
    similarity_score: Mapped[float | None] = mapped_column(Float)
    match_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | confirmed | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("source", "source_id", "target_source", "target_id"),)
