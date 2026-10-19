"""Non-destructive product clustering job (run migration first).

python -m app.cluster_jobs                 # simulate and roll back all data writes
python -m app.cluster_jobs --queue-only    # persist memberships/review queue only
python -m app.cluster_jobs --apply         # group confident pairs; preserve original Ads/Products
python -m app.cluster_jobs --undo-auto     # undo Phase 1 automatic groupings in reverse order
"""
from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import select

from .db import SessionLocal, ingest_lock
from .models import Product, ProductLink
from .services import clustering as C

log = logging.getLogger("cluster_jobs")


def tick(db) -> dict:
    """Reuse persisted embeddings in the worker; only the cluster overlay and review queue change."""
    with ingest_lock():
        stat = C.run(db, apply_auto=True)
        db.commit()
    return stat


def _name(db, pid: int) -> str:
    p = db.get(Product, pid)
    return f"{p.canonical_name} (#{pid}, {p.name_status})" if p else f"#{pid}"


def run(mode: str) -> dict:
    # Schema provisioning is explicit: python -m app.cluster_migrate (also called on startup).
    # Simulation rolls back all membership, candidate and event writes.
    with SessionLocal() as db, ingest_lock():
        before = C.metrics(db)
        stat = C.run(db, apply_auto=mode in ("simulate", "apply"))
        after = C.metrics(db)
        if mode == "simulate":
            db.rollback()
        else:
            db.commit()
        return {"mode": mode, "before": before, "after": after, **stat}


def undo_auto() -> int:
    n = 0
    with SessionLocal() as db:
        for link in db.scalars(select(ProductLink).where(ProductLink.status == "auto_clustered").order_by(ProductLink.id.desc())).all():
            ev = db.get(C.Event, link.event_id) if link.event_id else None
            if ev is None or (ev.data or {}).get("undone"):
                continue
            try:
                C.undo_event(db, ev.product_id, ev.id, "undo-auto")
                n += 1
            except ValueError as e:
                log.warning("link %s: %s", link.id, e)
        db.commit()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--queue-only", action="store_true")
    g.add_argument("--undo-auto", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.undo_auto:
        print({"undone": undo_auto()})
    else:
        r = run("apply" if args.apply else "queue" if args.queue_only else "simulate")
        print(json.dumps({k: v for k, v in r.items() if k not in ("prevented_examples", "mixed_examples")}, ensure_ascii=False, indent=2, default=str))
