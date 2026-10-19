"""Phase 6 evaluation on the real database, READ ONLY: latency + accuracy proxies for similar products / ads.

    python scripts/eval_similar.py [--n 60] [--out ../../docs/PHASE6_SIMILAR_EVAL.json]

Accuracy proxies (no labelled set exists yet): category / concept agreement of the top 10, recall of known duplicate
pairs (Phase 1 duplicate_product links, duplicate_ad_links) being labelled duplicate and never offered as "similar",
and no same-cluster product returned as similar.
"""
import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text  # noqa: E402

from app import models  # noqa: E402,F401
from app import similar as S  # noqa: E402
from app.cluster_models import DuplicateAdLink  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Ad, Product, ProductLink  # noqa: E402


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "docs" / "PHASE6_SIMILAR_EVAL.json"))
    args = ap.parse_args()
    rnd = random.Random(20261019)
    out = {}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        red = S._red(db)
        pool = list(db.scalars(select(Product.id).where(Product.in_target.is_(True), Product.name_status != "UNKNOWN",
                                                        Product.category.is_not(None), Product.id.not_in(list(red) or [0]))))
        sample = rnd.sample(pool, min(args.n, len(pool)))
        lat, cat_agree, con_agree, sizes, same_cluster = [], [], [], [], 0
        S.similar_products(db, sample[0], None)  # warm caches (redirect map, connection)
        for pid in sample:
            t = time.perf_counter()
            r = S.similar_products(db, pid, None, 10)
            lat.append(1000 * (time.perf_counter() - t))
            sizes.append(r["candidates"])
            src = db.get(Product, r["product_id"])
            top = r["similar"][:10]
            if top:
                cat_agree.append(sum(1 for x in top if x["category"] == src.category) / len(top))
                s_con = S.concepts(S._product_text(db, [src.id])[0][src.id])
                if s_con:
                    con_agree.append(sum(1 for x in top if x["signals"]["concepts"] > 0) / len(top))
            same_cluster += sum(1 for x in r["similar"] if S._root(red, x["product_id"]) == r["product_id"])
        out["similar_products"] = {"n": len(sample), "latency_ms": {"p50": pct(lat, .5), "p95": pct(lat, .95), "max": pct(lat, 1)},
                                   "candidates": {"p50": pct(sizes, .5), "max": max(sizes) if sizes else 0},
                                   "with_results": len(cat_agree), "category_agreement_at10": round(statistics.mean(cat_agree), 3) if cat_agree else None,
                                   "concept_agreement_at10": round(statistics.mean(con_agree), 3) if con_agree else None,
                                   "same_cluster_returned_as_similar": same_cluster}
        ads = list(db.scalars(select(Ad.id).where(Ad.search_text.is_not(None))))
        alat = []
        for aid in rnd.sample(ads, min(args.n, len(ads))):
            t = time.perf_counter()
            S.similar_ads(db, aid, 10)
            alat.append(1000 * (time.perf_counter() - t))
        out["similar_ads"] = {"n": len(alat), "latency_ms": {"p50": pct(alat, .5), "p95": pct(alat, .95), "max": pct(alat, 1)}}
        links = db.execute(select(ProductLink.a_id, ProductLink.b_id).where(
            ProductLink.relation_kind == "duplicate_product", ProductLink.status == "pending")).all()
        live = [(a, b) for a, b in links if db.get(Product, a) and db.get(Product, b) and S._root(red, a) != S._root(red, b)]
        hit = leak = 0
        for a, b in live[:50]:
            r = S.similar_products(db, a, None, 100)
            hit += S._root(red, b) in {x["product_id"] for x in r["duplicates"]}
            leak += S._root(red, b) in {x["product_id"] for x in r["similar"]}
        out["duplicate_products"] = {"pairs_checked": min(50, len(live)), "labelled_duplicate": hit, "offered_as_similar": leak}
        dhit = dleak = 0
        dpairs = db.execute(select(DuplicateAdLink.a_id, DuplicateAdLink.b_id)).all()
        for a, b in dpairs:
            r = S.similar_ads(db, a, 100)
            dhit += b in {x["ad_id"] for x in r["duplicates"]}
            dleak += b in {x["ad_id"] for x in r["similar"]}
        out["duplicate_ads"] = {"pairs_checked": len(dpairs), "labelled_duplicate": dhit, "offered_as_similar": dleak}
        db.rollback()
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
