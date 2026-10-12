-- Image search: move the CLIP vectors from BYTEA (float16, in-RAM cosine) to pgvector once the extension is available.
-- Prerequisite: pgvector built/installed for THIS PostgreSQL (18.x on Windows needs MSVC: https://github.com/pgvector/pgvector#windows),
-- then run as a superuser. Safe to run while the app is up: the app keeps using product_images.embedding (BYTEA) until
-- app/visual/embed.py is switched to the `embedding_vec` column (EmbIndex.topk → ORDER BY embedding_vec <=> :q LIMIT k).
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE product_images ADD COLUMN IF NOT EXISTS embedding_vec vector(512);

-- float16 BYTEA → vector(512): decode in Python once (no SQL float16 decoder), e.g.
--   python -c "from app.visual.migrate_pgvector import run; run()"   (see docs/IMAGE_SEARCH.md §ops)
-- The helper streams rows: SELECT id, embedding FROM product_images WHERE embedding IS NOT NULL AND embedding_vec IS NULL
-- and UPDATEs embedding_vec = '[...]'::vector in batches of 1000.

-- cosine index (HNSW; build after the backfill — it is the slow part)
CREATE INDEX IF NOT EXISTS ix_product_images_embedding_vec ON product_images USING hnsw (embedding_vec vector_cosine_ops);

-- query shape the app will use:
--   SELECT id, 1 - (embedding_vec <=> :q) AS cos FROM product_images
--   WHERE processing_status = 'done' AND embedding_vec IS NOT NULL ORDER BY embedding_vec <=> :q LIMIT 300;
