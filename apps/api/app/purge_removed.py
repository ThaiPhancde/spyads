"""Delete the data of sources dropped from the product (platforms.REMOVED_SOURCES / REMOVED_NETWORKS).

Removes: their ads, provenance (ad_sources), creatives + stored files (local disk or R2), their connectors with
tracked queries, raw batches, and products left with no ad at all. Also drops the two tables the code no longer
has a model for (`markets`, `creative_daily_snapshots`: never read). Kept: products with votes /
extension saves (retention.protected_products) — they only lose the dropped rows.

    python -m app.purge_removed            # dry run: counts only, changes nothing
    python -m app.purge_removed --apply    # really delete (run once locally, once against the server's DATABASE_URL)
"""
from __future__ import annotations

import sys

from sqlalchemy import delete, func, or_, select, text, update

from .db import SessionLocal
from .models import (Ad, AdSource, Alert, Comment, Connector, Creative, LifecycleEvent, Product,
                     ProductAlias, ProductDailySnapshot, RawRecord, Store, TrackedQuery, Vote)
from .platforms import REMOVED_NETWORKS, REMOVED_SOURCES
from .retention import protected_products
from .storage import store


DROPPED_TABLES = ("creative_daily_snapshots", "markets")


def _chunks(ids: list[int], n: int = 500):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def plan(db) -> dict:
    gone = or_(Ad.source.in_(REMOVED_SOURCES), Ad.network.in_(REMOVED_NETWORKS))
    ads = db.execute(select(Ad.id, Ad.product_id).where(gone)).all()
    ad_ids = [a for a, _ in ads]
    touched = {p for _, p in ads if p}
    still = {p for (p,) in db.execute(select(Ad.product_id).where(Ad.product_id.in_(touched), ~gone).group_by(Ad.product_id))}
    orphans = sorted(touched - still - protected_products(db))
    crs = [c for ch in _chunks(ad_ids) for c in db.scalars(select(Creative).where(Creative.ad_id.in_(ch)))]
    keys = {k for c in crs for k in (c.storage_key, c.thumb_key) if k}
    shared = {k for ch in _chunks(sorted(keys)) for (k,) in db.execute(
        select(Creative.storage_key).where(Creative.storage_key.in_(ch), Creative.ad_id.not_in(ad_ids or [-1])))}
    conns = db.scalars(select(Connector.id).where(Connector.adapter.in_(REMOVED_SOURCES))).all()
    return {"ad_ids": ad_ids, "creatives": crs, "files": sorted(keys - shared), "orphans": orphans,
            "rescore": sorted(touched - set(orphans)), "connectors": list(conns)}


def apply(db, p: dict) -> int:
    files = 0
    for key in p["files"]:
        try:
            files += store().delete(key)
        except Exception:
            pass  # already gone
    for ids in _chunks(p["ad_ids"]):
        db.execute(update(Comment).where(Comment.ad_id.in_(ids)).values(ad_id=None))
        db.execute(delete(Creative).where(Creative.ad_id.in_(ids)))
        db.execute(delete(AdSource).where(AdSource.ad_id.in_(ids)))
        db.execute(delete(Ad).where(Ad.id.in_(ids)))
    for t in DROPPED_TABLES:  # first: creative_daily_snapshots references products
        db.execute(text(f"DROP TABLE IF EXISTS {t}"))
    for ids in _chunks(p["orphans"]):
        db.execute(update(Comment).where(Comment.product_id.in_(ids)).values(product_id=None))
        for m in (ProductAlias, LifecycleEvent, Store, ProductDailySnapshot, Alert, Vote, Creative):
            db.execute(delete(m).where(m.product_id.in_(ids)))
        db.execute(delete(Product).where(Product.id.in_(ids)))
    if p["connectors"]:
        db.execute(delete(TrackedQuery).where(TrackedQuery.connector_id.in_(p["connectors"])))
        db.execute(update(RawRecord).where(RawRecord.connector_id.in_(p["connectors"])).values(connector_id=None))
        db.execute(delete(Connector).where(Connector.id.in_(p["connectors"])))
    for src in REMOVED_SOURCES:
        try:
            files += store().delete_prefix(f"raw/{src}/")
        except Exception:
            pass
    db.commit()
    from .services.engine import rescore_products

    rescore_products(db, p["rescore"])
    db.commit()
    return files


def main(argv: list[str]) -> None:
    with SessionLocal() as db:
        p = plan(db)
        print(f"sources: {', '.join(REMOVED_SOURCES)}")
        print(f"ads {len(p['ad_ids'])} · creatives {len(p['creatives'])} · media files {len(p['files'])} · "
              f"products left empty {len(p['orphans'])} · products re-scored {len(p['rescore'])} · connectors {len(p['connectors'])}")
        if "--apply" not in argv:
            print("dry run — nothing changed. Re-run with --apply to delete.")
            return
        print(f"deleted; {apply(db, p)} stored files / raw batches removed. Products total: {db.scalar(select(func.count(Product.id)))}")


if __name__ == "__main__":
    main(sys.argv[1:])
