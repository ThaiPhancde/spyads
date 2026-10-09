"""Retention: keep the *knowledge* (ads, scores, thumbnails, history), expire the *heavy files*.

What costs space is video: ~12 MB each, vs a whole database of ~14 MB. So by default:

  videos   → rolling daily batch: at most VIDEO_DAILY_QUOTA (3000) new videos per day, strongest ads first;
             at the end of the day (DAY_TZ_OFFSET, default UTC+7) every video older than VIDEO_KEEP_DAYS (1 = only
             today's batch) is deleted so the next day's batch is fresh. Thumbnail + metadata + original link stay
             (status "archived"), the card still shows the poster frame
  images   → IMAGE_RETENTION_DAYS (30)
  raw data → RAW_RETENTION_DAYS (90) raw/*.jsonl batches (only needed to reprocess)
  events   → EVENT_RETENTION_DAYS (30) realtime feed rows
  temp     → half-downloaded .part files older than 1 day

Never expired automatically (protected):
  · creatives pinned by a user (📌)
  · products somebody voted LOVE / TEST / WATCH on
  · ads saved through the Chrome extension / manual import (saved_by)

Set a retention value to 0 to disable that rule. Archived videos can be re-downloaded ("Thử lại") only while
the original CDN link is still alive — usually it is not, so pin what you want to keep.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Ad, Creative, Event, Vote
from .storage import BACKEND, DATA_DIR, store


def cfg() -> dict:
    g = lambda k, d: int(os.getenv(k, d))
    return {"video_keep_days": g("VIDEO_KEEP_DAYS", 1), "video_daily_quota": g("VIDEO_DAILY_QUOTA", 3000),
            "day_tz_offset": g("DAY_TZ_OFFSET", 7), "image_days": g("IMAGE_RETENTION_DAYS", 30),
            "raw_days": g("RAW_RETENTION_DAYS", 90), "event_days": g("EVENT_RETENTION_DAYS", 30)}


def day_start(now: datetime | None = None) -> datetime:
    """Start (as naive UTC, like stored_at) of the team's current day — midnight at UTC+DAY_TZ_OFFSET."""
    off = timedelta(hours=cfg()["day_tz_offset"])
    local = (now or datetime.utcnow()) + off
    return local.replace(hour=0, minute=0, second=0, microsecond=0) - off


# ------------------------------------------------------------ per-network budgets
# One network must never eat the whole store (Meta alone used to fill the 20 GB cap and starve TikTok / sources).
# STORAGE_SHARES splits both R2_MAX_GB and VIDEO_DAILY_QUOTA. Strict caps: an unused share stays free for its owner.
BUCKETS = {"meta": "Meta", "tiktok": "TikTok (ads + viral)", "ads_other": "Snapchat · khác", "commerce": "Nguồn hàng & store"}
MAX_STORAGE_BYTES = int(float(os.getenv("R2_MAX_GB", "20")) * 1024 ** 3)


def bucket_of(network: str | None) -> str:
    from .platforms import channel_of

    if network == "meta":
        return "meta"
    if network in ("tiktok", "tiktok_organic"):
        return "tiktok"
    return "commerce" if network and channel_of(network) == "commerce" else "ads_other"


def shares() -> dict[str, float]:
    raw = dict(kv.split(":") for kv in os.getenv("STORAGE_SHARES", "meta:45,tiktok:30,ads_other:10,commerce:15").replace(" ", "").split(",") if ":" in kv)
    vals = {b: float(raw.get(b, 0)) for b in BUCKETS}
    total = sum(vals.values()) or 1
    return {b: v / total for b, v in vals.items()}


def _network(cr) -> str:
    from .platforms import network_of

    return cr.network or network_of(cr.source_platform, cr.source)


_files_cache: tuple[float, list] = (0.0, [])


def stored_files(db: Session, fresh: bool = False) -> list:
    """One row per (network, source_platform, source, type): files, bytes — each distinct storage_key counted once
    (a file shared by several ads used to be summed per row: ~2× and it blocked every new download). SQL aggregates:
    the Python loop over 80k rows took 16 s on /api/storage/usage. Cached 30 s: the media workers ask per file."""
    import time

    global _files_cache
    if not fresh and time.monotonic() - _files_cache[0] < 30:
        return _files_cache[1]
    one = (select(Creative.storage_key, func.min(Creative.network).label("network"), func.min(Creative.source_platform).label("source_platform"),
                  func.min(Creative.source).label("source"), func.min(Creative.type).label("type"),
                  func.max(func.coalesce(Creative.stored_bytes, Creative.size_bytes, 0)).label("bytes"),
                  func.sum(func.coalesce(Creative.pinned, 0)).label("pinned"))
           .where(Creative.status == "stored", Creative.storage_key.is_not(None)).group_by(Creative.storage_key).subquery())
    rows = db.execute(select(one.c.network, one.c.source_platform, one.c.source, one.c.type, func.count().label("files"),
                             func.sum(one.c.bytes).label("bytes"), func.sum(one.c.pinned).label("pinned"))
                      .group_by(one.c.network, one.c.source_platform, one.c.source, one.c.type)).all()
    _files_cache = (time.monotonic(), rows)
    return rows


def used_by_bucket(db: Session, fresh: bool = False) -> dict[str, dict]:
    """Stored bytes / files per bucket (see stored_files)."""
    out = {b: {"bytes": 0, "videos": 0, "images": 0} for b in BUCKETS}
    for r in stored_files(db, fresh):
        b = out[bucket_of(_network(r))]
        b["bytes"] += int(r.bytes or 0)
        b["videos" if r.type == "video" else "images"] += r.files
    return out


def bucket_budget(bucket: str) -> int:
    return int(MAX_STORAGE_BYTES * shares()[bucket])


def videos_today_by_bucket(db: Session) -> dict[str, int]:
    out = {b: 0 for b in BUCKETS}
    for r in db.execute(select(Creative.network, Creative.source_platform, Creative.source, func.count(func.distinct(Creative.storage_key)).label("n"))
                        .where(Creative.type == "video", Creative.status == "stored", Creative.stored_at >= day_start())
                        .group_by(Creative.network, Creative.source_platform, Creative.source)):
        out[bucket_of(_network(r))] += r.n
    return out


def videos_today(db: Session, bucket: str | None = None) -> int:
    by = videos_today_by_bucket(db)
    return by[bucket] if bucket else sum(by.values())


def video_quota_left(db: Session, bucket: str | None = None) -> int | None:
    q = cfg()["video_daily_quota"]
    if q <= 0:
        return None
    if bucket is None:
        return max(q - videos_today(db), 0)
    return max(int(q * shares()[bucket]) - videos_today(db, bucket), 0)


def protected_products(db: Session) -> set[int]:
    ids: set[int] = set()
    ids |= {p for (p,) in db.execute(select(Vote.product_id).where(Vote.decision.in_(["LOVE", "TEST", "WATCH"])))}
    ids |= {p for (p,) in db.execute(select(Ad.product_id).where(Ad.saved_by.is_not(None), Ad.product_id.is_not(None)).distinct())}
    return ids


def _tree_bytes(d) -> int:
    """scandir: on Windows DirEntry.stat() is free (rglob + Path.stat() was one syscall per file, 2.5 s for 2k files)."""
    with os.scandir(d) as it:
        return sum(_tree_bytes(e.path) if e.is_dir(follow_symlinks=False) else e.stat().st_size for e in it)


def usage(db: Session) -> dict:
    out: dict = {"video": {"count": 0, "bytes": 0}, "image": {"count": 0, "bytes": 0}, "archived": 0, "pending": 0, "pinned": 0}
    files = stored_files(db, fresh=True)
    for r in files:
        out[r.type]["count"] += r.files
        out[r.type]["bytes"] += int(r.bytes or 0)  # stored_bytes when available, each file once
    for st, n in db.execute(select(Creative.status, func.count()).where(Creative.status.in_(["archived", "pending"])).group_by(Creative.status)):
        out[st] = n
    out["pinned"] = sum(int(r.pinned or 0) for r in files)  # pinned rows in the vault — same scan, not a second one (no index on pinned)
    raw = DATA_DIR / "raw"
    out["raw_bytes"] = _tree_bytes(raw) if BACKEND == "local" and raw.is_dir() else 0
    out["config"] = cfg()
    today = videos_today_by_bucket(db)
    out["today"] = {"videos": sum(today.values()), "quota": cfg()["video_daily_quota"], "day_start": day_start(),
                    "next_rollover": day_start() + timedelta(days=1)}
    out["backend"] = BACKEND
    used, sh, quota = used_by_bucket(db), shares(), cfg()["video_daily_quota"]  # from the stored_files just computed
    out["max_bytes"] = MAX_STORAGE_BYTES
    out["buckets"] = [{"key": b, "label": label, "share": round(sh[b], 3), "budget_bytes": bucket_budget(b), **used[b],
                       "videos_today": today[b], "video_quota": int(quota * sh[b]) if quota > 0 else None}
                      for b, label in BUCKETS.items()]
    return out


def trim_over_budget(db: Session, prot: set[int], now: datetime, dry_run: bool = False) -> dict:
    """A bucket above its share loses its weakest unprotected files — videos and images (lowest ad force, oldest
    first) until it fits. Thumbnails and metadata stay — the card keeps its poster frame, exactly like the end-of-day
    rollover. Local disk has no bucket shares (media._skip_reason): only R2 is trimmed."""
    from .models import Ad

    res = {"trimmed": 0, "trimmed_bytes": 0}
    if BACKEND not in ("r2", "s3"):
        return res
    used = used_by_bucket(db, fresh=True)
    for b in BUCKETS:
        over = used[b]["bytes"] - bucket_budget(b)
        if over <= 0:
            continue
        # ponytail: loads every stored row of the vault when a bucket is over; fine for a nightly job on R2
        rows = db.execute(select(Creative, Ad.force_score).join(Ad, Ad.id == Creative.ad_id, isouter=True).where(
            Creative.status == "stored", Creative.pinned.is_not(True))
            .order_by(Ad.force_score.asc().nulls_first(), Creative.stored_at.asc())).all()
        groups: dict[str, list[Creative]] = {}
        for cr, _ in rows:
            if bucket_of(_network(cr)) == b and not (cr.product_id and cr.product_id in prot):
                groups.setdefault(cr.storage_key, []).append(cr)
        keep = {k for (k,) in db.execute(select(Creative.storage_key).where(Creative.status == "stored", Creative.pinned.is_(True)))}
        for key, group in groups.items():
            if over <= 0:
                break
            if key in keep:
                continue
            size = group[0].stored_bytes or group[0].size_bytes or 0
            over -= size
            res["trimmed"] += len(group)
            res["trimmed_bytes"] += size
            if dry_run:
                continue
            try:
                store().delete(key)
            except Exception:
                continue
            for cr in group:
                cr.status, cr.archived_at = "archived", now
                cr.error = f"Ngăn {BUCKETS[b]} vượt ngân sách {shares()[b]:.0%} kho — gỡ file yếu nhất (ghim 📌 để giữ)"
    if not dry_run:
        db.flush()
    return res


def run_cleanup(db: Session, dry_run: bool = False, reset: bool = False) -> dict:
    """reset=True: wipe every unprotected video now (fresh start), however many were collected today."""
    c = cfg()
    now = datetime.utcnow()
    res = {"dry_run": dry_run, "archived": 0, "freed_bytes": 0, "kept_protected": 0, "kept_shared": 0, "raw_deleted": 0,
           "events_deleted": 0, "tmp_deleted": 0}
    prot = protected_products(db)

    # Reconcile: rows marked "stored" whose file vanished (deleted by hand / disk cleanup) → archived, so the UI
    # shows the poster instead of a broken player. Local disk only (a HEAD per object on R2 would be slow).
    res["missing_marked"] = 0
    if BACKEND == "local":
        from sqlalchemy import update

        from .db import read_only

        with read_only():  # ~20k file stats: never inside a write transaction (it blocked every writer for seconds)
            rows = db.execute(select(Creative.id, Creative.storage_key).where(Creative.status == "stored")).all()
            db.rollback()
        missing = [cid for cid, key in rows if key and not store().path(key).exists()]
        res["missing_marked"] = len(missing)
        if missing and not dry_run:
            db.execute(update(Creative).where(Creative.id.in_(missing), Creative.status == "stored")
                       .values(status="archived", archived_at=now, error="File không còn trong kho"))

    for ctype, days in (("video", c["video_keep_days"] or int(reset)), ("image", c["image_days"])):
        if days <= 0:
            continue
        # videos: whole days (today's batch survives until the team's midnight); images: rolling N days
        cutoff = (now if reset else day_start(now) - timedelta(days=days - 1)) if ctype == "video" else now - timedelta(days=days)
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
            size = group[0].stored_bytes or (store().size(key) if key else 0) or group[0].size_bytes or 0
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
                cr.status, cr.archived_at = "archived", now
                cr.error = ("Hết ngày: video được thay bằng lứa mới (ghim 📌 để giữ lâu)" if ctype == "video"
                            else f"Đã dọn khỏi kho sau {days} ngày (giữ ảnh bìa + thông tin)")
        if not dry_run:
            db.flush()

    res.update(trim_over_budget(db, prot, now, dry_run))
    res["freed_bytes"] += res["trimmed_bytes"]

    res["stale_pending"] = 0
    stale = [] if c["video_daily_quota"] <= 0 else db.scalars(select(Creative).where(
        Creative.type == "video", Creative.status == "pending", Creative.collected_at < now - timedelta(days=2))).all()
    res["stale_pending"] = len(stale)
    if not dry_run:
        for cr in stale:
            cr.status, cr.error = "skipped", "Bỏ qua: chờ quá 2 ngày chưa đến lượt trong hạn mức video/ngày"

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
        import shutil

        for f in tmp.iterdir():  # leaked *.part downloads
            if now.timestamp() - f.stat().st_mtime > 86400:
                res["tmp_deleted"] += 1
                if not dry_run:
                    shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)

    if not dry_run:
        db.commit()
    return res
