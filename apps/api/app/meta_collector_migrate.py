"""Additive, repeatable migration: python -m app.meta_collector_migrate."""
from .db import engine
from .models import IngestBatch, MetaCollation


def migrate(bind=None):
    with (bind or engine).begin() as conn:
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            conn.execute(text("SELECT pg_advisory_xact_lock(727004)"))
        for model in (IngestBatch, MetaCollation):
            model.__table__.create(conn, checkfirst=True)


if __name__ == "__main__":
    migrate()
    print("Meta collector tables ready; existing data unchanged.")
