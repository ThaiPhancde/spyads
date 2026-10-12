"""Ground-truth evaluation of image search (spec §12): Precision@K / Recall@K / false-match rate on real indexed pictures,
against the live database (reads only). Not a unit test — a measurement that calibrates the thresholds.

Sets (built from the index, no hand labels needed for 1-3; 4 is a noisy proxy):
  1. copies      — the stored thumbnail itself, re-encoded (JPEG q35, half size, WebP)   → the same picture (same thumbnail
                   sha256 — Meta reuses one creative across many ads, so *any* copy counts) must be in the top K as EXACT, rank 1
  2. light crop  — 5 % border crop                                                        → the picture must be found (any tier)
  3. heavy crop  — 15 % / 25 % border crop, slight brightness change                      → embedding tier's job
  4. other-angle — the SAME product has another creative whose picture is far by hash (> SIMILAR_DIST): is one of those
                   *different* pictures in the top K? (hash engine: 0 by construction; measures the embedding tier).
                   Noisy labels: a product groups several photos / angles / variants.
  5. ambiguity   — EXACT hits (same picture) that belong to a DIFFERENT product cluster: duplicates the entity resolution did
                   not merge. Not a hashing error, but exactly why LIKELY_SAME needs metadata agreement.
  6. suspicious EXACT (clip only) — EXACT-tier hits whose CLIP cosine is < SIMILAR_COS: hash collision candidates.
Run: cd apps/api && .venv/Scripts/python eval_image_search.py [N] [--write docs]   (needs DATABASE_URL from .env)
"""
import io
import json
import os
import random
import sys
import time
from datetime import datetime

from PIL import Image, ImageEnhance
from sqlalchemy import func, select

from app import models  # noqa: F401
from app.db import SessionLocal
from app.models import Ad, Creative
from app.storage import store
from app.visual import service
from app.visual.models import ProductImage
from app.visual.service import EXACT, LIKELY_SAME, QueryImage

N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
K = 10
random.seed(7)


def variants(raw: bytes) -> dict[str, bytes]:
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size

    def enc(x, fmt="JPEG", **kw):
        b = io.BytesIO()
        x.save(b, fmt, **kw)
        return b.getvalue()

    return {
        "q35": enc(im, quality=35), "half": enc(im.resize((max(32, w // 2), max(32, h // 2)))), "webp": enc(im, "WEBP", quality=70),
        "crop5": enc(im.crop((w // 20, h // 20, w * 19 // 20, h * 19 // 20))),
        "crop15": enc(im.crop((w * 3 // 20, h * 3 // 20, w * 17 // 20, h * 17 // 20))),
        "crop25_bright": enc(ImageEnhance.Brightness(im.crop((w // 4, h // 4, w * 3 // 4, h * 3 // 4))).enhance(1.15)),
    }


def main():
    out = {"ran_at": datetime.utcnow().isoformat(), "n": N, "k": K}
    with SessionLocal() as db:
        total = db.scalar(select(func.count(ProductImage.id)).where(ProductImage.processing_status == "done"))
        emb = db.scalar(select(func.count(ProductImage.id)).where(ProductImage.embedding.is_not(None)))
        out["index"] = {"images": total, "embeddings": emb}
        try:
            from app.visual import embed

            use_emb = embed.enabled() and emb > 0
        except Exception:  # noqa: BLE001
            use_emb = False
        out["engine"] = "hash+clip" if use_emb else "hash"
        # sample creatives whose product has ≥ 2 distinct pictures (for the other-angle set)
        q = (select(Creative.id, Creative.thumb_key, Creative.product_id).join(ProductImage, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
             .where(ProductImage.processing_status == "done", Creative.product_id.is_not(None)).order_by(func.random()).limit(N * 3))
        rows = db.execute(q).all()
        sample = rows[:N]
        hits = {k: 0 for k in ("q35", "half", "webp", "crop5", "crop15", "crop25_bright")}
        exact_rank1 = {k: 0 for k in ("q35", "half", "webp")}
        ambiguous, ambiguous_cases, suspicious, suspicious_cases = 0, [], 0, []
        other_angle_total = other_angle_hit = 0
        took = []
        for cid, tkey, pid in sample:
            raw = store().get(tkey)
            my_sha = db.scalar(select(ProductImage.image_sha256).where(ProductImage.source == "creative", ProductImage.source_id == cid))
            for name, data in variants(raw).items():
                qi = QueryImage(data)
                if use_emb:
                    qi.embedding = embed.embed_image(qi.im)
                t = time.time()
                res = service.search(db, qi, limit=K)
                took.append(time.time() - t)
                rows_ = res["rows"]
                # the picture itself = any row with the same thumbnail bytes (one creative image is reused by many ads)
                found = next((i for i, r in enumerate(rows_) if r["creative_id"] == cid or r.get("image_sha256") == my_sha), None)
                if found is not None:
                    hits[name] += 1
                if name in exact_rank1 and found == 0 and rows_[0]["tier"] == EXACT:
                    exact_rank1[name] += 1
                if name == "q35":
                    for r in rows_:
                        if r["tier"] == EXACT and r.get("image_sha256") == my_sha and r.get("product_id") not in (None, pid):
                            ambiguous += 1  # same picture, another product cluster (unmerged duplicate)
                            ambiguous_cases.append({"query_creative": cid, "hit_creative": r["creative_id"], "query_product": pid, "hit_product": r["product_id"]})
                            break
                    for r in rows_:
                        if r["tier"] == EXACT and r.get("cosine") is not None and r["cosine"] < embed.SIMILAR_COS:
                            suspicious += 1  # hash says same picture, CLIP says different: possible hash collision
                            suspicious_cases.append({"query_creative": cid, "hit_creative": r["creative_id"], "dist": r["hash_distance"], "cos": r["cosine"]})
                            break
            # other-angle proxy: does the product have a *different* picture (far by hash), and is one of them in the top K?
            sib = db.execute(select(Creative.id, ProductImage.phash, ProductImage.dhash).join(ProductImage, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
                             .where(Creative.product_id == pid, Creative.id != cid, ProductImage.processing_status == "done")).all()
            mine = db.execute(select(ProductImage.phash, ProductImage.dhash).where(ProductImage.source == "creative", ProductImage.source_id == cid)).first()
            far = {s[0] for s in sib if s[1] and mine and min(service.hamming(s[1], mine[0]), service.hamming(s[2] or s[1], mine[1] or mine[0])) > service.SIMILAR_DIST}
            if far:
                other_angle_total += 1
                qi = QueryImage(raw)
                if use_emb:
                    qi.embedding = embed.embed_image(qi.im)
                res = service.search(db, qi, limit=K, exclude=("creative", cid))
                if any(r.get("creative_id") in far for r in res["rows"]):
                    other_angle_hit += 1
        n = len(sample)
        out["recall_at_k"] = {k: round(v / n, 3) for k, v in hits.items()}
        out["exact_rank1"] = {k: round(v / n, 3) for k, v in exact_rank1.items()}
        out["same_picture_other_product"] = {"rate": round(ambiguous / n, 4), "cases": ambiguous, "examples": ambiguous_cases[:8],
                                             "note": "data ambiguity (unmerged duplicates), not a hash error — why LIKELY_SAME needs metadata agreement"}
        out["suspicious_exact"] = {"rate": round(suspicious / n, 4), "cases": suspicious, "examples": suspicious_cases[:8],
                                   "note": "EXACT by hash but CLIP cosine < SIMILAR_COS (needs the clip engine; 0 when hash-only)"}
        out["other_angle_proxy"] = {"queries": other_angle_total, "different_picture_in_top_k": other_angle_hit,
                                    "recall": round(other_angle_hit / other_angle_total, 3) if other_angle_total else None,
                                    "note": "proxy: a hash-far creative of the same product (noisy labels — products group several photos / angles / variants)"}
        took.sort()
        out["latency_ms"] = {"p50": int(took[len(took) // 2] * 1000), "p95": int(took[int(len(took) * 0.95)] * 1000), "max": int(took[-1] * 1000), "queries": len(took)}
        out["thresholds"] = {"exact_dist": service.EXACT_DIST, "similar_dist": service.SIMILAR_DIST,
                             **({"same_cos": embed.SAME_COS, "similar_cos": embed.SIMILAR_COS} if use_emb else {})}
    print(json.dumps(out, indent=1, default=str))
    if "--write" in sys.argv:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", f"image_search_eval_{out['engine']}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=str)
        print("wrote", os.path.abspath(p))


if __name__ == "__main__":
    main()
