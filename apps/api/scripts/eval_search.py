"""Multilingual search eval on the live DB (read-only), before (expand=false = old matching) vs after (expand=true).

    python scripts/eval_search.py            # recall per query + top-10 dump → scripts/search_eval.json (label "ok")
    python scripts/eval_search.py score      # precision@10 from the labelled scripts/search_eval.json

Recall: an intent's relevant pool = products the English source terms find with the old matching (words anywhere,
e.g. "feng shui bracelet" | "fengshui bracelet"); recall of a query = share of that pool it returns.
"before" = old matching (expand=false + words-anywhere exact), "after" = current. Precision@10: hand labels on the top 10.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import and_  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.routers import intel  # noqa: E402
from app.routers.intel import search  # noqa: E402

NEW_EXACT = intel._exact
OLD_EXACT = lambda kw: and_(*[intel._ad_has(t) for t in kw.split()])  # matching before 2026-10-13: words anywhere, no expansion

OUT = Path(__file__).with_name("search_eval.json")
INTENTS = {  # pool terms (English, exact) → queries in other languages
    "feng shui bracelet": (["feng shui bracelet", "fengshui bracelet"],
                           ["vòng tay phong thủy", "feng shui bracelet", "pulseras swerte", "سوار حظ", "风水手链"]),
    "posture corrector": (["posture corrector", "back brace"], ["đai chống gù", "posture corrector", "مصحح القوام"]),
    "sunscreen": (["sunscreen", "sunblock"], ["kem chống nắng", "sunscreen", "واقي شمس"]),
    "massage gun": (["massage gun"], ["súng massage", "massage gun", "مسدس مساج"]),
    "perfume": (["perfume", "fragrance"], ["nước hoa", "perfume", "pabango", "عطر"]),
    "teeth whitening": (["teeth whitening", "whitening strips"], ["làm trắng răng", "teeth whitening"]),
}


def ids(db, q, expand, limit=200):
    intel._exact = NEW_EXACT if expand else OLD_EXACT
    r = search(q=q, expand=expand, limit=limit, db=db, active_only=False)
    return [c["id"] for c in r["rows"]], r["rows"], r["total"]


def run():
    out = []
    with SessionLocal() as db:
        for intent, (pool_terms, queries) in INTENTS.items():
            pool = set()
            for t in pool_terms:
                pool |= set(ids(db, t, False, 500)[0])
            for q in queries:
                row = {"intent": intent, "query": q, "pool": len(pool)}
                for mode, exp in (("before", False), ("after", True)):
                    got, rows, total = ids(db, q, exp, 500)
                    row[mode] = {"total": total, "recall": round(len(pool & set(got)) / max(1, len(pool)), 3),
                                 "top10": [{"id": c["id"], "name": c["name"], "tier": c["match_tier"], "ok": None} for c in rows[:10]]}
                out.append(row)
                print(f"{intent:18s} {q!r:24s} pool={len(pool):4d}  recall before={row['before']['recall']:.2f} "
                      f"({row['before']['total']})  after={row['after']['recall']:.2f} ({row['after']['total']})")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def score():
    rows = json.loads(OUT.read_text(encoding="utf-8"))
    for mode in ("before", "after"):
        lab = [x["ok"] for r in rows for x in r[mode]["top10"] if x["ok"] is not None]
        per_q = [sum(x["ok"] for x in r[mode]["top10"]) / 10 for r in rows if any(x["ok"] is not None for x in r[mode]["top10"])]
        print(f"{mode}: P@10 (mean over {len(per_q)} queries) = {sum(per_q) / max(1, len(per_q)):.2f} · labelled {len(lab)}")
        rec = [r[mode]["recall"] for r in rows]
        print(f"{mode}: recall mean = {sum(rec) / len(rec):.2f}")


if __name__ == "__main__":
    score() if sys.argv[1:] == ["score"] else run()
