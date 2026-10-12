"""Move CLIP vectors into pgvector (run once after `CREATE EXTENSION vector` works):
    cd apps/api && .venv/Scripts/python -m app.visual.migrate_pgvector [--no-index]
Adds product_images.embedding_vec vector(512), copies every BYTEA float16 embedding in batches (restartable: only rows with
embedding_vec IS NULL), then builds an HNSW cosine index. The app switches to it by itself (embed.pgvector_ready, 5 min cache).
"""
import sys
import time

from sqlalchemy import text

from .. import models  # noqa: F401
from ..db import SessionLocal, engine
from .embed import from_bytes, vec_literal


def run(batch: int = 1000, index: bool = True) -> dict:
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("ALTER TABLE product_images ADD COLUMN IF NOT EXISTS embedding_vec vector(512)"))
    done, t0 = 0, time.time()
    while True:
        with SessionLocal() as db:
            rows = db.execute(text("SELECT id, embedding FROM product_images WHERE embedding IS NOT NULL AND embedding_vec IS NULL ORDER BY id LIMIT :n"), {"n": batch}).all()
            if not rows:
                break
            for i, b in rows:
                db.execute(text("UPDATE product_images SET embedding_vec = CAST(:v AS vector) WHERE id = :i"), {"v": vec_literal(from_bytes(b)), "i": i})
            db.commit()
        done += len(rows)
        print(f"{done} vectors copied ({time.time() - t0:.0f}s)", flush=True)
    if index:
        t = time.time()
        with engine.begin() as c:
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_product_images_embedding_vec ON product_images USING hnsw (embedding_vec vector_cosine_ops)"))
        print(f"HNSW index ready ({time.time() - t:.0f}s)")
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM product_images WHERE embedding_vec IS NOT NULL")).scalar()
    return {"copied_now": done, "total_in_pgvector": n}


if __name__ == "__main__":
    print(run(index="--no-index" not in sys.argv))
