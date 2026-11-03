"""Creative / video reuse insight (P4): which pages, shops and markets run the same video, and since when WE saw them.

`creative_reuse_observations` = one row per (creative family, ad): page, advertiser, market, source, first / last time a collector
reported the ad (AdSource — our observation, not Meta's delivery dates) and the evidence that put the creative in the family
(video_fp_v2 > video_fp_v1 > poster phash, with its confidence and the Ad Library link).

The features are EXPERIMENTAL: shown with their evidence, never read by the decision engine (TEST / TEST_NOW stay as they are).
Four counts that are easy to confuse are kept apart:
  ad_count            distinct ads in ToolSpy (Meta collation siblings already merged into one ad)
  collation_variants  sum of Meta's own collation_count over those ads (variants Meta groups under one ad; unknown = 1)
  distinct_pages      distinct Facebook page ids running the video
  distinct_shop_id    distinct stores (landing-page shops) behind those ads
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, Index, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base

log = logging.getLogger(__name__)


class CreativeReuseObservation(Base):
    __tablename__ = "creative_reuse_observations"
    __table_args__ = (Index("uq_reuse_family_ad", "family_key", "ad_id", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    family_key: Mapped[str] = mapped_column(String(40), index=True)  # "fam:<creatives.family_id>"
    ad_id: Mapped[int] = mapped_column(Integer, index=True)
    creative_id: Mapped[int | None] = mapped_column(Integer)
    page_id: Mapped[str | None] = mapped_column(String(64), index=True)
    advertiser_id: Mapped[int | None] = mapped_column(Integer)
    advertiser: Mapped[str | None] = mapped_column(String(255))
    market: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind, confidence, snapshot_url}
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)  # first time a collector reported the ad
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _evidence(c, snapshot_url: str | None) -> dict:
    v2 = c.video_fp_v2 or {}
    if v2.get("version") == 2 and any(v2.get("frame_hashes") or []):
        kind, conf = "video_fp_v2", v2.get("confidence")
    elif c.video_fp:
        kind, conf = "video_fp_v1", c.video_fp_confidence
    elif c.phash:
        kind, conf = "poster_phash", None
    else:
        kind, conf = "same_file", None
    return {"kind": kind, "confidence": conf, "snapshot_url": snapshot_url}


RANK = {"video_fp_v2": 3, "video_fp_v1": 2, "poster_phash": 1, "same_file": 0}


def refresh(db: Session, now: datetime | None = None) -> dict:
    """Upsert one observation per (family, ad) from creatives that belong to a family. Idempotent.
    ponytail: full pass (~30k creatives in a few seconds) every REUSE_EVERY_MIN; an incremental `since` when the vault grows 10x."""
    from .models import Ad, AdSource, Advertiser, Creative

    now = now or datetime.utcnow()
    seen = {aid: (f, l) for aid, f, l in db.execute(
        select(AdSource.ad_id, func.min(AdSource.first_collected_at), func.max(AdSource.last_collected_at)).group_by(AdSource.ad_id))}
    best: dict[tuple[str, int], tuple] = {}
    for c, a, adv in db.execute(select(Creative, Ad, Advertiser).join(Ad, Ad.id == Creative.ad_id)
                                .outerjoin(Advertiser, Advertiser.id == Ad.advertiser_id).where(Creative.family_id.is_not(None))):
        key = (f"fam:{c.family_id}", a.id)
        ev = _evidence(c, a.snapshot_url)
        if key not in best or RANK[ev["kind"]] > RANK[best[key][3]["kind"]]:
            best[key] = (c, a, adv, ev)
    have = {(o.family_key, o.ad_id): o for o in db.scalars(select(CreativeReuseObservation))}
    for key, (c, a, adv, ev) in best.items():
        first, last = seen.get(a.id, (None, None))
        o = have.get(key) or CreativeReuseObservation(family_key=key[0], ad_id=a.id)
        if o.id is None:
            db.add(o)
        o.creative_id, o.page_id, o.advertiser_id = c.id, a.page_id, a.advertiser_id
        o.advertiser, o.market, o.source, o.evidence = adv.name if adv else None, a.country, a.source, ev
        o.first_seen_at = min(x for x in (o.first_seen_at, first) if x) if (o.first_seen_at or first) else None
        o.last_seen_at = max(x for x in (o.last_seen_at, last) if x) if (o.last_seen_at or last) else None
        o.updated_at = now
    db.commit()
    return {"observations": len(best)}


def features(db: Session, family_key: str, now: datetime | None = None) -> dict:
    """Experimental reuse features of one family (see the module docstring for what each count means)."""
    from .models import Ad

    now = now or datetime.utcnow()
    obs = db.scalars(select(CreativeReuseObservation).where(CreativeReuseObservation.family_key == family_key)).all()
    ads = {a.id: a for a in db.scalars(select(Ad).where(Ad.id.in_([o.ad_id for o in obs])))} if obs else {}
    week = now - timedelta(days=7)
    page_first: dict[str, datetime] = {}
    for o in obs:
        if o.page_id and o.first_seen_at:
            page_first[o.page_id] = min(page_first.get(o.page_id, o.first_seen_at), o.first_seen_at)
    firsts = [o.first_seen_at for o in obs if o.first_seen_at]
    return {
        "experimental": True,  # shown with evidence, never read by the decision engine
        "ad_count": len({o.ad_id for o in obs}),
        "collation_variants": sum((ads[o.ad_id].variants or 1) for o in obs if o.ad_id in ads),
        "distinct_pages": len({o.page_id for o in obs if o.page_id}),
        "distinct_shop_id": len({ads[o.ad_id].store_id for o in obs if o.ad_id in ads and ads[o.ad_id].store_id}),
        "markets": sorted({o.market for o in obs if o.market}),
        "new_distinct_pages_7d": sum(1 for t in page_first.values() if t >= week),
        "reuse_velocity_7d": round(sum(1 for t in firsts if t >= week) / 7, 3),  # ads first observed per day, last 7 days
        "earliest_observed_video_at": min(firsts).isoformat() if firsts else None,
    }


def for_product(db: Session, product_id: int, families: int = 10, per_family: int = 30) -> list[dict]:
    """The reuse picture of a product's creative families — other shops' ads included (that is the insight)."""
    from .models import Creative

    fams = [f for (f,) in db.execute(select(Creative.family_id).where(Creative.product_id == product_id, Creative.family_id.is_not(None))
                                     .group_by(Creative.family_id).order_by(func.count().desc()).limit(families))]
    out = []
    for fid in fams:
        key = f"fam:{fid}"
        rows = db.scalars(select(CreativeReuseObservation).where(CreativeReuseObservation.family_key == key)
                          .order_by(CreativeReuseObservation.last_seen_at.desc().nulls_last()).limit(per_family)).all()
        out.append({"family_key": key, "features": features(db, key), "observations": [
            {"ad_id": o.ad_id, "page_id": o.page_id, "advertiser": o.advertiser, "market": o.market, "source": o.source,
             "evidence": o.evidence, "first_seen_at": o.first_seen_at, "last_seen_at": o.last_seen_at} for o in rows]})
    return out
