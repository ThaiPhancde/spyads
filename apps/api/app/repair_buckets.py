"""Dissolve "bucket" products named after a Meta Instant-Experience link (fb.com/canvas_doc/…, fb.com/messenger_doc/…).

Old ingest versions named the product from that URL slug, so ads of unrelated advertisers (Torriden, Sace Lady, Beplain…)
all landed in one "Canvas Doc" product. Each advertiser's ads move to their own product (resolve_product with the
advertiser-bucket name, the rule ingest uses today); the empty bucket is deleted.

    python -m app.repair_buckets            # dry run
    python -m app.repair_buckets --apply
"""
import re
import sys
from collections import defaultdict

from sqlalchemy import select, text

from .db import SessionLocal
from .models import Ad, Advertiser, Creative, Product, Store
from .services.engine import rescore_products
from .services.entity_resolution import resolve_product

BUCKET_NAME = re.compile(r"^(canvas|messenger) doc$", re.I)


def run(apply: bool) -> None:
    with SessionLocal() as db:
        buckets = [p for p in db.scalars(select(Product)).all() if BUCKET_NAME.match(p.canonical_name or "")]
        for p in buckets:
            ads = db.scalars(select(Ad).where(Ad.product_id == p.id)).all()
            by_adv = defaultdict(list)
            for a in ads:
                by_adv[a.advertiser_id].append(a)
            print(f"{p.product_code} '{p.canonical_name}': {len(ads)} ads from {len(by_adv)} advertisers")
            touched = set()
            for aid, lst in by_adv.items():
                adv = db.get(Advertiser, aid) if aid else None
                name = f"{adv.name} (chưa đặt tên)" if adv else "Unknown (chưa đặt tên)"
                a0 = lst[0]
                target, created, _ = resolve_product(db, {"name": name, "advertiser": adv.name if adv else None, "category": p.category,
                                                          "country": a0.country, "currency": a0.currency}, source="repair_buckets")
                if target.id == p.id:
                    continue
                print(f"   → {len(lst):3d} ads → {target.product_code} '{target.canonical_name}'{' (new)' if created else ''}")
                for a in lst:
                    a.product_id = target.id
                    if a.store_id and (st := db.get(Store, a.store_id)) and st.product_id == p.id:
                        st.product_id = target.id
                for c in db.scalars(select(Creative).where(Creative.ad_id.in_([a.id for a in lst]))).all():
                    c.product_id = target.id
                touched.add(target.id)
            if not apply:
                db.rollback()
                continue
            db.flush()
            if not db.scalars(select(Ad.id).where(Ad.product_id == p.id).limit(1)).first():
                # snapshots, aliases, alerts, stores… of the junk bucket — by the live schema, not the models
                # (creative_daily_snapshots has no model any more). ponytail: SQLite only, this is a one-off repair
                for (t,) in db.execute(text("select name from sqlite_master where type='table'")).all():
                    for fk in db.execute(text(f"pragma foreign_key_list('{t}')")).all():
                        if fk[2] == "products":
                            db.execute(text(f'delete from "{t}" where "{fk[3]}" = :pid'), {"pid": p.id})
                db.delete(p)
                print(f"   deleted empty bucket {p.product_code}")
            rescore_products(db, touched)
            db.commit()
        if not buckets:
            print("no bucket products found")
        print("applied" if apply else "dry run — add --apply to write")


if __name__ == "__main__":
    run("--apply" in sys.argv)
