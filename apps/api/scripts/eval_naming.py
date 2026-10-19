"""Naming eval on the live DB (read-only).

    python scripts/eval_naming.py coverage            # how many ads get a name, per source, before vs after
    python scripts/eval_naming.py sample [N] [seed]   # N random named ads per source → scripts/naming_sample.json (label it)
    python scripts/eval_naming.py score               # precision per source from the labelled naming_sample.json

Labelling: set "ok" to true/false on every row of naming_sample.json — true when the name is the product the ad sells
(short is fine, a shop / CTA / slogan / wrong product is false). SOURCE_PRECISION in app/services/naming.py comes from here.
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Ad, Advertiser, Product  # noqa: E402
from app.services import naming  # noqa: E402

SAMPLE = Path(__file__).with_name("naming_sample.json")
OLD_BAD = ("chưa đặt tên", " creative")


def ad_rows(db):
    advs = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
    names = dict(db.execute(select(Product.id, Product.canonical_name)).all())
    for a in db.scalars(select(Ad)).yield_per(2000):
        yield a, {"channel": a.channel, "title": a.title, "ad_text": a.ad_text, "landing_url": a.landing_url,
                  "advertiser": advs.get(a.advertiser_id), "cta_type": a.cta_type, "cta_text": a.cta_text, "funnel": a.funnel,
                  "matched_query": None}, names.get(a.product_id) or ""


def coverage():
    with SessionLocal() as db:
        src, before_bad, after_unknown, fixed, types = Counter(), 0, 0, 0, Counter()
        n = 0
        for a, d, pname in ad_rows(db):
            n += 1
            b = naming.best(d)
            src[b["source"] or "UNKNOWN"] += 1
            bad = any(x in pname.lower() for x in OLD_BAD) or pname.lower().startswith("unknown")
            before_bad += bad
            after_unknown += b["name"] is None
            fixed += bad and b["name"] is not None
            types[naming.ad_type(d, b)] += 1
        print(f"ads {n} · before: {before_bad} ads in unnamed products · after: {after_unknown} ads with no evidence · "
              f"{fixed} unnamed ads now named")
        print("by source", dict(src.most_common()))
        print("ad types", dict(types.most_common()))


def sample(k=40, seed=7):
    random.seed(seed)
    by: dict[str, list] = {}
    with SessionLocal() as db:
        for a, d, _ in ad_rows(db):
            b = naming.best(d)
            if b["name"]:
                by.setdefault(b["source"], []).append({"ad_id": a.id, "source": b["source"], "name": b["name"],
                                                       "title": (a.title or "")[:200], "text": (a.ad_text or "")[:200],
                                                       "url": a.landing_url, "advertiser": d["advertiser"], "ok": None})
    rows = [r for s in by.values() for r in random.sample(s, min(k, len(s)))]
    SAMPLE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(rows)} rows → {SAMPLE}", Counter(r["source"] for r in rows))


def score():
    rows = json.loads((SAMPLE.with_name("naming_labels.json") if "--labels" in sys.argv else SAMPLE).read_text(encoding="utf-8"))
    for s in sorted({r["source"] for r in rows}):
        lab = [r["ok"] for r in rows if r["source"] == s and r["ok"] is not None]
        print(f"{s:9s} precision {sum(lab) / max(1, len(lab)):.2f}  (n={len(lab)})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "coverage"
    {"coverage": coverage, "sample": lambda: sample(*map(int, sys.argv[2:4])), "score": score}[cmd]()
