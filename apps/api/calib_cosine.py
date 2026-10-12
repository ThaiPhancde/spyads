"""Calibrate CLIP cosine thresholds on the live index (read-only).
negatives = random pairs of indexed pictures from DIFFERENT products (what a loose threshold lets through);
positives = pairs of hash-far pictures of the SAME product (other angle / variant — noisy labels).
Prints percentiles so IMAGE_SIMILAR_COS / IMAGE_SAME_COS can be chosen for a target false-positive rate.
Run: cd apps/api && .venv/Scripts/python calib_cosine.py [N]"""
import random
import sys

import numpy as np
from sqlalchemy import select

from app import models  # noqa: F401
from app.db import SessionLocal
from app.models import Creative
from app.visual import embed, service
from app.visual.models import ProductImage

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
random.seed(3)
with SessionLocal() as db:
    rows = db.execute(select(ProductImage.id, ProductImage.embedding, ProductImage.phash, ProductImage.dhash, Creative.product_id)
                      .join(Creative, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
                      .where(ProductImage.embedding.is_not(None), Creative.product_id.is_not(None)).limit(N * 5)).all()
random.shuffle(rows)
rows = rows[:N]
vec = np.stack([embed.from_bytes(r[1]) for r in rows])
pid = np.array([r[4] for r in rows])
ph = [r[2] for r in rows]
dh = [r[3] or r[2] for r in rows]
cos = vec @ vec.T
iu = np.triu_indices(len(rows), 1)
same_prod = pid[iu[0]] == pid[iu[1]]
c = cos[iu]
neg = c[~same_prod]
# positives: same product AND hash-far (different picture)
far = np.array([min(service.hamming(ph[i], ph[j]), service.hamming(dh[i], dh[j])) > service.SIMILAR_DIST for i, j in zip(iu[0], iu[1]) if pid[i] == pid[j]])
pos = c[same_prod][far]
pct = lambda a, q: float(np.percentile(a, q)) if len(a) else None
print(f"vectors={len(rows)} negative pairs={len(neg)} positive(other-picture, same product) pairs={len(pos)}")
print("negatives cosine p50/p90/p95/p99/p99.9/max:", [round(pct(neg, q), 3) for q in (50, 90, 95, 99, 99.9, 100)])
print("positives cosine p10/p25/p50/p75/p90:", [round(pct(pos, q), 3) for q in (10, 25, 50, 75, 90)] if len(pos) else None)
for t in (0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.95):
    fp = float((neg >= t).mean()) if len(neg) else None
    tp = float((pos >= t).mean()) if len(pos) else None
    exp_fp_per_query = fp * len(rows) if fp is not None else None  # ≈ how many unrelated pictures pass per query in an index of this sample size
    print(f"threshold {t:.2f}: negatives passing {fp:.4%} (≈{fp * 72000:.0f} per query on the 72k index) · positives kept {tp:.1%}" if fp is not None else t)
