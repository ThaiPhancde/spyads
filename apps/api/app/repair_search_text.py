"""Undo the keyword tag old ingests prepended to ads.search_text (ingest.py, before 2026-10-14).

    python -m app.repair_search_text            # dry run: how many ads carry a tag that is not in the ad's own fields
    python -m app.repair_search_text --apply    # strip it (originals kept in bak_search_text, one row per ad)
    python -m app.repair_search_text --rollback # put the originals back

The tag was `<keyword> ` + the real text, added only when the keyword was NOT in the real text: every ad an ad library returned
for a loosely matched keyword became an "exact" hit. A row is repaired only when search_text starts with a keyword the app
ever searched (search_jobs / tracked_queries) AND that keyword is in none of the ad's own fields — so nothing legit is cut.
Marketplace rows are repaired too: AliExpress / 1688 return unrelated listings for a foreign-language keyword, so the tag was
never evidence.
"""
from __future__ import annotations

import sys

from sqlalchemy import select, text

from .db import SessionLocal, auto_migrate
from .models import Ad, Advertiser, SearchJob, TrackedQuery


def keywords(db) -> set[str]:
    kws: set[str] = set()
    for q in db.scalars(select(SearchJob.query)):
        kws |= {k.strip().lower() for k in (q or "").split("|") if k.strip()}
    for q in db.scalars(select(TrackedQuery.query)):
        if q:
            kws.add(q.strip().lower())
    return {k for k in kws if len(k) >= 2}


def tagged(a: Ad, adv: str | None, kws: set[str]) -> str | None:
    st = a.search_text or ""
    own = " ".join(filter(None, [a.raw_product_name, a.title, a.ad_text, adv, a.landing_url])).lower()
    for k in sorted(kws, key=len, reverse=True):
        if st.startswith(k + " ") and k not in own:
            return k
    return None


def run(apply: bool, rollback: bool = False) -> dict:
    auto_migrate()
    with SessionLocal() as db:
        if rollback:
            db.execute(text("UPDATE ads SET search_text = b.search_text FROM bak_search_text b WHERE ads.id = b.ad_id"))
            db.commit()
            return {"rolled_back": True}
        kws = keywords(db)
        advs = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
        todo = []
        for a in db.scalars(select(Ad).where(Ad.search_text.is_not(None))):
            if k := tagged(a, advs.get(a.advertiser_id), kws):
                todo.append((a, k))
        out = {"keywords": len(kws), "tagged_ads": len(todo)}
        if apply and todo:
            db.execute(text("CREATE TABLE IF NOT EXISTS bak_search_text (ad_id INTEGER PRIMARY KEY, search_text TEXT)"))
            for a, k in todo:
                db.execute(text("INSERT INTO bak_search_text VALUES (:i, :s) ON CONFLICT (ad_id) DO NOTHING"), {"i": a.id, "s": a.search_text})
                a.search_text = a.search_text[len(k) + 1:]
            db.commit()
            out["repaired"] = len(todo)
        return out


if __name__ == "__main__":
    print(run("--apply" in sys.argv, "--rollback" in sys.argv))
