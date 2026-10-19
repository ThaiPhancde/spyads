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
from .eu_market.models import EUAdMatch
from .models import (Ad, AdSource, Advertiser, AdvertiserDailySnapshot, Alert, Comment, Connector, Creative, Event, LifecycleEvent, Product,
                     ProductAlias, ProductDailySnapshot, RawRecord, Store, TrackedQuery, Vote)
from .platforms import REMOVED_NETWORKS, REMOVED_SOURCES
from .retention import protected_products
from .storage import store
from .visual.models import ProductImage, ProductMatch


DROPPED_TABLES = ("creative_daily_snapshots", "markets")


def _chunks(ids: list[int], n: int = 500):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def plan(db, gone=None) -> dict:
    """What deleting the ads matching `gone` (SQL condition; default: removed sources) takes with it.
    Shared things stay: a product that still has other ads, a file another creative / image still points to."""
    gone = gone if gone is not None else or_(Ad.source.in_(REMOVED_SOURCES), Ad.network.in_(REMOVED_NETWORKS))
    ads = db.execute(select(Ad.id, Ad.product_id, Ad.advertiser_id).where(gone)).all()
    ad_ids = [a.id for a in ads]
    touched = {a.product_id for a in ads if a.product_id}
    still = {p for (p,) in db.execute(select(Ad.product_id).where(Ad.product_id.in_(touched), ~gone).group_by(Ad.product_id))}
    orphans = sorted(touched - still - protected_products(db))
    crs = [c for ch in _chunks(ad_ids) for c in db.scalars(select(Creative).where(Creative.ad_id.in_(ch)))]
    crs += [c for ch in _chunks(orphans) for c in db.scalars(select(Creative).where(Creative.product_id.in_(ch), Creative.ad_id.is_(None)))]
    cr_ids = [c.id for c in crs]
    imgs = [i for ch in _chunks(cr_ids) for i in db.scalars(select(ProductImage).where(ProductImage.source == "creative",
                                                                                      ProductImage.source_id.in_(ch)))]
    keys = {k for c in crs for k in (c.storage_key, c.thumb_key) if k} | {i.thumb_key for i in imgs if i.thumb_key}
    gone_cr, gone_img = set(cr_ids) or {-1}, {i.id for i in imgs} or {-1}
    shared: set[str] = set()
    for ch in _chunks(sorted(keys)):  # content-addressed: the same file may back a creative we keep
        for col in (Creative.storage_key, Creative.thumb_key):
            shared |= set(db.scalars(select(col).where(col.in_(ch), Creative.id.not_in(gone_cr))))
        shared |= set(db.scalars(select(ProductImage.thumb_key).where(ProductImage.thumb_key.in_(ch), ProductImage.id.not_in(gone_img))))
    files = sorted(keys - shared)
    size = {k: c.stored_bytes or c.size_bytes or 0 for c in crs for k in (c.storage_key,) if k}
    advs = {a.advertiser_id for a in ads if a.advertiser_id}
    conns = db.scalars(select(Connector.id).where(Connector.adapter.in_(REMOVED_SOURCES))).all()
    return {"ad_ids": ad_ids, "creatives": crs, "images": [i.id for i in imgs], "files": files,
            "bytes": sum(size.get(k, 0) for k in files), "orphans": orphans, "advertisers": sorted(advs),
            "rescore": sorted(touched - set(orphans)), "connectors": list(conns)}


def purge_ads(db, p: dict) -> int:
    """Delete the planned ads and everything only they used; re-score the products that keep other ads. Returns files removed.
    Rows first, files after the commit: a failed delete must never leave rows pointing at files that are gone."""
    cr_ids = [c.id for c in p["creatives"]]
    for ids in _chunks(p["images"]):
        db.execute(delete(ProductImage).where(ProductImage.id.in_(ids)))
    for ids in _chunks(cr_ids):
        for col_s, col_i in ((ProductMatch.source, ProductMatch.source_id), (ProductMatch.target_source, ProductMatch.target_id)):
            db.execute(delete(ProductMatch).where(col_s == "creative", col_i.in_(ids)))
        db.execute(update(Product).where(Product.cover_creative_id.in_(ids)).values(cover_creative_id=None))  # media.py picks a new one
        db.execute(delete(Creative).where(Creative.id.in_(ids)))
    for ids in _chunks(p["ad_ids"]):
        db.execute(update(Comment).where(Comment.ad_id.in_(ids)).values(ad_id=None))
        db.execute(delete(EUAdMatch).where(EUAdMatch.ad_id.in_(ids)))
        db.execute(delete(Creative).where(Creative.ad_id.in_(ids)))
        db.execute(delete(AdSource).where(AdSource.ad_id.in_(ids)))
        db.execute(delete(Ad).where(Ad.id.in_(ids)))
    for ids in _chunks(p["orphans"]):
        db.execute(update(Comment).where(Comment.product_id.in_(ids)).values(product_id=None))
        for m in (ProductAlias, LifecycleEvent, Store, ProductDailySnapshot, Alert, Vote, Creative, Event):
            db.execute(delete(m).where(m.product_id.in_(ids)))
        db.execute(delete(Product).where(Product.id.in_(ids)))
    for ids in _chunks(p.get("advertisers", [])):  # advertisers with no ad and no store left
        used = set(db.scalars(select(Ad.advertiser_id).where(Ad.advertiser_id.in_(ids)))) | \
            set(db.scalars(select(Store.advertiser_id).where(Store.advertiser_id.in_(ids))))
        if dead := [i for i in ids if i not in used]:
            db.execute(update(Alert).where(Alert.advertiser_id.in_(dead)).values(advertiser_id=None))
            db.execute(delete(AdvertiserDailySnapshot).where(AdvertiserDailySnapshot.advertiser_id.in_(dead)))
            db.execute(delete(Advertiser).where(Advertiser.id.in_(dead)))
    db.commit()
    files = 0
    for key in p["files"]:
        try:
            files += store().delete(key)
        except Exception:
            pass  # already gone
    from .services.engine import rescore_products

    rescore_products(db, p["rescore"])
    db.commit()
    return files


def apply(db, p: dict) -> int:
    for t in DROPPED_TABLES:  # first: creative_daily_snapshots references products
        db.execute(text(f"DROP TABLE IF EXISTS {t}"))
    if p["connectors"]:
        db.execute(delete(TrackedQuery).where(TrackedQuery.connector_id.in_(p["connectors"])))
        db.execute(update(RawRecord).where(RawRecord.connector_id.in_(p["connectors"])).values(connector_id=None))
        db.execute(delete(Connector).where(Connector.id.in_(p["connectors"])))
    files = purge_ads(db, p)
    for src in REMOVED_SOURCES:
        try:
            files += store().delete_prefix(f"raw/{src}/")
        except Exception:
            pass
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
