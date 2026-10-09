"""Creative Vault worker (discovery §3-§6).

Spy Ads is a research tool, not a media host: collecting an ad saves its metadata + one small JPEG thumbnail
(thumbs/, ≤480 px) — images and videos alike. The player streams the source URL on Play. VIDEO_DOWNLOAD=1 brings
the old full-file vault back; a single creative is still saved on demand (POST /api/creatives/{id}/retry).

pending Creative → download original (CDN URLs expire within hours, so this runs right after
ingest) → sha256 content address → store creatives/<aa>/<sha>.<ext> → poster/thumbnail →
perceptual hash (dHash) → creative family (near-duplicate grouping).
"""
from __future__ import annotations

import hashlib
import io
import itertools
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from .db import SessionLocal, read_only
from .events import publish
from .models import Creative
from .platforms import NETWORKS
from .storage import DATA_DIR, store

log = logging.getLogger(__name__)

MAX_BYTES = int(os.getenv("MEDIA_MAX_MB", "200")) * 1024 * 1024
WORKERS = int(os.getenv("MEDIA_WORKERS", "3"))
FAMILY_DISTANCE = 10  # dHash hamming distance (0-64) to treat two creatives as one family
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Referer": "https://www.facebook.com/",
}
EXT = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "image/jpeg": ".jpg", "image/png": ".png",
       "image/webp": ".webp", "image/gif": ".gif"}


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = shutil.which("ffmpeg") or _ffmpeg_exe()  # PATH, else the imageio_ffmpeg binary
FFPROBE = shutil.which("ffprobe") or (FFMPEG and next((str(p) for p in Path(FFMPEG).parent.glob("ffprobe*") if p.is_file()), None))
if not FFMPEG:
    log.warning("ffmpeg not found (PATH / imageio_ffmpeg): video posters, phash and creative families are disabled")

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="media")  # videos: download + ffmpeg (slow)
# images are a quick download + thumbnail: own pool, so thousands of images never wait behind video transcodes
_img_pool = ThreadPoolExecutor(max_workers=int(os.getenv("IMAGE_WORKERS", "6")), thread_name_prefix="media-img")
_inflight: set[int] = set()
_lock = threading.Lock()


class Expired(Exception):
    pass


def _download(url: str, dest: Path) -> tuple[str, str, int]:
    h = hashlib.sha256()
    size = 0
    with httpx.stream("GET", url, headers=HEADERS, timeout=120, follow_redirects=True) as r:
        if r.status_code in (403, 410):
            raise Expired(f"HTTP {r.status_code}")
        r.raise_for_status()
        mime = r.headers.get("content-type", "").split(";")[0].strip() or "application/octet-stream"
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError(f"file > {MAX_BYTES // 1048576} MB")
                h.update(chunk)
                f.write(chunk)
    return h.hexdigest(), mime, size


def dhash(img_bytes: bytes) -> tuple[str, int, int]:
    from PIL import Image

    im = Image.open(io.BytesIO(img_bytes))
    w, h = im.size
    g = im.convert("L").resize((9, 8))
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}", w, h


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _thumb_jpeg(img_bytes: bytes, max_w: int = 480) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82)
    return out.getvalue()


IMAGE_SHRINK_BYTES = int(os.getenv("IMAGE_SHRINK_KB", "200")) * 1024
IMAGE_MAX_PX = int(os.getenv("IMAGE_MAX_PX", "1080"))


def _shrink_image(path: Path) -> str | None:
    """Re-encode a large image in place as JPEG (longest side ≤ IMAGE_MAX_PX, q82). Returns the new mime, or None
    when it would not get smaller (GIF animations, tiny PNGs) — then the original is kept."""
    from PIL import Image

    try:
        im = Image.open(path)
        if getattr(im, "is_animated", False):
            return None
        im = im.convert("RGB")
        im.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        if out.tell() >= path.stat().st_size:
            return None
        path.write_bytes(out.getvalue())
        return "image/jpeg"
    except Exception as e:
        log.warning("image shrink failed (%s): %s", path.name, e)
        return None


def _probe(path: Path) -> dict:
    if not FFPROBE:
        return {}
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "default=nw=1", str(path)],
                             capture_output=True, text=True, timeout=30).stdout
        kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return {"width": int(kv["width"]) if kv.get("width", "").isdigit() else None,
                "height": int(kv["height"]) if kv.get("height", "").isdigit() else None,
                "duration": float(kv["duration"]) if kv.get("duration") not in (None, "N/A") else None}
    except Exception:
        return {}


def _frame(path: Path) -> bytes | None:
    if not FFMPEG:
        return None
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1", "-f", "image2", "-"],
                           capture_output=True, timeout=60)
        return r.stdout or None
    except Exception:
        return None


def source_alive(url: str | None, margin_s: int = 600) -> bool:
    """Signed CDN links carry their expiry (fbcdn `oe=<hex unix time>`, TikTok `x-expires=<unix>`, tiktokcdn video
    `/<sig>/<hex unix time>/video/`, TikTok Ad Library `/api/v1/cdn/<unix>/`); true while the original is still
    playable. Links without an expiry (AliExpress / 1688 images) stay playable."""
    if not url:
        return False
    if m := (re.search(r"[?&]oe=([0-9A-Fa-f]+)", url) or re.search(r"tiktokcdn[\w.-]*/[0-9a-f]{32}/([0-9a-f]{8})/", url)):
        return int(m.group(1), 16) - margin_s > time.time()
    if m := (re.search(r"[?&]x-expires=(\d+)", url) or re.search(r"tiktok\.com/api/v1/cdn/(\d{10})/", url)):
        return int(m.group(1)) - margin_s > time.time()
    return True


def _strip(url: str | None) -> str:
    """CDN URL without its signature (?oe=…, ?x-expires=…) — the same media re-collected gets a new query string."""
    return (url or "").split("?", 1)[0]


_SHARED = ("storage_key", "thumb_key", "sha256", "phash", "mime", "size_bytes", "width", "height", "duration_sec", "family_id",
           "stored_bytes")


def _twin(db, c: Creative, cond) -> Creative | None:
    """Oldest stored row with the same media (prefer one of the same ad); None when this is the first copy."""
    return db.scalar(select(Creative).where(cond, Creative.status == "stored", Creative.id != c.id)
                     .order_by((Creative.ad_id == c.ad_id).desc(), Creative.id).limit(1))


def _same_owner(a: Creative, b: Creative) -> bool:
    return (a.ad_id, a.product_id) == (b.ad_id, b.product_id) and (a.ad_id or a.product_id) is not None


def _in_store(d: Creative) -> bool:
    return bool(d.storage_key) and store().exists(d.storage_key)


def process_creative(cid: int):
    try:
        _process(cid)
    finally:
        with _lock:
            _inflight.discard(cid)


def _process(cid: int):
    from .retention import BUCKETS, _network, bucket_of, video_quota_left

    with SessionLocal() as db:
        # decide on a read-only (deferred BEGIN) transaction: the budget / quota scans used to hold the SQLite write
        # lock for seconds per creative and live-search ingest failed with "database is locked"
        with read_only():
            c = db.get(Creative, cid)
            if not c or c.status == "stored":
                return
            base = _strip(c.source_url)
            # same ad / product re-collected with a fresh CDN signature (indexed by owner: a scan over 80k source_urls
            # per creative is what the old equality lookup cost); cross-ad copies are caught by sha256 after download
            owner = Creative.ad_id == c.ad_id if c.ad_id else Creative.product_id == c.product_id
            dup = _twin(db, c, owner & Creative.source_url.startswith(base, autoescape=True))
            if dup and (_strip(dup.source_url) != base or not _in_store(dup)):
                dup = None
            skip = wait = None
            if not dup:
                skip = _skip_reason(db, c)
                if not skip and c.type == "video":
                    b = bucket_of(_network(c))
                    if video_quota_left(db, b) == 0:  # this network's share of today's batch is full → wait for tomorrow
                        wait = f"Chờ: {BUCKETS[b]} đã đủ hạn mức video hôm nay (VIDEO_DAILY_QUOTA × STORAGE_SHARES)"
            db.commit()  # end the read transaction; every write below is its own short BEGIN IMMEDIATE
        if skip or wait:
            if skip:
                c.status = "skipped"
            c.error = skip or wait
            db.commit()
            return
        tmp_dir = DATA_DIR / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".part", dir=tmp_dir)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if not dup:  # no DB transaction is open here: never hold the write lock during a network download
                sha, mime, size = _download(c.source_url, tmp)
                with SessionLocal() as rdb, read_only():
                    dup = _twin(rdb, c, Creative.sha256 == sha)
                if dup and not _in_store(dup):
                    dup = None  # twin's file is gone (cleanup will archive it): store this copy
            if dup and _same_owner(dup, c):
                db.delete(c)  # same ad, same bytes, fresh CDN signature: the first stored row is enough
                db.commit()
                return
            if dup:  # same media already in the vault (another ad / product): share the file, skip transcode
                for k in _SHARED:
                    setattr(c, k, getattr(dup, k))
            else:
                if c.type == "image" and size > IMAGE_SHRINK_BYTES:
                    mime = _shrink_image(tmp) or mime  # originals average ~480 KB; 1080 px JPEG is plenty for a card
                ext = EXT.get(mime) or Path(urlparse(c.source_url).path).suffix[:5] or (".mp4" if c.type == "video" else ".jpg")
                key = f"creatives/{sha[:2]}/{sha}{ext}"
                c.sha256, c.mime, c.size_bytes, c.storage_key = sha, mime, size, key
                poster: bytes | None = None
                if c.type == "image":
                    poster = tmp.read_bytes()
                else:
                    meta = _probe(tmp)
                    c.width, c.height, c.duration_sec = meta.get("width"), meta.get("height"), meta.get("duration")
                    if c.preview_source_url:
                        try:
                            pr = httpx.get(c.preview_source_url, headers=HEADERS, timeout=60, follow_redirects=True)
                            if pr.status_code == 200:
                                poster = pr.content
                        except httpx.HTTPError:
                            pass
                    poster = poster or _frame(tmp)
                c.stored_bytes = tmp.stat().st_size  # after shrink, what the store holds — put_file moves/unlinks tmp
                if not store().exists(key):
                    store().put_file(key, tmp, mime)
                if poster:
                    try:
                        c.phash, w, h = dhash(poster)
                        if c.type == "image":
                            c.width, c.height = w, h
                        tkey = f"thumbs/{sha[:2]}/{sha}.jpg"
                        if not store().exists(tkey):
                            store().put(tkey, _thumb_jpeg(poster), "image/jpeg")
                        c.thumb_key = tkey
                    except Exception as e:  # corrupt poster — keep the media
                        log.warning("thumbnail failed for creative %s: %s", cid, e)
                # a scan over every creative's phash (~50k rows): on a read-only session, or it holds the SQLite write
                # lock for seconds per creative and live-search ingest fails with "database is locked"
                with SessionLocal() as rdb, read_only():
                    c.family_id = assign_family(rdb, c)
            c.status, c.error, c.stored_at = "stored", None, datetime.utcnow()
            if c.product_id:
                from .models import Product

                p = db.get(Product, c.product_id)
                cur = db.get(Creative, p.cover_creative_id) if p and p.cover_creative_id else None
                if p and (cur is None or cur.status != "stored" or (cur.type != "video" and c.type == "video")):
                    p.cover_creative_id = c.id  # prefer a video as the product cover
            db.commit()
            publish("CREATIVE_STORED", {"creative_id": c.id, "type": c.type}, product_id=c.product_id, persist=False)
        except OperationalError:  # busy_timeout ran out — not this creative's fault: stays pending, no attempt counted
            log.exception("creative %s: database locked, will retry", cid)
            db.rollback()
        except Expired as e:
            db.rollback()
            c.status, c.error = "expired", f"CDN URL hết hạn ({e}) — thu thập lại ad để lấy link mới"
            db.commit()
        except Exception as e:
            db.rollback()
            c.attempts += 1  # attempts = failed tries; a lock timeout above is not one
            c.status = "failed" if c.attempts >= 3 else "pending"
            c.error = f"{type(e).__name__}: {e}"[:250]
            db.commit()
        finally:
            tmp.unlink(missing_ok=True)


VIDEO_DOWNLOAD = os.getenv("VIDEO_DOWNLOAD", "0") == "1"  # bulk-download every collected video into the vault
MIN_FREE_BYTES = int(float(os.getenv("MIN_FREE_GB", "5")) * 1024 ** 3)


def _skip_reason(db, c: Creative) -> str | None:
    """Every creative is kept (CDN links expire, so a skipped file is lost for good). Local disk: only stop when the
    disk itself runs low. R2: never more than the creative's network share of R2_MAX_GB (retention.STORAGE_SHARES)."""
    from .retention import BUCKETS, _network, bucket_budget, bucket_of, shares, used_by_bucket
    from .storage import BACKEND

    if BACKEND not in ("r2", "s3"):
        free = shutil.disk_usage(store().root).free  # store() creates DATA_DIR on first use
        if free < MIN_FREE_BYTES:
            return f"Bỏ qua: ổ đĩa chỉ còn {free / 1024 ** 3:.1f} GB trống (MIN_FREE_GB)"
        return None  # local vault keeps everything; STORAGE_SHARES only split R2_MAX_GB
    b = bucket_of(_network(c))
    if used_by_bucket(db)[b]["bytes"] >= bucket_budget(b) and not c.pinned:
        return f"Bỏ qua: ngăn {BUCKETS[b]} đã dùng hết {shares()[b]:.0%} kho ({bucket_budget(b) / 1024 ** 3:.1f} GB)"
    return None


def assign_family(db, c: Creative) -> int:
    if c.sha256:
        same = db.scalar(select(Creative.family_id).where(Creative.sha256 == c.sha256, Creative.family_id.is_not(None), Creative.id != c.id))
        if same:
            return same
    if c.phash:
        for fid, ph in db.execute(select(Creative.family_id, Creative.phash).where(
                Creative.phash.is_not(None), Creative.family_id.is_not(None), Creative.id != c.id, Creative.type == c.type)):
            if hamming(ph, c.phash) <= FAMILY_DISTANCE:
                return fid
    return c.id


def grab_thumb(cid: int) -> str | None:
    """Save one small JPEG (≤480 px) for a creative the moment it is collected: a video's cover frame, an image itself.
    Signed CDN links (Meta / TikTok) die within hours and marketplace CDNs block hotlinking, so this is what the cards
    show. Returns the thumb key (None when the source is gone); also called on demand by GET /api/creatives/{id}/thumb."""
    try:
        with SessionLocal() as db, read_only():
            c = db.get(Creative, cid)
            if not c:
                return None
            if c.thumb_key:
                return c.thumb_key
            url = c.preview_source_url if c.type == "video" else c.source_url
            if not source_alive(url):
                return None
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            if c.type == "image" and r.status_code in (403, 404, 410):
                with SessionLocal() as db:
                    db.execute(update(Creative).where(Creative.id == cid, Creative.status == "pending")
                               .values(status="expired", error=f"Ảnh gốc không còn (HTTP {r.status_code})"))
                    db.commit()
            return None
        sha = hashlib.sha256(r.content).hexdigest()
        tkey = f"thumbs/{sha[:2]}/{sha}.jpg"
        if not store().exists(tkey):
            store().put(tkey, _thumb_jpeg(r.content), "image/jpeg")
        with SessionLocal() as db:
            db.execute(update(Creative).where(Creative.id == cid, Creative.thumb_key.is_(None)).values(thumb_key=tkey))
            db.commit()
        return tkey
    except Exception as e:  # network blip / not an image: the next enqueue_pending tick tries again
        log.warning("thumb grab failed for creative %s: %s", cid, e)
        return None


def enqueue(ids: list[int], force: bool = False):
    """Every creative: one small thumbnail, nothing else. The full file (video / original image) only when `force`
    (a user asked to save that one) or VIDEO_DOWNLOAD=1 — collecting ads never downloads media in bulk."""
    if not ids:
        return
    with SessionLocal() as db, read_only():
        types = dict(db.execute(select(Creative.id, Creative.type).where(Creative.id.in_(ids))).all())
    for cid in ids:  # submission order = priority order
        if not (force or (VIDEO_DOWNLOAD and types.get(cid) == "video")):
            _img_pool.submit(grab_thumb, cid)
            continue
        with _lock:
            if cid in _inflight:
                continue
            _inflight.add(cid)
        (_img_pool if types.get(cid) == "image" else _pool).submit(process_creative, cid)


def requeue_lost() -> int:
    """Creatives skipped / expired by the old storage rules (market filter, R2 budget, daily rollover) go back to the
    queue while their original link still works — the local vault keeps them all."""
    from sqlalchemy import or_

    with SessionLocal() as db:
        rows = db.scalars(select(Creative).where(or_(
            Creative.status == "skipped",
            (Creative.status == "archived") & (Creative.pinned.is_not(True))))).all()
        n = 0
        for c in rows:
            if source_alive(c.source_url):
                c.status, c.error, c.attempts = "pending", None, 0
                n += 1
        db.commit()
    return n


def expire_dead_links() -> int:
    """Pending creatives whose signed CDN link has already expired: downloading them can only 403, so they go to
    `expired` now (the next sync of the ad brings a fresh link) instead of clogging the queue for days."""
    with SessionLocal() as db, read_only():
        dead = [i for i, u in db.execute(select(Creative.id, Creative.source_url).where(Creative.status == "pending"))
                if not source_alive(u)]
    if dead:
        with SessionLocal() as db:
            for i in range(0, len(dead), 900):  # SQLite bind-parameter limit
                db.execute(update(Creative).where(Creative.id.in_(dead[i:i + 900]))
                           .values(status="expired", error="CDN URL hết hạn — thu thập lại ad để lấy link mới"))
            db.commit()
        log.info("media: %s pending creatives expired (dead CDN link)", len(dead))
    return len(dead)


def enqueue_pending(limit: int = 500) -> int:
    """Target-market ads first, then strongest ads (force score = days running + variants + placements), newest first
    among equals. Thumbnails always; full videos only with VIDEO_DOWNLOAD=1 and today's quota left."""
    from .models import Ad, Product
    from .retention import BUCKETS, bucket_of, video_quota_left

    expire_dead_links()
    with SessionLocal() as db, read_only():
        # what the team sees first (target markets, top-ranked products) gets its media first; then the strongest ads
        q = (select(Creative.id).join(Ad, Ad.id == Creative.ad_id, isouter=True)
             .join(Product, Product.id == Creative.product_id, isouter=True).where(Creative.status == "pending")
             .order_by(Ad.in_target.desc().nulls_last(), Product.opportunity_score.desc().nulls_last(),
                       Ad.force_score.desc().nulls_last(), Creative.id.desc()))
        # thumbnails still missing, last day only: older signed links are dead, the rest is fetched on view
        # (GET /api/creatives/{id}/thumb) — re-scanning 20k dead rows every tick would starve the fresh ones
        recent = Creative.collected_at >= datetime.utcnow() - timedelta(days=1)
        ids = list(db.scalars(q.where(Creative.thumb_key.is_(None), recent).limit(limit)))
        # videos: one lane per network bucket, interleaved, so a Meta backlog never starves TikTok / Snapchat;
        # lane size grows with the backlog (80 per tick never caught up with 18k pending)
        nets = {b: [n for n in NETWORKS if bucket_of(n) == b] for b in BUCKETS}
        backlog = {b: 0 for b in BUCKETS}
        for net, n in db.execute(select(Creative.network, func.count()).where(Creative.status == "pending", Creative.type == "video")
                                 .group_by(Creative.network)):
            if net in NETWORKS:
                backlog[bucket_of(net)] += n
        lanes = [list(db.scalars(q.where(Creative.type == "video", Creative.network.in_(nets[b])).limit(max(80, backlog[b] // 20))))
                 for b in BUCKETS if VIDEO_DOWNLOAD and backlog[b] and video_quota_left(db, b) != 0]
        ids += [cid for tier in itertools.zip_longest(*lanes) for cid in tier if cid is not None]
    enqueue(ids)
    return len(ids)
