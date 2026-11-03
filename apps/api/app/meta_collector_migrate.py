"""Additive, repeatable migration: python -m app.meta_collector_migrate."""
from sqlalchemy import inspect, text

from .db import engine
from .crawl_queue import CrawlJob, CrawlRun
from .models import IngestBatch, MetaCollation

COLUMNS = {"creatives": (("video_fp", "VARCHAR(50)"), ("video_fp_confidence", "FLOAT")),  # Phase 2 video fingerprint
           "crawl_runs": tuple((c, "INTEGER") for c in ("pages", "empty_pages", "accepted", "duplicates", "invalid", "excluded",  # Phase 5
                                                         "requests", "cursor_cycles", "cursor_restarts", "schema_drift"))}  # P2 diagnostics


def migrate(bind=None):
    with (bind or engine).begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(727004)"))
        for model in (IngestBatch, MetaCollation, CrawlJob, CrawlRun):
            model.__table__.create(conn, checkfirst=True)
        insp = inspect(conn)
        for table, cols in COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))


if __name__ == "__main__":
    migrate()
    print("Meta collector tables / columns ready; existing data unchanged.")
