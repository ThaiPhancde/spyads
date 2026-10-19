from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def now():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role = 'MKT'"), CheckConstraint("status IN ('pending','active','locked')"))
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="MKT")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CsrfToken(Base):
    __tablename__ = "auth_csrf_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class Profile(Base):
    __tablename__ = "mkt_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    team: Mapped[str] = mapped_column(String(16), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    advertiser_mode: Mapped[str] = mapped_column(String(16), default="all")


class AdInteraction(Base):
    """One private row per ad, replacing three redundant saved/favorite/tested join tables."""
    __tablename__ = "mkt_ad_interactions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id"), primary_key=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    tested: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SavedSearch(Base):
    __tablename__ = "mkt_saved_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Follow(Base):
    __tablename__ = "mkt_followed_advertisers"
    __table_args__ = (CheckConstraint("interval_hours IN (6,12,24)"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), primary_key=True, index=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    followed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AdvertiserFilter(Base):
    __tablename__ = "mkt_advertiser_filters"
    __table_args__ = (CheckConstraint("mode IN ('whitelist','blacklist')"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))


class ScanJob(Base):
    """A single durable schedule/lease per advertiser, shared by all followers."""
    __tablename__ = "advertiser_scan_jobs"
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_status: Mapped[str] = mapped_column(String(32), default="pending")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_check_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    run_token: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    last_changes: Mapped[int] = mapped_column(Integer, default=0)
    last_manual_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceQuota(Base):
    __tablename__ = "competitor_source_quotas"
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    used: Mapped[int] = mapped_column(Integer, default=0)


class TrackedAd(Base):
    __tablename__ = "advertiser_tracked_ads"
    __table_args__ = (UniqueConstraint("advertiser_id", "source_ad_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), index=True)
    source_ad_id: Mapped[str] = mapped_column(String(128))
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("ads.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)


class AdSnapshot(Base):
    __tablename__ = "advertiser_ad_snapshots"
    __table_args__ = (UniqueConstraint("tracked_ad_id", "run_token"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_ad_id: Mapped[int] = mapped_column(ForeignKey("advertiser_tracked_ads.id"), index=True)
    run_token: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ActivityEvent(Base):
    __tablename__ = "advertiser_activity_events"
    __table_args__ = (Index("ix_advertiser_activity_time", "advertiser_id", "created_at", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("advertisers.id"), index=True)
    tracked_ad_id: Mapped[int | None] = mapped_column(ForeignKey("advertiser_tracked_ads.id"))
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Notification(Base):
    __tablename__ = "mkt_notifications"
    __table_args__ = (UniqueConstraint("user_id", "event_id"), Index("ix_notifications_user_read", "user_id", "read_at", "id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("advertiser_activity_events.id"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserActivity(Base):
    __tablename__ = "mkt_activity"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WorkspaceMigration(Base):
    __tablename__ = "workspace_migrations"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ResourceAccess(Base):
    """Private access grants to reusable shared lookup jobs (e.g. Lens cache)."""
    __tablename__ = "mkt_resource_access"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    resource: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[int] = mapped_column(Integer, primary_key=True)
