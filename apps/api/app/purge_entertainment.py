"""Remove short-drama / movie / trailer / streaming-app / podcast ads already in the DB (services/content_filter rules).

    python -m app.purge_entertainment                 # dry run: counts, reasons, sample, freed bytes + CSV of every verdict
    python -m app.purge_entertainment --apply         # backup rows → verify backup → record exclusions → delete → re-score
    python -m app.purge_entertainment --reviews       # the review queue (kept ads with one weak entertainment hint)
    python -m app.purge_entertainment --allow 12 34   # human: these ads are fine, never flag them again
    python -m app.purge_entertainment --block 56 78   # human: entertainment — exclude + delete now

Exclusions are written for every provider that reported a deleted ad (ad_sources), so no source re-ingests it.
Marketplace listings (channel=commerce) are never touched. Backups: data/backups/entertainment-<ts>.json.gz (rows only,
no media — the media is what we are throwing away).
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter
from datetime import datetime

from sqlalchemy import inspect as sa_inspect, select

from .db import SessionLocal, auto_migrate
from .eu_market.models import EUAdMatch
from .models import Ad, AdSource, Advertiser, Creative, Product
from .purge_removed import _chunks, plan, purge_ads
from .services.content_filter import RULES_VERSION, ContentExclusion, _host, _name, classify
from .storage import DATA_DIR
from .visual.models import ProductImage


def scan(db) -> dict[int, tuple[str, list[str], object]]:
    """ad id → (verdict, reasons, row) for every ad the rules (or a human) flag. Two passes: a page that ran one blocked
    ad counts as an entertainment page for its other ads."""
    rows = db.execute(select(Ad.id, Ad.source, Ad.external_id, Ad.page_id, Ad.title, Ad.ad_text, Ad.cta_type, Ad.cta_text,
                             Ad.landing_url, Ad.price, Ad.country, Ad.channel, Ad.product_id, Ad.source_industry, Ad.platform, Advertiser.name.label("advertiser"))
                      .outerjoin(Advertiser, Advertiser.id == Ad.advertiser_id)).all()
    ocr: dict[int, str] = {}
    for ad_id, t in db.execute(select(Creative.ad_id, Creative.ocr_text).where(Creative.ocr_text.is_not(None), Creative.ad_id.is_not(None))):
        ocr[ad_id] = f"{ocr.get(ad_id, '')} {t}"
    human = {(s, i): d for s, i, d in db.execute(select(ContentExclusion.source, ContentExclusion.source_ad_id, ContentExclusion.decision)
                                                 .where(ContentExclusion.decided_by == "human"))}
    out: dict[int, tuple[str, list[str], object]] = {}

    def judge(r, blocked_page=False):
        if r.channel == "commerce":
            return None
        if (h := human.get((r.source, r.external_id))) in ("allow", "block"):
            return (h, ["human"]) if h == "block" else None
        v, why = classify(r.advertiser, r.title, r.ad_text, r.cta_type, r.cta_text, r.landing_url, r.price, ocr.get(r.id),
                          r.country, blocked_page, r.source_industry, r.platform)
        return None if v == "keep" else (v, why)

    for r in rows:
        if j := judge(r):
            out[r.id] = (*j, r)
    bad_pages = {r.page_id for v, _, r in out.values() if v == "block" and r.page_id}
    bad_names = {_name(r.advertiser) for v, _, r in out.values() if v == "block" and _name(r.advertiser)}
    for r in rows:
        if (r.page_id in bad_pages or _name(r.advertiser) in bad_names) and (r.id not in out or out[r.id][0] == "review") and (j := judge(r, True)) and j[0] == "block":
            out[r.id] = (*j, r)
    return out


def _dump(db, objs) -> list[dict]:
    out = []
    for o in objs:
        d = {c.key: getattr(o, c.key) for c in sa_inspect(o).mapper.column_attrs}
        d.pop("embedding", None)
        out.append({k: v.isoformat() if isinstance(v, datetime) else v for k, v in d.items()})
    return out


def backup(db, p: dict) -> tuple[str, dict]:
    ids = p["ad_ids"]
    data = {
        "rules_version": RULES_VERSION, "taken_at": datetime.utcnow().isoformat(),
        "ads": _dump(db, [a for ch in _chunks(ids) for a in db.scalars(select(Ad).where(Ad.id.in_(ch)))]),
        "ad_sources": _dump(db, [a for ch in _chunks(ids) for a in db.scalars(select(AdSource).where(AdSource.ad_id.in_(ch)))]),
        "creatives": _dump(db, p["creatives"]),
        "product_images": _dump(db, [i for ch in _chunks(p["images"]) for i in db.scalars(select(ProductImage).where(ProductImage.id.in_(ch)))]),
        "eu_ad_matches": _dump(db, [m for ch in _chunks(ids) for m in db.scalars(select(EUAdMatch).where(EUAdMatch.ad_id.in_(ch)))]),
        "products_removed": _dump(db, [x for ch in _chunks(p["orphans"]) for x in db.scalars(select(Product).where(Product.id.in_(ch)))]),
    }
    d = DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"entertainment-{datetime.utcnow():%Y%m%d-%H%M%S}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    with gzip.open(path, "rt", encoding="utf-8") as f:  # verify: readable and complete before anything is deleted
        back = json.load(f)
    counts = {k: len(v) for k, v in back.items() if isinstance(v, list)}
    if counts["ads"] != len(ids) or counts["creatives"] != len(p["creatives"]):
        raise SystemExit(f"backup check failed {counts} — nothing deleted")
    return str(path), counts


def record_exclusions(db, flagged: dict, ad_ids: list[int], decided_by: str = "cleanup") -> None:
    """block rows for every provider id of the deleted ads; review rows (queue) for the kept doubtful ones."""
    have = {(s, i): e for e in db.scalars(select(ContentExclusion)) for s, i in [(e.source, e.source_ad_id)]}
    prov: dict[int, list[tuple[str, str]]] = {}
    for ch in _chunks(ad_ids):
        for a, s, i in db.execute(select(AdSource.ad_id, AdSource.source, AdSource.source_ad_id).where(AdSource.ad_id.in_(ch))):
            prov.setdefault(a, []).append((s, i))
    for ad_id, (verdict, why, r) in flagged.items():
        keys = {(r.source, r.external_id), *prov.get(ad_id, [])} if verdict == "block" else {(r.source, r.external_id)}
        for s, i in keys:
            e = have.get((s, i))
            if e is None:
                e = ContentExclusion(source=s, source_ad_id=i)
                db.add(e)
                have[(s, i)] = e
            elif e.decided_by == "human" and e.decision != verdict:
                continue
            e.page_id, e.advertiser, e.landing_host = r.page_id, _name(r.advertiser) or None, _host(r.landing_url)[:255] or None
            e.decision, e.reasons, e.rules_version, e.ad_id = verdict, why, RULES_VERSION, ad_id
            e.decided_by = e.decided_by if e.decided_by == "human" else decided_by
    db.commit()


def report(flagged: dict, p: dict) -> str:
    block = {k: v for k, v in flagged.items() if v[0] == "block"}
    review = {k: v for k, v in flagged.items() if v[0] == "review"}
    first = Counter(v[1][0].split(":")[0] for v in block.values())
    lines = [f"rules {RULES_VERSION}",
             f"block {len(block)} ads · review (kept, queued) {len(review)}",
             f"delete: creatives {len(p['creatives'])} · images {len(p['images'])} · files {len(p['files'])} "
             f"({p['bytes'] / 1e6:.1f} MB) · products emptied {len(p['orphans'])} · products re-scored {len(p['rescore'])}",
             "block by first reason: " + ", ".join(f"{k} {n}" for k, n in first.most_common()),
             "top advertisers: " + ", ".join(f"{a} {n}" for a, n in Counter((v[2].advertiser or "?") for v in block.values()).most_common(15))]
    return "\n".join(lines)


def write_csv(flagged: dict) -> str:
    d = DATA_DIR / "cleanup"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"entertainment-{datetime.utcnow():%Y%m%d-%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ad_id", "verdict", "source", "advertiser", "title", "text", "cta", "landing", "reasons"])
        for ad_id, (v, why, r) in sorted(flagged.items(), key=lambda x: (x[1][0], x[1][2].advertiser or "")):
            w.writerow([ad_id, v, r.source, r.advertiser, (r.title or "")[:120], (r.ad_text or "")[:300].replace("\n", " "),
                        r.cta_type, r.landing_url, " | ".join(why)])
    return str(path)


def _human(db, ad_ids: list[int], decision: str) -> dict:
    rows = db.execute(select(Ad.id, Ad.source, Ad.external_id, Ad.page_id, Ad.landing_url, Advertiser.name.label("advertiser"))
                      .outerjoin(Advertiser, Advertiser.id == Ad.advertiser_id).where(Ad.id.in_(ad_ids))).all()
    flagged = {r.id: (decision, ["human"], r) for r in rows}
    record_exclusions(db, flagged, [r.id for r in rows], decided_by="human")
    return flagged


def main(argv: list[str]) -> None:
    auto_migrate()  # content_exclusions may not exist yet if the API has not restarted since this code landed
    with SessionLocal() as db:
        if "--reviews" in argv:
            for e in db.scalars(select(ContentExclusion).where(ContentExclusion.decision == "review").order_by(ContentExclusion.id)):
                print(f"ad {e.ad_id} · {e.source}:{e.source_ad_id} · {e.advertiser} · {e.landing_host} · {' | '.join(e.reasons or [])}")
            return
        for flag in ("--allow", "--block"):
            if flag in argv:
                ids = [int(x) for x in argv[argv.index(flag) + 1:] if x.isdigit()]
                _human(db, ids, flag[2:])
                if flag == "--block":
                    p = plan(db, Ad.id.in_(ids))
                    print(f"backup {backup(db, p)[0]}; files removed {purge_ads(db, p)}")
                print(f"{flag[2:]}: {len(ids)} ads")
                return
        flagged = scan(db)
        block_ids = sorted(k for k, v in flagged.items() if v[0] == "block")
        p = plan(db, Ad.id.in_(block_ids or [-1]))
        print(report(flagged, p))
        print(f"csv: {write_csv(flagged)}")
        if "--apply" not in argv:
            print("dry run — nothing changed. Re-run with --apply to back up and delete.")
            return
        path, counts = backup(db, p)
        print(f"backup verified: {path} {counts}")
        record_exclusions(db, flagged, block_ids)
        files = purge_ads(db, p)
        print(f"deleted {len(block_ids)} ads, {files} stored files; ads left {db.query(Ad).count()}")


if __name__ == "__main__":
    main(sys.argv[1:])
