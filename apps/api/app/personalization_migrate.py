"""Phase 9: additive indexes for the personalization read paths, chosen from EXPLAIN ANALYZE on the real database
(docs/personalization_progress.md §18). Idempotent; PostgreSQL only; never touches rows."""
import logging

from sqlalchemy import text

INDEXES = (
    # similar.product_candidates: same-category candidates by opportunity — was a seq scan of the wide products table
    "CREATE INDEX IF NOT EXISTS ix_products_category_opportunity ON products (category, opportunity_score DESC)",
    # recommend.pool: top in-target products by opportunity — was a full sort
    "CREATE INDEX IF NOT EXISTS ix_products_target_opportunity ON products (opportunity_score DESC) WHERE in_target IS TRUE",
    # similar._image_rows / recommend.vectors: first embedded image per creative — index-only instead of 1 KB heap rows
    "CREATE INDEX IF NOT EXISTS ix_product_images_creative_done ON product_images (source_id, id) "
    "WHERE source = 'creative' AND processing_status = 'done'",
)


def migrate(bind=None):
    from .db import engine
    eng = bind or engine
    if eng.dialect.name != "postgresql":
        return False
    try:
        with eng.begin() as c:
            for sql in INDEXES:
                c.execute(text(sql))
            for table in ("products", "product_images"):
                c.execute(text(f"ANALYZE {table}"))
        return True
    except Exception:  # noqa: BLE001 — missing privilege: the app still works, only slower
        logging.getLogger("app.personalization").warning("personalization indexes not created", exc_info=True)
        return False


if __name__ == "__main__":
    print("indexes ready:", migrate())
