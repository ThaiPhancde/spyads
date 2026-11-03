"""Additive, repeatable migration: python -m app.meta_collector_migrate."""
import logging

from sqlalchemy import inspect, text

from .db import engine
from .crawl_queue import CrawlJob, CrawlRun
from .models import IngestBatch, MetaCollation

log = logging.getLogger(__name__)

COLUMNS = {"creatives": (("video_fp", "VARCHAR(50)"), ("video_fp_confidence", "FLOAT"),  # Phase 2 video fingerprint
                         ("video_fp_v2", "JSON"), ("media_claims", "JSON")),  # P3 versioned fingerprint + efg claims
           "crawl_runs": tuple((c, "INTEGER") for c in ("pages", "empty_pages", "accepted", "duplicates", "invalid", "excluded",  # Phase 5
                                                         "requests", "cursor_cycles", "cursor_restarts", "schema_drift"))  # P2 diagnostics
           + (("outcome", "VARCHAR(16)"), ("schema_version", "VARCHAR(32)")),  # P1 outcome taxonomy
           "collector_ingest_batches": (("source", "VARCHAR(32)"), ("job_id", "VARCHAR(80)"), ("batch_id", "VARCHAR(160)"),  # P1 envelope
                                        ("fetched_at", "TIMESTAMP"), ("schema_version", "VARCHAR(32)"),
                                        ("cursor_before", "VARCHAR(512)"), ("cursor_after", "VARCHAR(512)"))}
# P1: the database guarantees one row per source id / one receipt per (job, batch) — created only when no old duplicate exists
UNIQUE = (("uq_ads_source_external", "ads", ("source", "external_id")),
          ("uq_ad_sources_source_ad", "ad_sources", ("source", "source_ad_id")),
          ("uq_ingest_batches_job_batch", "collector_ingest_batches", ("job_id", "batch_id")))


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
        for name, table, cols in UNIQUE:
            if inspect(conn).has_table(table) and unique_index(conn, name, table, cols) == "skipped_duplicates":
                log.warning("%s not created: %s already holds duplicate %s — clean them up, then rerun the migration", name, table, cols)


def unique_index(conn, name: str, table: str, cols: tuple) -> str:
    """created | exists | skipped_duplicates. Never deletes a row: duplicates leave the index uncreated and a warning."""
    if name in {i["name"] for i in inspect(conn).get_indexes(table)}:
        return "exists"
    c = ", ".join(f'"{x}"' for x in cols)
    nulls = " AND ".join(f'"{x}" IS NOT NULL' for x in cols)
    dup = conn.execute(text(f'SELECT 1 FROM "{table}" WHERE {nulls} GROUP BY {c} HAVING count(*) > 1 LIMIT 1')).first()
    if dup:
        return "skipped_duplicates"
    conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}" ON "{table}" ({c})'))
    return "created"


if __name__ == "__main__":
    migrate()
    print("Meta collector tables / columns ready; existing data unchanged.")
