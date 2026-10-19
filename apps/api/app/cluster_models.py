"""Additive cluster overlay. Original Ads, Products and their histories are never moved/deleted."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ProductCluster(Base):
    __tablename__ = "product_clusters"
    # Stable original Product ID; redirects group products without rewriting their rows.
    id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("product_clusters.id"), index=True)
    revision: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("merged_into_id IS NULL OR merged_into_id <> id"),)


class AdClusterMembership(Base):
    __tablename__ = "ad_cluster_memberships"
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("product_clusters.id"), index=True)
    source_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    # NULL means the legacy assignment has not been evaluated, not 100% certainty.
    confidence: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(32), default="legacy")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"),)


class DuplicateAdLink(Base):
    __tablename__ = "duplicate_ad_links"
    a_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), primary_key=True)
    b_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (CheckConstraint("a_id < b_id"), CheckConstraint("confidence >= 0 AND confidence <= 1"))
