"""Engagement history + enrichment queue. Additive tables (db.auto_migrate via app.models).

Two scopes, never mixed (spec §2 "phân biệt tương tác bài gốc với tương tác riêng của quảng cáo"):
  ad    numbers the AD itself reports (TikTok Creative Center like / comment / share of that creative) — also kept on ads.likes …
  post  numbers of the public post / video the ad was made from (Bright Data Facebook / TikTok Posts): organic + paid reach of the
        original post, NOT the ad's own engagement. Shown with its own label on the card.
A field the source did not give is NULL, never 0: the card shows N/A. A real 0 stays 0.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

AD, POST = "ad", "post"


def now():
    return datetime.utcnow()


class AdEngagement(Base):
    """One look at an ad's engagement: who said it, which scope, which post it maps to. Never overwritten, so history is the table."""
    __tablename__ = "ad_engagement"
    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int] = mapped_column(Integer, index=True)  # no FK: purge / merge jobs delete ads with raw SQL
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    scope: Mapped[str] = mapped_column(String(8), index=True)  # ad | post
    provider: Mapped[str] = mapped_column(String(32))  # the ad's source (tiktok_top_ads …) | brightdata_posts | router
    platform: Mapped[str | None] = mapped_column(String(16))  # tiktok | facebook
    post_id: Mapped[str | None] = mapped_column(String(64), index=True)  # Ad ID → Post ID / Video ID, as mapped
    post_url: Mapped[str | None] = mapped_column(Text)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)  # ok | unmapped | not_found | disabled | quota | error
    reason: Mapped[str | None] = mapped_column(String(64))  # why not ok (no_post_link, structured_disabled, quota_exceeded_daily …)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # mapping source, dataset id, raw field names read — never the raw payload


class EngagementJob(Base):
    """Queue, one row per ad (dedupe). Survives restarts: a `running` row older than 10 min is re-queued."""
    __tablename__ = "engagement_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued | running | done | error
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("ad_id"),)


class PageFetch(Base):
    """One Bright Data page-posts collection (Facebook - Pages Posts by Profile URL): sync answers fill `status=ready` at once, a slow one keeps
    its snapshot_id and the worker collects it later (progress / snapshot polling is free). One row per page, refreshed after PAGE_TTL_DAYS."""
    __tablename__ = "page_fetches"
    id: Mapped[int] = mapped_column(primary_key=True)
    page_key: Mapped[str] = mapped_column(String(64), unique=True)  # facebook page id / handle, as the ads carry it
    platform: Mapped[str] = mapped_column(String(16), default="facebook")
    page_url: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending | ready | failed | empty
    posts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)


class PagePost(Base):
    """A public post of a page, as Bright Data returned it: the pool ads are matched against (copy · landing link · picture hash · CLIP).
    Numbers are NULL when the record did not carry them."""
    __tablename__ = "page_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), default="facebook")
    page_key: Mapped[str] = mapped_column(String(64), index=True)
    post_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    external_link: Mapped[str | None] = mapped_column(Text)  # the link the post sends to (landing page of the product)
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    is_sponsored: Mapped[bool | None] = mapped_column(Boolean)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    phash: Mapped[str | None] = mapped_column(String(16))  # of the first picture, computed once when a match needs it
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("platform", "post_id"),)
