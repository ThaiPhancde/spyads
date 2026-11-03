"""Video identity (design §2.2, audit 01 §1.5): creatives whose cover frame pHash lies within VIDEO_DIST bits of a group's
first creative share one `product_images.video_key` (= that first creative's pHash), across every page, network and product.
Trivial hashes (blank / single-bit frames) get no key. Facts per key: every ad (active or not), distinct pages, earliest
sighting, pages a source re-confirmed (adsignals.verified_live), and the pages in order of arrival.

ponytail: cover frame only — a re-upload with a new cover splits into another video. Stored videos now carry a 3-frame
fingerprint (Creative.video_fp, media.video_fingerprint) used by creative families; upgrade = let it join / split keys here. Leader buckets (compare to each group's first hash, no transitive merge):
two groups whose leaders sit 7+ bits apart stay apart even if their members touch.

CLI:  python -m app.videos backfill     (additive: only rows without a key are written)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from .models import ADS_ONLY, Ad, Advertiser, Creative
from .services.adsignals import verified_live
from .services.clustering import MAX_KEY_PRODUCTS
from .services.entity_resolution import TRIVIAL_PHASH
from .services.features import COLLECTED_DATE_SOURCES, adv_key
from .visual.models import ProductImage

VIDEO_DIST = 6
_IMG = and_(ProductImage.source == "creative", ProductImage.source_id == Creative.id)


def assign(db: Session, chunk: int = 2000) -> int:
    """Key every hashed creative cover that has none: nearest existing key within VIDEO_DIST bits, else its own pHash
    (a new video). Incremental — runs after visual.index_pending each tick; the backfill is the same call."""
    rows = db.execute(select(ProductImage.id, ProductImage.phash).join(Creative, _IMG)
                      .where(ProductImage.processing_status == "done", ProductImage.phash.is_not(None),
                             ProductImage.phash.not_in(TRIVIAL_PHASH), ProductImage.video_key.is_(None))
                      .order_by(Creative.id)).all()
    if not rows:
        return 0
    keys = list(db.scalars(select(ProductImage.video_key).where(ProductImage.video_key.is_not(None)).distinct()))
    lead = np.zeros(len(keys) + len(rows), dtype=np.uint64)
    lead[:len(keys)] = [int(k, 16) for k in keys]
    n, out = len(keys), []
    for img_id, h in rows:
        v = np.uint64(int(h, 16))
        d = np.bitwise_count(lead[:n] ^ v)
        i = int(d.argmin()) if n else 0
        if n and d[i] <= VIDEO_DIST:
            key = keys[i]
        else:
            key, lead[n], n = h, v, n + 1
            keys.append(h)
        out.append({"id": img_id, "video_key": key})
        if len(out) >= chunk:
            db.execute(update(ProductImage), out)
            db.commit()
            out = []
    if out:
        db.execute(update(ProductImage), out)
        db.commit()
    return len(rows)


def _facts(key: str, ads: list[Ad], names: dict[int, str], now: datetime, with_ads: bool) -> dict:
    d7 = now - timedelta(days=7)
    pages: dict[str, list[Ad]] = defaultdict(list)
    for a in ads:
        pages[adv_key(a) or f"ad{a.id}"].append(a)
    entrants = []
    for k, lst in pages.items():
        first = min(lst, key=lambda a: a.first_seen_at)
        live = any(verified_live(a, now) for a in lst)
        entrants.append({"page": k, "advertiser_id": first.advertiser_id, "name": names.get(first.advertiser_id), "network": first.network,
                         "first_seen": first.first_seen_at, "ads": len(lst), "verified_live": live,
                         # no page id (TikTok "unverified:ad:…" advertiser): one row per ad, may be the same shop as another entrant
                         "page_known": any(a.page_id for a in lst),
                         # TikTok Top Ads: first_seen_at is our collection day, not a launch (features.COLLECTED_DATE_SOURCES)
                         "new_this_week": first.first_seen_at > d7 and first.source not in COLLECTED_DATE_SOURCES})
    entrants.sort(key=lambda e: e["first_seen"])
    out = {"video_key": key, "ads": len(ads), "pages": len(pages), "pages_unknown_id": sum(not e["page_known"] for e in entrants),
           "verified_live_pages": sum(e["verified_live"] for e in entrants),
           "new_pages_7d": sum(e["new_this_week"] for e in entrants),
           "video_first_seen": entrants[0]["first_seen"] if entrants else None,
           "networks": sorted({a.network for a in ads if a.network}),
           "products": sorted({a.product_id for a in ads if a.product_id}),
           # same rule as clustering: a key spread over more products than this is a template / stock frame, not one video
           "generic": len({a.product_id for a in ads if a.product_id}) > MAX_KEY_PRODUCTS,
           "entrants": entrants}
    if with_ads:
        out["ad_list"] = [{"id": a.id, "network": a.network, "source": a.source, "page_id": a.page_id, "advertiser_id": a.advertiser_id,
                           "advertiser": names.get(a.advertiser_id), "product_id": a.product_id, "first_seen": a.first_seen_at,
                           "last_seen": a.last_seen_at, "active": a.is_active, "verified_live": verified_live(a, now),
                           "landing_url": a.landing_url, "text": (a.ad_text or a.title or "")[:160]}
                          for a in sorted(ads, key=lambda a: a.first_seen_at)]
    return out


def facts_for(db: Session, keys, with_ads: bool = False, now: datetime | None = None) -> dict[str, dict]:
    """{video_key: facts} — every external ad (any network, active or not) whose creative carries the key."""
    keys, now = set(keys), now or datetime.utcnow()
    if not keys:
        return {}
    by: dict[str, dict[int, Ad]] = defaultdict(dict)
    for k, a in db.execute(select(ProductImage.video_key, Ad).join(Creative, _IMG).join(Ad, Ad.id == Creative.ad_id)
                           .where(ProductImage.video_key.in_(keys), ADS_ONLY, Ad.is_internal.is_(False))):
        by[k][a.id] = a
    advs = {a.advertiser_id for d in by.values() for a in d.values() if a.advertiser_id}
    names = dict(db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_(advs))).all()) if advs else {}
    return {k: _facts(k, list(d.values()), names, now, with_ads) for k, d in by.items()}


def _ranked(vids) -> list[dict]:
    return sorted(vids, key=lambda f: (f["generic"], -f["pages"], -f["ads"]))


def product_video_facts(db: Session, pid: int, top: int = 5) -> dict:
    """The product's videos, most pages first; primary = the non-generic one run by the most pages."""
    keys = db.scalars(select(ProductImage.video_key).join(Creative, _IMG).join(Ad, Ad.id == Creative.ad_id)
                      .where(Ad.product_id == pid, ProductImage.video_key.is_not(None)).distinct()).all()
    vids = _ranked(facts_for(db, keys).values())
    real = [f for f in vids if not f["generic"]]
    return {"count": len(real), "primary": real[0] if real else None, "videos": vids[:top]}


def primary_for(db: Session, pids) -> dict[int, dict]:
    """{product_id: primary video facts} for a page of products in two queries (list payloads; same rule as above)."""
    pairs = db.execute(select(ProductImage.video_key, Ad.product_id).join(Creative, _IMG).join(Ad, Ad.id == Creative.ad_id)
                       .where(Ad.product_id.in_(list(pids)), ProductImage.video_key.is_not(None)).distinct()).all() if pids else []
    facts = facts_for(db, {k for k, _ in pairs})
    by: dict[int, list] = defaultdict(list)
    for k, pid in pairs:
        if k in facts and not facts[k]["generic"]:
            by[pid].append(facts[k])
    return {pid: _ranked(v)[0] for pid, v in by.items()}


if __name__ == "__main__":
    from .db import SessionLocal, auto_migrate

    if sys.argv[1:] != ["backfill"]:
        sys.exit("usage: python -m app.videos backfill")
    auto_migrate()  # additive: adds product_images.video_key + index on a DB the servers have not upgraded yet
    with SessionLocal() as s:
        print({"keyed": assign(s)})
