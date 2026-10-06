"""Retention: keep the *knowledge* (ads, scores, thumbnails, history), expire the *heavy files*.

What costs space is video: ~12 MB each, vs a whole database of ~14 MB. So by default:

  videos   → file deleted MEDIA_RETENTION_DAYS (7) after it was stored; thumbnail + metadata + original
             link stay (status "archived"), the card still shows the poster frame
  images   → IMAGE_RETENTION_DAYS (30)
  raw data → RAW_RETENTION_DAYS (90) raw/*.jsonl batches (only needed to reprocess)
  events   → EVENT_RETENTION_DAYS (30) realtime feed rows
  temp     → half-downloaded .part files older than 1 day

Never expired automatically (protected):
  · creatives pinned by a user (📌)
  · products somebody voted LOVE / TEST / WATCH on
  · products with orders, experiments or ad spend (company data)
  · ads saved through the Chrome extension / manual import (saved_by)

Set a retention value to 0 to disable that rule. Archived videos can be re-downloaded ("Thử lại") only while
the original CDN link is still alive — usually it is not, so pin what you want to keep.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Ad, AdMetric, Creative, Event, Experiment, Order, Vote
from .storage import BACKEND, DATA_DIR, store


def cfg() -> dict:
    g = lambda k, d: int(os.getenv(k, d))
    return {"media_days": g("MEDIA_RETENTION_DAYS", 7), "image_days": g("IMAGE_RETENTION_DAYS", 30),
            "raw_days": g("RAW_RETENTION_DAYS", 90), "event_days": g("EVENT_RETENTION_DAYS", 30)}


def protected_products(db: Session) -> set[int]:
    ids: set[int] = set()
    ids |= {p for (p,) in db.execute(select(Vote.product_id).where(Vote.decision.in_(["LOVE", "TEST", "WATCH"])))}
    ids |= {p for (p,) in db.execute(select(Order.product_id).distinct())}
    ids |= {p for (p,) in db.execute(select(Experiment.product_id).distinct())}
    ids |= {p for (p,) in db.execute(select(AdMetric.product_id).where(AdMetric.product_id.is_not(None)).distinct())}
    ids |= {p for (p,) in db.execute(select(Ad.product_id).where(Ad.saved_by.is_not(None), Ad.product_id.is_not(None)).distinct())}
    return ids


def usage(db: Session) -> dict:
    rows = db.execute(select(Creative.type, Creative.status, Creative.size_bytes)).all()
    out: dict = {"video": {"count": 0, "bytes": 0}, "image": {"count": 0, "bytes": 0}, "archived": 0, "pending": 0, "pinned": 0}
    for t, st, sz in rows:
        if st == "stored":
            out[t]["count"] += 1
            out[t]["bytes"] += sz or 0
        elif st == "archived":
            out["archived"] += 1
        elif st == "pending":
            out["pending"] += 1
    out["pinned"] = len(db.scalars(select(Creative.id).where(Creative.pinned.is_(True))).all())
    raw_bytes = 0
    if BACKEND == "local":
        for k in store().list("raw"):
            try:
                raw_bytes += store().size(k) or 0
            except Exception:
                pass
    out["raw_bytes"] = raw_bytes
    out["config"] = cfg()
    out["backend"] = BACKEND
    return out


def run_cleanup(db: Session, dry_run: bool = False) -> dict:
    c = cfg()
    now = datetime.utcnow()
    res = {"dry_run": dry_run, "archived": 0, "freed_bytes": 0, "kept_protected": 0, "kept_shared": 0, "raw_deleted": 0,
           "events_deleted": 0, "tmp_deleted": 0}
    prot = protected_products(db)

    # Reconcile: rows marked "stored" whose file vanished (deleted by hand / disk cleanup) → archived, so the UI
    # shows the poster instead of a broken player. Local disk only (a HEAD per object on R2 would be slow).
    res["missing_marked"] = 0
    if BACKEND == "local":
        for cr in db.scalars(select(Creative).where(Creative.status == "stored")).all():
            if cr.storage_key and not store().path(cr.storage_key).exists():
                res["missing_marked"] += 1
                if not dry_run:
                    cr.status, cr.archived_at, cr.error = "archived", now, "File không còn trong kho"

    for ctype, days in (("video", c["media_days"]), ("image", c["image_days"])):
        if days <= 0:
            continue
        cutoff = now - timedelta(days=days)
        cands = db.scalars(select(Creative).where(Creative.type == ctype, Creative.status == "stored",
                                                  Creative.stored_at < cutoff)).all()
        # a stored file can be shared by several creatives (same sha256): delete it only when none of them stays
        keep_keys = {k for (k,) in db.execute(select(Creative.storage_key).where(
            Creative.status == "stored", Creative.type == ctype, Creative.stored_at >= cutoff))}
        for cr in cands:
            if cr.pinned or (cr.product_id and cr.product_id in prot):
                res["kept_protected"] += 1
                keep_keys.add(cr.storage_key)
        by_key: dict[str, list[Creative]] = {}
        for cr in cands:
            if cr.pinned or (cr.product_id and cr.product_id in prot):
                continue
            by_key.setdefault(cr.storage_key, []).append(cr)
        for key, group in by_key.items():
            if key in keep_keys:
                res["kept_shared"] += len(group)
                continue
            size = (store().size(key) or group[0].size_bytes or 0) if key else 0
            res["freed_bytes"] += size
            res["archived"] += len(group)
            if dry_run:
                continue
            if key:
                try:
                    store().delete(key)
                except Exception:
                    continue
            for cr in group:
                cr.status, cr.archived_at, cr.error = "archived", now, f"Đã dọn khỏi kho sau {days} ngày (giữ ảnh bìa + thông tin)"
        if not dry_run:
            db.flush()

    if c["raw_days"] > 0:
        cutoff_day = (now - timedelta(days=c["raw_days"])).strftime("%Y-%m-%d")
        for key in list(store().list("raw")):
            parts = key.split("/")  # raw/<source>/<YYYY-MM-DD>/<file>
            if len(parts) >= 4 and parts[2] < cutoff_day:
                res["raw_deleted"] += 1
                if not dry_run:
                    try:
                        store().delete(key)
                    except Exception:
                        pass

    if c["event_days"] > 0:
        cutoff = now - timedelta(days=c["event_days"])
        n = len(db.scalars(select(Event.id).where(Event.created_at < cutoff)).all())
        res["events_deleted"] = n
        if n and not dry_run:
            db.execute(delete(Event).where(Event.created_at < cutoff))

    tmp = DATA_DIR / "tmp"
    if BACKEND == "local" and tmp.exists():
        for f in tmp.glob("*.part"):
            if now.timestamp() - f.stat().st_mtime > 86400:
                res["tmp_deleted"] += 1
                if not dry_run:
                    f.unlink(missing_ok=True)

    if not dry_run:
        db.commit()
    return res
