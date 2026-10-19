"""Idempotent, additive Phase 1 migration: python -m app.cluster_migrate.

Creates only cluster overlay tables/columns; backfills unscored legacy membership.
No old Ads, Products, snapshots or candidate history are deleted or reclassified.
"""
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import models  # register referenced tables
from .cluster_models import AdClusterMembership, DuplicateAdLink, ProductCluster
from .db import engine
from .services.cluster_membership import lock, sync


def migrate(bind=None):
    with Session(bind or engine) as db, db.begin():
        lock(db)
        conn = db.connection()
        for model in (ProductCluster, AdClusterMembership, DuplicateAdLink):
            model.__table__.create(conn, checkfirst=True)
        columns = {c["name"] for c in inspect(conn).get_columns("product_links")}
        for name in ("relation_kind", "algorithm_version"):
            if name not in columns:
                conn.execute(text(f'ALTER TABLE product_links ADD COLUMN "{name}" VARCHAR(32)'))
        sync(db)


if __name__ == "__main__":
    migrate()
    print("Phase 1 cluster overlay migrated; original Ads/Products/history preserved.")
