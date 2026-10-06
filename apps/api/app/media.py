"""Creative Vault worker (discovery §3-§6).

pending Creative → download original (CDN URLs expire within hours, so this runs right after
ingest) → sha256 content address → store creatives/<aa>/<sha>.<ext> → poster/thumbnail →
perceptual hash (dHash) → creative family (near-duplicate grouping).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .db import SessionLocal
from .events import publish
from .models import Creative
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
FFPROBE = shutil.which("ffprobe")
FFMPEG = shutil.which("ffmpeg")

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="media")
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


def process_creative(cid: int):
    with SessionLocal() as db:
        c = db.get(Creative, cid)
        if not c or c.status == "stored":
            return
        c.attempts += 1
        tmp_dir = DATA_DIR / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".part", dir=tmp_dir)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            # same original URL already stored → reuse
            dup = db.scalar(select(Creative).where(Creative.source_url == c.source_url, Creative.status == "stored", Creative.id != c.id))
            if dup:
                for k in ("storage_key", "thumb_key", "sha256", "phash", "mime", "size_bytes", "width", "height", "duration_sec", "family_id"):
                    setattr(c, k, getattr(dup, k))
            else:
                db.commit()  # never hold a DB transaction (and its write lock) during a network download
                sha, mime, size = _download(c.source_url, tmp)
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
                c.family_id = assign_family(db, c)
            c.status, c.error, c.stored_at = "stored", None, datetime.utcnow()
            if c.product_id:
                from .models import Product

                p = db.get(Product, c.product_id)
                cur = db.get(Creative, p.cover_creative_id) if p and p.cover_creative_id else None
                if p and (cur is None or (cur.type != "video" and c.type == "video")):
                    p.cover_creative_id = c.id  # prefer a video as the product cover
            db.commit()
            publish("CREATIVE_STORED", {"creative_id": c.id, "type": c.type}, product_id=c.product_id, persist=False)
        except Expired as e:
            c.status, c.error = "expired", f"CDN URL hết hạn ({e}) — thu thập lại ad để lấy link mới"
            db.commit()
        except Exception as e:
            c.status = "failed" if c.attempts >= 3 else "pending"
            c.error = f"{type(e).__name__}: {e}"[:250]
            db.commit()
        finally:
            tmp.unlink(missing_ok=True)
            with _lock:
                _inflight.discard(cid)


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


def enqueue(ids: list[int]):
    for cid in ids:
        with _lock:
            if cid in _inflight:
                continue
            _inflight.add(cid)
        _pool.submit(process_creative, cid)


def enqueue_pending(limit: int = 500) -> int:
    with SessionLocal() as db:
        ids = list(db.scalars(select(Creative.id).where(Creative.status == "pending").order_by(Creative.id.desc()).limit(limit)))
    enqueue(ids)
    return len(ids)
