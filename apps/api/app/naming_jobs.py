"""Background naming work (worker scheduler, or `python -m app.backfill_names --llm N / --translate N`).

  naming_ai     UNKNOWN products: evidence rules first (no model), then batched AI over the provider chain (see naming_ai.py).
  translate     products with a name but no name_vi → short Vietnamese name (the evidenced name translated, nothing added).

Both are resumable by construction (the columns are the checkpoint), spend at most NAMING_LLM_DAILY model calls a day
and stop at the first failure (llm.py cools down 5 min after a 429; the next tick continues).
"""
from __future__ import annotations

import logging
import os
from datetime import date

from sqlalchemy import select

from .models import Product, ProductAlias
from .services import llm

log = logging.getLogger(__name__)
# Gemini free tier = 20 requests / day / model (2026-10-13), shared with ingest's own model pass: naming takes at most
# this many per day so new ads still get theirs. ponytail: in-process counter, resets with the worker — a restart re-grants it.
DAILY_CALLS = int(os.getenv("NAMING_LLM_DAILY", "12"))
_spent = {"day": None, "n": 0}


def _budget() -> bool:
    today = date.today()
    if _spent["day"] != today:
        _spent.update(day=today, n=0)
    if _spent["n"] >= DAILY_CALLS:
        return False
    _spent["n"] += 1
    return True


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "name_vi": {"type": ["string", "null"]}},
        "required": ["i", "name_vi"], "additionalProperties": False}}},
    "required": ["results"], "additionalProperties": False,
}


def translate(db, limit: int = 100) -> dict:
    """Vietnamese display names for named products, in-target and best opportunity first."""
    if not llm.available():
        return {"translated": 0, "skipped": "llm unavailable"}
    ps = db.scalars(select(Product).where(Product.name_vi.is_(None), Product.name_status.in_(["VERIFIED", "AUTO_CONFIRMED", "SUGGESTED"]))
                    .order_by(Product.in_target.desc().nullslast(), Product.opportunity_score.desc()).limit(limit)).all()
    if not ps:
        return {"translated": 0}
    lines = "\n".join(f"{i}. {p.canonical_name}" for i, p in enumerate(ps))
    if not _budget():
        return {"translated": 0, "skipped": f"daily model budget used ({DAILY_CALLS})"}
    data = llm.complete_json(
        system=("Translate each e-commerce product name into a short natural Vietnamese product name for a marketing team "
                "(at most 7 words). Keep brand, model, material, size and the key feature; drop marketing words, prices and "
                "keyword stuffing. Do not add anything the name does not say. If a line is not a product name, return null. "
                "Use the number as i."),
        user=lines, schema=TRANSLATE_SCHEMA, max_tokens=6000)
    if not data:
        return {"translated": 0, "skipped": "llm failed"}
    by_i = {r["i"]: (r.get("name_vi") or "").strip() for r in data.get("results", [])}
    n = 0
    for i, p in enumerate(ps):
        vi = by_i.get(i)
        if not vi:
            continue
        p.name_vi = vi[:255]
        if vi.lower() not in {a.name.lower() for a in p.aliases}:  # Vietnamese name joins matching + alias search
            db.add(ProductAlias(product_id=p.id, name=p.name_vi, source="name_vi", match_score=1.0))
        n += 1
    db.flush()
    return {"translated": n, "asked": len(ps)}


def tick(db) -> dict:
    """Scheduler: one model call of each kind per tick. Writes under the ingest lock (product merges vs. ingest)."""
    from .db import ingest_lock

    from . import naming_ai

    out = {"rules": naming_ai.rule_pass(db, limit=int(os.getenv("NAMING_RULES_PER_TICK", "300")))}
    db.commit()
    out["unknown"] = naming_ai.ai_pass(db, batches=int(os.getenv("NAMING_AI_BATCHES_PER_TICK", "2")), lock=ingest_lock)
    db.commit()
    # translate() is no longer scheduled: names stay in their own language; Vietnamese search goes through query_intent.
    # Run it by hand (backfill_names --translate N) only to add Vietnamese search keywords.
    if ids := out["unknown"].get("product_ids"):
        from .services.engine import rescore_products

        rescore_products(db, ids)
        db.commit()
    return out
