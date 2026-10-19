"""Phase 3: per-MKT behavior history for future recommendation. Append-only, one row per real action.

Duplicates are stopped two ways: toggles (save / favorite / tested / follow) are recorded only when the stored state
actually changes, inside the existing workspace transaction; client-reported signals (view, detail, skip,
not-interested) carry a deterministic dedupe_key per time window and are inserted with ON CONFLICT DO NOTHING.
Nothing here grants ownership — that is app/allocation.py only.
"""
import os
import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Ad

KINDS = ("ad_view", "ad_detail_view", "ad_save", "ad_unsave", "ad_favorite", "ad_unfavorite", "test_start",
         "test_success", "test_fail", "test_inconclusive", "ad_skip", "ad_not_interested",
         "advertiser_follow", "advertiser_unfollow")
CLIENT_KINDS = ("ad_view", "ad_detail_view", "ad_skip", "ad_not_interested")
OUTCOME_KIND = {"winner": "test_success", "loser": "test_fail", "inconclusive": "test_inconclusive"}


class BehaviorEvent(Base):
    __tablename__ = "mkt_behavior_events"
    __table_args__ = (CheckConstraint("kind IN (" + ",".join(f"'{k}'" for k in KINDS) + ")"),
                      Index("ix_behavior_user_time", "user_id", "created_at", "id"),
                      Index("ix_behavior_user_kind", "user_id", "kind"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(24))
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("ads.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, index=True)  # no FK: legacy product merges may delete ids
    advertiser_id: Mapped[int | None] = mapped_column(ForeignKey("advertisers.id"))
    allocation_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _window(kind, now):
    """Dedupe scope for client signals: a view counts once per window, not-interested once per ad."""
    if kind == "ad_not_interested":
        return "once"
    minutes = 24 * 60 if kind == "ad_skip" else int(os.getenv("BEHAVIOR_VIEW_WINDOW_MIN", "30"))
    return str(int(now.timestamp()) // (max(1, minutes) * 60))


def _insert(db, rows):
    if not rows:
        return 0
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    # RETURNING, not rowcount: psycopg reports -1 for multi-row inserts. Conflicts return nothing.
    return len(db.execute(insert(BehaviorEvent).values(rows).on_conflict_do_nothing(index_elements=["dedupe_key"])
                          .returning(BehaviorEvent.id)).all())


def track(db, user_id, kind, ad=None, key=None, product_id=None, advertiser_id=None, allocation_id=None, **data):
    """Record one server-observed action. `key` defaults to unique: callers only call on a real state change."""
    if not user_id:
        return 0  # service-token requests have no MKT identity; never attribute them to anyone
    return _insert(db, [{"user_id": user_id, "kind": kind, "ad_id": ad.id if ad else None,
                         "product_id": product_id or (ad.product_id if ad else None),
                         "advertiser_id": advertiser_id or (ad.advertiser_id if ad else None),
                         "allocation_id": allocation_id, "data": data, "created_at": datetime.utcnow(),
                         "dedupe_key": key or f"{user_id}:{kind}:{uuid.uuid4().hex}"}])


def track_client(db, user_id, kind, ad_ids):
    """Batch of client-reported signals: one SELECT + one INSERT, unknown ad ids ignored."""
    now = datetime.utcnow()
    win = _window(kind, now)
    ads = db.execute(select(Ad.id, Ad.product_id, Ad.advertiser_id).where(Ad.id.in_(set(ad_ids)))).all()
    return _insert(db, [{"user_id": user_id, "kind": kind, "ad_id": a.id, "product_id": a.product_id,
                         "advertiser_id": a.advertiser_id, "allocation_id": None, "data": {}, "created_at": now,
                         "dedupe_key": f"{user_id}:{kind}:{a.id}:{win}"} for a in ads])


def interaction(db, user_id, ad, before, row, allocation=None):
    """Save / favorite / tested from PUT /api/workspace/ads/{aid}: only real flips are history."""
    flips = {"saved": ("ad_save", "ad_unsave"), "favorite": ("ad_favorite", "ad_unfavorite"), "tested": ("test_start", None)}
    for field, (on, off) in flips.items():
        was, now = bool(before.get(field)), bool(getattr(row, field))
        kind = on if now and not was else off if was and not now else None
        if kind:
            track(db, user_id, kind, ad, allocation_id=allocation.id if allocation and kind == "test_start" else None)


def view(e):
    return {"id": e.id, "kind": e.kind, "ad_id": e.ad_id, "product_id": e.product_id, "advertiser_id": e.advertiser_id,
            "allocation_id": e.allocation_id, "data": e.data, "created_at": e.created_at}
