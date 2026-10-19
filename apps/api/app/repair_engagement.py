"""Undo the "unknown engagement = 0" the old ingest wrote (ingest.py `int(rec.comments or 0)`), 2026-10-14.

    python -m app.repair_engagement             # dry run: how many ads carry a 0 the source never reported
    python -m app.repair_engagement --apply     # NULL them (originals in bak_engagement), then write the first engagement history rows
    python -m app.repair_engagement --rollback  # put the originals back

Rules (a real 0 reported by the source is kept; anything we cannot prove is NULL = N/A on the card):
  * sources that never carry engagement (Meta Ad Library, marketplaces, TikTok Ad Library / commercial): likes / comments / shares that are
    0 become NULL.
  * TikTok Creative Center (tiktok_top_ads, apify_tiktok_top_ads): the raw batches in data/raw keep what the source really sent
    (`comments: null` for ~92 % of ads): the latest raw record decides each field; a field null in raw → NULL; an ad with no raw record
    keeps a non-zero value and loses a zero (unprovable).
"""
from __future__ import annotations

import glob
import gzip
import json
import sys
from pathlib import Path

from sqlalchemy import select, text

from .db import SessionLocal, auto_migrate
from .engagement.service import record_ad_scope
from .models import Ad

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
NEVER = ("meta_library", "meta_ads", "apify_fb_ads", "aliexpress_search", "ali1688", "apify_1688", "apify_taobao", "tiktok_ad_library", "tiktok_commercial")
CC = ("tiktok_top_ads", "apify_tiktok_top_ads")


def raw_engagement(source: str) -> dict[str, dict]:
    """source_ad_id → {likes, comments, shares} from the newest raw record of each ad (files are date-ordered)."""
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(RAW / source / "*" / "*.jsonl.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                for r in (rec if isinstance(rec, list) else [rec]):
                    if isinstance(r, dict) and r.get("source_ad_id") is not None:
                        out[str(r["source_ad_id"])] = {"likes": r.get("likes"), "comments": r.get("comments"), "shares": r.get("shares")}
    return out


def plan(db) -> list[tuple[Ad, dict]]:
    """[(ad, new values)] for every ad whose stored engagement changes."""
    todo = []
    raw = {s: raw_engagement(s) for s in CC}
    for a in db.scalars(select(Ad).where(Ad.source.in_(NEVER + CC))):
        cur = {"likes": a.likes, "comments": a.comments_count, "shares": a.shares}
        if a.source in NEVER:
            new = {k: (None if v == 0 else v) for k, v in cur.items()}
        else:
            r = raw[a.source].get(a.external_id)
            if r is not None:
                new = {k: (r[k] if r[k] is not None else None) for k in cur}   # the source's own word, null included
            else:
                new = {k: (None if v == 0 else v) for k, v in cur.items()}
        if new != cur:
            todo.append((a, new))
    return todo


def run(apply: bool, rollback: bool = False) -> dict:
    auto_migrate()   # drops NOT NULL on ads.likes / comments_count / shares
    with SessionLocal() as db:
        if rollback:
            db.execute(text("UPDATE ads SET likes = b.likes, comments_count = b.comments_count, shares = b.shares FROM bak_engagement b WHERE ads.id = b.ad_id"))
            db.commit()
            return {"rolled_back": True}
        todo = plan(db)
        out = {"ads_changed": len(todo), "by_source": {}}
        for a, _ in todo:
            out["by_source"][a.source] = out["by_source"].get(a.source, 0) + 1
        if not apply:
            return out
        db.execute(text("CREATE TABLE IF NOT EXISTS bak_engagement (ad_id INTEGER PRIMARY KEY, likes INTEGER, comments_count INTEGER, shares INTEGER)"))
        for a, new in todo:
            db.execute(text("INSERT INTO bak_engagement VALUES (:i, :l, :c, :s) ON CONFLICT (ad_id) DO NOTHING"),
                       {"i": a.id, "l": a.likes, "c": a.comments_count, "s": a.shares})
            a.likes, a.comments_count, a.shares = new["likes"], new["comments"], new["shares"]
        db.commit()
        n = 0
        for a in db.scalars(select(Ad).where((Ad.likes.is_not(None)) | (Ad.comments_count.is_not(None)) | (Ad.shares.is_not(None)) | (Ad.views.is_not(None)))):
            if record_ad_scope(db, a, a.source):
                n += 1
                if n % 500 == 0:
                    db.commit()
        db.commit()
        out["history_rows"] = n
        return out


if __name__ == "__main__":
    print(run("--apply" in sys.argv, "--rollback" in sys.argv))
