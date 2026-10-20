"""Merge duplicate creatives of one ad that differ only by CDN signature / host (tiktokcdn signs inside the URL path and
fbcdn rotates hosts, so every re-collect used to add a copy). Keeps one row per (ad, bare url) — the one with a thumbnail, else the oldest — and points
products.cover_creative_id at the survivor; deleted rows are first copied to bak_creatives_dedupe. Dry run unless --apply:  python -m app.dedupe_creatives [--apply]
"""
import sys
import time

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError

from .db import SessionLocal
from .ingest import bare_url
from .models import Creative, Product


def run(apply: bool = False) -> dict:
    with SessionLocal() as db:
        groups: dict[tuple, list[Creative]] = {}
        for c in db.scalars(select(Creative).where(Creative.source_url.is_not(None))):
            groups.setdefault((c.ad_id, c.type, bare_url(c.source_url)), []).append(c)
        drop, remap, skipped = [], {}, 0
        for rows in groups.values():
            if len(rows) < 2:
                continue
            if len({c.sha256 for c in rows if c.sha256}) > 1:  # same url key but different bytes: not a duplicate, keep all
                skipped += 1
                continue
            keep = min(rows, key=lambda c: (not c.thumb_key, c.id))
            for c in rows:
                if c is not keep:
                    drop.append(c.id)
                    remap[c.id] = keep.id
        if apply and drop:
            db.execute(text("CREATE TABLE IF NOT EXISTS bak_creatives_dedupe AS SELECT * FROM creatives WHERE false"))
            db.commit()
            for i in range(0, len(drop), 200):  # small committed batches: the worker updates creatives concurrently (deadlocks → retry)
                ids = drop[i:i + 200]
                for attempt in range(5):
                    try:
                        db.execute(text("INSERT INTO bak_creatives_dedupe SELECT * FROM creatives WHERE id = ANY(:ids) "
                                        "AND id NOT IN (SELECT id FROM bak_creatives_dedupe)"), {"ids": ids})
                        for pid, old in db.execute(select(Product.id, Product.cover_creative_id).where(Product.cover_creative_id.in_(ids))).all():
                            db.execute(update(Product).where(Product.id == pid).values(cover_creative_id=remap[old]))
                        db.execute(delete(Creative).where(Creative.id.in_(ids)))
                        db.commit()
                        break
                    except OperationalError:
                        db.rollback()
                        time.sleep(1 + attempt)
                else:
                    raise RuntimeError(f"batch at {i} kept deadlocking; re-run to continue (it is idempotent)")
        return {"checked": sum(len(r) for r in groups.values()), "duplicates": len(drop), "skipped_different_content": skipped, "applied": apply and bool(drop)}


if __name__ == "__main__":
    print(run("--apply" in sys.argv))
