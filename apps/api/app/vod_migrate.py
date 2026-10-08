"""One-off: move the media vault to Video-on-Demand (HLS) on Cloudflare R2, under a hard size budget.

  python -m app.vod_migrate --plan                 numbers only, changes nothing
  python -m app.vod_migrate --transcode            keep-set videos → local HLS ladder; delete their originals;
                                                   delete videos nobody needs (non-target markets / stopped ads)
  python -m app.vod_migrate --upload --max-gb 20   push thumbs → HLS → images to R2 in priority order and stop
                                                   at the budget; anything not uploaded is archived (poster kept)

Keep-set = video/image of an ad that runs in a TARGET_MARKETS country and is still active, plus anything pinned
or attached to a product the team voted on / ordered / tested (retention.protected_products).
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import hls
from .db import SessionLocal
from .models import Ad, Creative
from .retention import protected_products
from .storage import DATA_DIR

GB = 1024 ** 3


def keep_ids(db) -> set[int]:
    prot = protected_products(db)
    rows = db.execute(select(Creative.id, Creative.pinned, Creative.product_id, Ad.in_target, Ad.is_active)
                      .join(Ad, Ad.id == Creative.ad_id, isouter=True)).all()
    return {cid for cid, pinned, pid, tgt, act in rows if pinned or (pid in prot) or (tgt and act)}


def plan():
    with SessionLocal() as db:
        keep = keep_ids(db)
        vids = db.scalars(select(Creative).where(Creative.type == "video", Creative.status == "stored")).all()
        imgs = db.scalars(select(Creative).where(Creative.type == "image", Creative.status == "stored")).all()
        k_keys = {c.storage_key for c in vids if c.id in keep and not c.hls_key}
        d_keys = {c.storage_key for c in vids if c.id not in keep} - k_keys - {c.storage_key for c in vids if c.hls_key}
        size = lambda keys: sum((DATA_DIR / k).stat().st_size for k in keys if k and (DATA_DIR / k).exists())
        ki = [c for c in imgs if c.id in keep]
        print(f"videos to convert to HLS : {len(k_keys)} files · originals {size(k_keys) / GB:.2f} GB")
        print(f"videos to delete         : {len(d_keys)} files · {size(d_keys) / GB:.2f} GB (non-target markets / stopped ads)")
        print(f"already HLS              : {len({c.hls_key for c in vids if c.hls_key})}")
        print(f"images to keep in cloud  : {len(ki)} · {sum(c.size_bytes or 0 for c in ki) / GB:.2f} GB (of {len(imgs)})")


def _one(key: str) -> tuple[str, dict | None, str | None]:
    src = DATA_DIR / key
    sha = Path(key).stem
    out = DATA_DIR / "hls" / sha
    try:
        return key, hls.transcode(src, out), None
    except Exception as e:
        return key, None, str(e)[:240]


def transcode(workers: int):
    with SessionLocal() as db:
        keep = keep_ids(db)
        vids = db.scalars(select(Creative).where(Creative.type == "video", Creative.status == "stored")).all()
        db.commit()
    by_key: dict[str, list[int]] = {}
    for c in vids:
        if c.storage_key and not c.hls_key:
            by_key.setdefault(c.storage_key, []).append(c.id)
    todo = [k for k, ids in by_key.items() if any(i in keep for i in ids) and (DATA_DIR / k).exists()]
    drop = [k for k, ids in by_key.items() if not any(i in keep for i in ids)]
    print(f"converting {len(todo)} videos with {workers} workers…", flush=True)
    t0, ok, fail, done_bytes = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, k) for k in todo]
        for i, f in enumerate(as_completed(futs), 1):
            key, res, err = f.result()
            with SessionLocal() as db:
                cs = db.scalars(select(Creative).where(Creative.storage_key == key)).all()
                if res:
                    sha = Path(key).stem
                    top = hls.top_rendition(res["renditions"])
                    for c in cs:
                        c.hls_key = f"hls/{sha}/master.m3u8"
                        c.download_key = c.storage_key = f"hls/{sha}/{top}/index.m4s"
                        c.renditions, c.stored_bytes = res["renditions"], res["bytes"]
                        c.width, c.height, c.duration_sec, c.mime = res["width"], res["height"], res["duration"], "video/mp4"
                    db.commit()
                    (DATA_DIR / key).unlink(missing_ok=True)  # original no longer needed
                    ok += 1
                    done_bytes += res["bytes"]
                else:
                    for c in cs:
                        c.error = f"HLS: {err}"
                    db.commit()
                    fail += 1
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} · ok {ok} · lỗi {fail} · HLS {done_bytes / GB:.2f} GB · {time.time() - t0:.0f}s", flush=True)
    freed = 0
    with SessionLocal() as db:
        for key in drop:
            p = DATA_DIR / key
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
            for c in db.scalars(select(Creative).where(Creative.storage_key == key)).all():
                c.status, c.archived_at, c.error = "archived", datetime.utcnow(), "Đã xoá: không thuộc thị trường mục tiêu hoặc quảng cáo đã dừng"
        db.commit()
    print(f"deleted {len(drop)} unneeded videos · freed {freed / GB:.2f} GB")


def upload(max_gb: float, workers: int = 16):
    """Parallel upload (one HTTPS round-trip per file is ~0.8 s to APAC — sequential was ~1 file/s)."""
    import threading

    from .models import Product
    from .r2_sync import BUCKET, s3

    c3 = s3()
    budget = int(max_gb * GB)
    existing: dict[str, int] = {}
    for page in c3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for o in page.get("Contents", []):
            existing[o["Key"]] = o["Size"]
    used = sum(existing.values())
    print(f"R2 hiện dùng {used / GB:.2f} GB ({len(existing)} file) · ngân sách {max_gb} GB · {workers} luồng", flush=True)
    c3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"],
        "ExposeHeaders": ["Content-Length", "Content-Range", "Accept-Ranges", "ETag"], "MaxAgeSeconds": 86400}]})
    print("✓ CORS bucket đã bật (GET/HEAD, Range)", flush=True)
    lock = threading.Lock()
    state = {"used": used, "files": 0, "over": 0}

    def reserve(size: int) -> bool:
        with lock:
            if state["used"] + size > budget:
                state["over"] += 1
                return False
            state["used"] += size
            return True

    def put(local: Path, key: str, ctype: str) -> bool:
        if key in existing:
            return True
        size = local.stat().st_size
        if not reserve(size):
            return False
        try:
            c3.upload_file(str(local), BUCKET, key,
                           ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=31536000, immutable"})
        except Exception:
            with lock:
                state["used"] -= size
            raise
        with lock:
            existing[key] = size
            state["files"] += 1
        return True

    def run(label: str, tasks: list[tuple[Path, str, str]]):
        t0 = time.time()
        todo = [t for t in tasks if t[1] not in existing]
        print(f"{label}: {len(todo)} file cần đẩy ({len(tasks) - len(todo)} đã có)", flush=True)
        errors = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(put, *t) for t in todo]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    f.result()
                except Exception:
                    errors += 1
                if i % 500 == 0 or i == len(futs):
                    print(f"  {label} {i}/{len(futs)} · lỗi {errors} · R2 {state['used'] / GB:.2f} GB · {time.time() - t0:.0f}s", flush=True)

    with SessionLocal() as db:
        keep = keep_ids(db)
        crs = db.scalars(select(Creative).where(Creative.status == "stored")).all()
        rank = {p.id: (p.potential or {}).get("vector", {}).get("opportunity", 0) for p in db.scalars(select(Product))}
        db.commit()
    # 1) posters — tiny, every card needs one
    thumbs = {c.thumb_key for c in crs if c.thumb_key and (DATA_DIR / c.thumb_key).exists()}
    run("ảnh bìa", [(DATA_DIR / k, k, "image/jpeg") for k in sorted(thumbs)])
    # 2) HLS: all media files first (best products first), playlists only afterwards
    ladders, seen = [], set()
    for c in sorted([c for c in crs if c.hls_key], key=lambda c: -rank.get(c.product_id, 0)):
        d = Path(c.hls_key).parent.as_posix()
        if d not in seen and (DATA_DIR / d).exists():
            seen.add(d)
            ladders.append(d)
    media, lists = [], []
    media_of: dict[str, list[str]] = {}
    for d in ladders:
        for f, rel in hls.files(DATA_DIR / d):
            (lists if f.suffix == ".m3u8" else media).append((f, f"{d}/{rel}", hls.content_type(f), d))
            if f.suffix != ".m3u8":
                media_of.setdefault(d, []).append(f"{d}/{rel}")
    run("video HLS (media)", [t[:3] for t in media])
    # a ladder's playlists go up only when *that* ladder's media are all in the bucket (never a broken player)
    run("video HLS (playlist)", [t[:3] for t in lists if all(k in existing for k in media_of.get(t[3], []))])
    # 3) still images of kept ads
    imgs = sorted([c for c in crs if c.type == "image" and c.id in keep and c.storage_key and (DATA_DIR / c.storage_key).exists()],
                  key=lambda c: -rank.get(c.product_id, 0))
    run("ảnh quảng cáo", [(DATA_DIR / c.storage_key, c.storage_key, c.mime or "image/jpeg") for c in imgs])
    # anything not in the cloud is archived (poster + metadata stay)
    with SessionLocal() as db:
        n = 0
        for c in db.scalars(select(Creative).where(Creative.status == "stored")).all():
            in_cloud = (c.hls_key and c.hls_key in existing) or (c.type == "image" and c.storage_key in existing)
            if not in_cloud:
                c.status, c.archived_at = "archived", datetime.utcnow()
                c.error = "Không đưa lên cloud (ngoài thị trường mục tiêu / quá ngân sách)"
                n += 1
        db.commit()
    print(f"✓ xong · đã đẩy {state['files']} file · R2 dùng {state['used'] / GB:.2f} GB / {max_gb} GB · "
          f"bỏ qua vì ngân sách {state['over']} · archived {n}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--transcode", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--upload-workers", type=int, default=16)
    ap.add_argument("--max-gb", type=float, default=float(os.getenv("R2_MAX_GB", "20")))
    a = ap.parse_args()
    if a.plan or not (a.transcode or a.upload):
        plan()
    if a.transcode:
        transcode(a.workers)
    if a.upload:
        upload(a.max_gb, a.upload_workers)


if __name__ == "__main__":
    main()
