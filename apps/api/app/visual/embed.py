"""Tier 2 — CLIP ViT-B/32 vision tower through onnxruntime (CPU, no torch / transformers).

Model: Xenova/clip-vit-base-patch32 ONNX export (MIT) in IMAGE_MODEL_DIR (default apps/api/data/models/clip-vit-b32):
`vision_model.onnx` (fp32, 352 MB) or `vision_model_quantized.onnx` (int8, 89 MB) — IMAGE_EMBED_MODEL=fp32|int8.
Measured on the Ryzen 5 5600H: fp32 43 ms / image (35 ms batched), int8 27 ms. Session loads lazily on first use and is
dropped after IMAGE_EMBED_IDLE_S without a call, so an idle API / worker holds no model in RAM.

Vector search: every embedding (float16 BYTEA in product_images) is held as one float32 matrix in RAM and scored with a
dot product. ponytail: 72k × 512 × 4 B ≈ 150 MB, ~10 ms / query; ceiling ≈ 1M rows (2 GB) — then run
docs/sql/pgvector_migration.sql and replace EmbIndex.topk with an ORDER BY embedding <=> query.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..storage import DATA_DIR, store
from .models import ProductImage

log = logging.getLogger("app.visual.embed")
MODEL_DIR = Path(os.getenv("IMAGE_MODEL_DIR", str(DATA_DIR / "models" / "clip-vit-b32")))
VARIANT = os.getenv("IMAGE_EMBED_MODEL", "fp32").lower()
MODEL_FILE = MODEL_DIR / ("vision_model_quantized.onnx" if VARIANT == "int8" else "vision_model.onnx")
MODEL_NAME = f"clip-vit-b32-{'int8' if VARIANT == 'int8' else 'fp32'}"
DIM = 512
IDLE_S = float(os.getenv("IMAGE_EMBED_IDLE_S", "600"))
THREADS = int(os.getenv("IMAGE_EMBED_THREADS", "4"))
BATCH = int(os.getenv("IMAGE_EMBED_BATCH", "16"))
PENDING_PER_TICK = int(os.getenv("IMAGE_EMBED_PER_TICK", "400"))
# cosine thresholds — calibrated on the live index (calib_cosine.py, 2026-10-09): unrelated-product pairs median 0.42,
# p99 0.71, p99.9 0.81 → 0.85 lets ≈0.04 % of unrelated pictures through (~27 per query on 72k rows); 0.80 let 3× more.
# Different photos of the same product score median 0.57 with ViT-B/32 — not separable from unrelated pairs, so
# LIKELY_SAME additionally needs metadata agreement (service.tier_of). See docs/IMAGE_SEARCH.md §benchmark.
SAME_COS = float(os.getenv("IMAGE_SAME_COS", "0.95"))
SIMILAR_COS = float(os.getenv("IMAGE_SIMILAR_COS", "0.85"))
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32).reshape(3, 1, 1)

_session = None
_lock = threading.Lock()
_last_used = 0.0
_reaper_started = False


def enabled() -> bool:
    if os.getenv("IMAGE_EMBED_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    return MODEL_FILE.exists()


def loaded() -> bool:
    return _session is not None


def _get_session():
    global _session, _last_used, _reaper_started
    with _lock:
        if _session is None:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = THREADS
            so.log_severity_level = 3
            t = time.time()
            _session = ort.InferenceSession(str(MODEL_FILE), so, providers=["CPUExecutionProvider"])
            log.info("CLIP model loaded (%s) in %.1fs", MODEL_NAME, time.time() - t)
            if not _reaper_started:
                _reaper_started = True
                threading.Thread(target=_reaper, name="clip-idle-unload", daemon=True).start()
        _last_used = time.time()
        return _session


def unload():
    global _session
    with _lock:
        if _session is not None:
            _session = None
            log.info("CLIP model unloaded (idle)")


def _reaper():
    while True:
        time.sleep(60)
        if _session is not None and time.time() - _last_used > IDLE_S:
            unload()


# ------------------------------------------------------------------ preprocessing (CLIPFeatureExtractor equivalent)
def preprocess(im: Image.Image) -> np.ndarray:
    """shortest edge → 224 (bicubic), center crop 224×224, RGB, /255, normalise → (3, 224, 224) float32."""
    im = im.convert("RGB")
    w, h = im.size
    s = 224 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    left, top = (im.width - 224) // 2, (im.height - 224) // 2
    im = im.crop((left, top, left + 224, top + 224))
    x = np.asarray(im, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return (x - MEAN) / STD


def embed_batch(ims: list[Image.Image]) -> np.ndarray:
    """(n, 512) float32, L2-normalised — cosine = dot product."""
    sess = _get_session()
    x = np.stack([preprocess(im) for im in ims]).astype(np.float32)
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0].astype(np.float32)
    out /= np.linalg.norm(out, axis=1, keepdims=True) + 1e-9
    return out


def embed_image(im: Image.Image) -> np.ndarray:
    return embed_batch([im])[0]


def to_bytes(v: np.ndarray) -> bytes:
    return v.astype(np.float16).tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


# ------------------------------------------------------------------ in-RAM vector index
class EmbIndex:
    def __init__(self):
        self.lock = threading.Lock()
        self.ids = np.zeros(0, dtype=np.int64)
        self.mat = np.zeros((0, DIM), dtype=np.float32)
        self.since: datetime | None = None
        self.count = 0
        self.checked_at = 0.0

    def refresh(self, db: Session, force: bool = False):
        if not force and time.time() - self.checked_at < 30:
            return
        with self.lock:
            self.checked_at = time.time()
            n = db.scalar(select(func.count(ProductImage.id)).where(ProductImage.embedding.is_not(None), ProductImage.processing_status == "done")) or 0
            if n == self.count:
                return
            q = select(ProductImage.id, ProductImage.embedding, ProductImage.embedded_at).where(ProductImage.embedding.is_not(None), ProductImage.processing_status == "done")
            if self.since is not None and n > self.count:  # incremental: only rows embedded since the last load
                q = q.where(ProductImage.embedded_at > self.since)
            else:
                self.ids, self.mat = np.zeros(0, dtype=np.int64), np.zeros((0, DIM), dtype=np.float32)
            rows = db.execute(q).all()
            if rows:
                ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
                mat = np.stack([from_bytes(r[1]) for r in rows])
                keep = ~np.isin(self.ids, ids)
                self.ids, self.mat = np.concatenate([self.ids[keep], ids]), np.concatenate([self.mat[keep], mat])
                self.since = max(r[2] for r in rows if r[2]) if any(r[2] for r in rows) else self.since
            self.count = n
            log.info("embedding index: %s vectors (%.0f MB)", len(self.ids), self.mat.nbytes / 1048576)

    def topk(self, db: Session, vec: np.ndarray, k: int = 200, min_cos: float | None = None) -> dict[int, float]:
        if pgvector_ready(db):
            return pg_topk(db, vec, k, SIMILAR_COS if min_cos is None else min_cos)
        self.refresh(db)
        if not len(self.ids):
            return {}
        with self.lock:
            cos = self.mat @ vec.astype(np.float32)
            k = min(k, len(cos))
            idx = np.argpartition(-cos, k - 1)[:k]
            floor = SIMILAR_COS if min_cos is None else min_cos
            return {int(self.ids[i]): round(float(cos[i]), 4) for i in idx if cos[i] >= floor}


INDEX = EmbIndex()


# ------------------------------------------------------------------ optional pgvector (Postgres only)
# `embedding_vec vector(512)` is created by migrate_pgvector.py, never by the ORM (no pgvector python dependency):
# raw SQL below. When present, topk() is an HNSW index scan instead of the in-RAM matrix, and new embeddings are
# written to both columns (BYTEA stays the source of truth / fallback).
_pgv: dict = {"ok": None, "at": 0.0}


def pgvector_ready(db: Session | None = None) -> bool:
    from ..db import IS_SQLITE

    if IS_SQLITE:
        return False
    if _pgv["ok"] is not None and time.time() - _pgv["at"] < 300:
        return _pgv["ok"]
    from sqlalchemy import text

    def check(s: Session) -> bool:
        return bool(s.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name='product_images' AND column_name='embedding_vec'")).first()
                    and s.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).first())

    try:
        if db is not None:
            ok = check(db)
        else:
            with SessionLocal() as s:
                ok = check(s)
    except Exception:  # noqa: BLE001
        ok = False
    _pgv.update(ok=ok, at=time.time())
    return ok


def vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v.astype(np.float32)) + "]"


def pg_topk(db: Session, vec: np.ndarray, k: int, floor: float) -> dict[int, float]:
    from sqlalchemy import text

    db.execute(text("SET LOCAL hnsw.ef_search = 300"))  # default 40 would cap a LIMIT 200 scan at ~40 rows
    rows = db.execute(text("SELECT id, 1 - (embedding_vec <=> CAST(:q AS vector)) AS cos FROM product_images "
                           "WHERE processing_status = 'done' AND embedding_vec IS NOT NULL ORDER BY embedding_vec <=> CAST(:q AS vector) LIMIT :k"),
                      {"q": vec_literal(vec), "k": k}).all()
    return {int(i): round(float(c), 4) for i, c in rows if c >= floor}


# ------------------------------------------------------------------ worker: embed indexed rows that lack a vector
def embed_pending(limit: int = PENDING_PER_TICK) -> int:
    """Rows hashed but not yet embedded (newest first), BATCH at a time; commit per batch (checkpoint)."""
    if not enabled():
        return 0
    done = 0
    with SessionLocal() as db:
        rows = db.execute(select(ProductImage.id, ProductImage.thumb_key).where(ProductImage.processing_status == "done", ProductImage.embedding.is_(None),
                                                                              ProductImage.thumb_key.is_not(None)).order_by(ProductImage.id.desc()).limit(limit)).all()
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            ims, ok = [], []
            for pid, key in chunk:
                try:
                    import io

                    ims.append(Image.open(io.BytesIO(store().get(key))))
                    ok.append(pid)
                except Exception as e:  # noqa: BLE001
                    db.execute(update(ProductImage).where(ProductImage.id == pid).values(error=f"embed: {str(e)[:200]}", emb_model=MODEL_NAME))
            if ims:
                vecs = embed_batch(ims)
                now = datetime.utcnow()
                use_pgv = pgvector_ready(db)
                for pid, v in zip(ok, vecs):
                    db.execute(update(ProductImage).where(ProductImage.id == pid).values(embedding=to_bytes(v), emb_model=MODEL_NAME, embedded_at=now))
                    if use_pgv:
                        from sqlalchemy import text

                        db.execute(text("UPDATE product_images SET embedding_vec = CAST(:v AS vector) WHERE id = :i"), {"v": vec_literal(v), "i": pid})
                done += len(ok)
            db.commit()
    return done


def status() -> dict:
    return {"enabled": enabled(), "model": MODEL_NAME if MODEL_FILE.exists() else None, "model_file": str(MODEL_FILE), "loaded": loaded(),
            "dim": DIM, "same_cos": SAME_COS, "similar_cos": SIMILAR_COS, "vectors_in_ram": int(len(INDEX.ids)),
            "vector_search": "pgvector (HNSW)" if pgvector_ready() else "numpy in RAM"}
