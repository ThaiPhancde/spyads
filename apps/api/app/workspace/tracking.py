import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from ..models import Ad, AdSource
from .models import ActivityEvent, AdSnapshot, Follow, Notification, TrackedAd


def fingerprint(data):
    def clean(v):
        if isinstance(v, str) and v.startswith(("https://", "http://")):
            u = urlsplit(v)
            # Only expiring media query signatures are ignored; landing query parameters carry meaning.
            return urlunsplit((u.scheme, u.netloc, u.path, "", ""))
        return v
    content = {k: v for k, v in data.items() if k not in ("metrics", "observed_at", "source", "snapshot_url")}
    content["media"] = [clean(u) for u in content.get("media") or []]
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def record_data(rec):
    return {"text": rec.ad_text, "title": rec.title, "media": [m.url for m in rec.media] or None, "media_type": rec.creative_type,
            "landing_url": rec.landing_page, "status": "active" if rec.active is True else "inactive" if rec.active is False else "unknown",
            "markets": sorted(rec.countries or ([rec.country] if rec.country else [])) or None,
            "platform": rec.platform, "start_date": rec.first_seen, "snapshot_url": rec.snapshot_url,
            "metrics": {k: getattr(rec, k) for k in ("views", "likes", "comments", "shares") if getattr(rec, k) is not None}}


def event(db, aid, kind, key, data=None, tracked_id=None, observed_at=None, notify=True):
    existing = db.scalar(select(ActivityEvent).where(ActivityEvent.dedupe_key == key))
    if existing:
        return existing
    e = ActivityEvent(advertiser_id=aid, tracked_ad_id=tracked_id, kind=kind, dedupe_key=key,
                      data=data or {}, created_at=observed_at or datetime.utcnow())
    db.add(e)
    db.flush()
    if notify:
        for uid in db.scalars(select(Follow.user_id).where(Follow.advertiser_id == aid, Follow.followed_at <= e.created_at)):
            db.add(Notification(user_id=uid, event_id=e.id))
    return e


def baseline(db, advertiser):
    existing = set(db.scalars(select(TrackedAd.source_ad_id).where(TrackedAd.advertiser_id == advertiser.id)))
    for ad in db.scalars(select(Ad).where(Ad.advertiser_id == advertiser.id)):
        if ad.external_id in existing:
            continue
        # Legacy is_active is an inferred/default bool; do not present it as fresh source evidence.
        data = {"text": ad.ad_text, "title": ad.title, "media": [ad.media_url] if ad.media_url else None,
                "landing_url": ad.landing_url, "status": "unknown", "markets": ad.countries or [], "platform": ad.platform,
                "snapshot_url": ad.snapshot_url, "metrics": {k: getattr(ad, col) for k, col in
                    (("views", "views"), ("likes", "likes"), ("comments", "comments_count"), ("shares", "shares")) if getattr(ad, col) is not None}}
        row = TrackedAd(advertiser_id=advertiser.id, source_ad_id=ad.external_id, ad_id=ad.id,
                        data=data, fingerprint=fingerprint(data), baseline=True, status="unknown")
        db.add(row)
        db.flush()
        db.add(AdSnapshot(tracked_ad_id=row.id, run_token="baseline", source=ad.source, data=data, observed_at=ad.last_seen_at))
        existing.add(ad.external_id)


def apply_scan(db, advertiser, result, run_token, observed_at=None):
    """Called with the job row locked. All observations + events + notifications commit together."""
    observed_at = observed_at or datetime.utcnow()
    existing = {a.source_ad_id: a for a in db.scalars(select(TrackedAd).where(TrackedAd.advertiser_id == advertiser.id))}
    records = {str(r.source_ad_id): r for r in result.records}
    ad_ids = dict(db.execute(select(AdSource.source_ad_id, AdSource.ad_id).where(
        AdSource.source == advertiser.source, AdSource.source_ad_id.in_(records))).all())
    ad_ids.update(dict(db.execute(select(Ad.external_id, Ad.id).where(
        Ad.source == advertiser.source, Ad.external_id.in_(records))).all()))
    changes = 0
    for external, rec in records.items():
        row = existing.get(external)
        fresh = record_data(rec)
        data = dict(row.data) if row else {}
        data.pop("absence", None)
        # A missing optional field is not evidence of deletion. Metrics always represent this observation only.
        data.update({k: v for k, v in fresh.items() if v is not None})
        fp = fingerprint(data)
        kind, changed = None, []
        if not row:
            row = TrackedAd(advertiser_id=advertiser.id, source_ad_id=external, ad_id=ad_ids.get(external),
                            data=data, fingerprint=fp, status=data["status"], detected_at=observed_at, last_seen_at=observed_at)
            db.add(row)
            db.flush()
            kind = "new_ad"
        elif fp != row.fingerprint:
            changed = [k for k in ("text", "title", "media", "landing_url", "status", "markets")
                       if data.get(k) != row.data.get(k) and fresh.get(k) is not None]
            # CDN signed URLs alone are not creative changes.
            if "media" in changed and fingerprint({"media": data["media"]}) == fingerprint({"media": row.data.get("media")}):
                changed.remove("media")
            kind = "ad_inactive" if data["status"] == "inactive" and row.status != "inactive" else "ad_changed" if changed else None
        if kind:
            event(db, advertiser.id, kind, f"{run_token}:{external}:{kind}",
                  {"fields": changed, "source_ad_id": external, "ad_id": row.ad_id, "status": data["status"]}, row.id, observed_at)
            changes += 1
        row.data, row.fingerprint, row.status, row.last_seen_at = data, fp, data["status"], observed_at
        if ad_ids.get(external):
            row.ad_id = ad_ids[external]
        if not db.scalar(select(AdSnapshot.id).where(AdSnapshot.tracked_ad_id == row.id, AdSnapshot.run_token == run_token)):
            db.add(AdSnapshot(tracked_ad_id=row.id, run_token=run_token, source=advertiser.source,
                              data=data, observed_at=observed_at))
    if result.complete:
        for external, row in existing.items():
            if external in records or external in result.seen_ids or row.status == "unknown":
                continue
            data = dict(row.data, status="unknown", absence="Không tìm thấy trong lần quét; không xác nhận đã dừng")
            row.status, row.data, row.fingerprint = "unknown", data, fingerprint(data)
            db.add(AdSnapshot(tracked_ad_id=row.id, run_token=run_token, source=advertiser.source, data=data, observed_at=observed_at))
            event(db, advertiser.id, "ad_unobserved", f"{run_token}:{external}:missing",
                  {"source_ad_id": external, "status": "unknown"}, row.id, observed_at, notify=False)
            changes += 1
    return changes
