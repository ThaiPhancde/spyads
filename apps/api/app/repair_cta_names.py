"""Split and re-name products named after a CTA / delivery promise ("توصيل مجاني", "CASH ON DELIVERY", "Pagamento alla consegna").

Before naming.DELIVERY those phrases passed as product names, so ingest pooled the ads of unrelated sellers under them
(the only TEST_NOW on 2026-11-04 was "توصيل مجاني": a garden hose, Next fashion, coffee, phones, Talabat… 15 sellers).
Each product is split into parts that are evidently one product (groups) through split_ads (PRODUCT_SPLIT event,
undo in the UI), then every part is named again from its own ads' evidence.
backfill_names.recluster is not used: it resolves ad by ad, and each ad's identity match (same landing / creative) is a
sibling still inside the pooled product, so whole shops came straight back to it.

    python -m app.repair_cta_names            # dry run: list, nothing written
    python -m app.repair_cta_names --apply    # backup → split → name → cluster membership sync → rescore
    python -m app.repair_cta_names --rollback # ads + names back from bak_cta_*, parts the repair created are dropped
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import inspect, select, text

from .db import SessionLocal, auto_migrate
from .models import Ad, Advertiser, Product
from .services import naming as N

BAK = ("bak_cta_products", "bak_cta_ads")
REPORT = Path(__file__).resolve().parents[1] / "data"
BY = "repair_cta_names"


def targets(db) -> list[Product]:
    # is_bad_name, not is_bad_product_name: the stricter one also hits real names ("My Champion Necklace")
    ps = db.scalars(select(Product).where(Product.name_status.is_distinct_from("VERIFIED"),
                                          Product.manual_override.is_not(True))).all()
    return [p for p in ps if p.name_status != "UNKNOWN" and N.is_bad_name(p.canonical_name)]


def seller(a: Ad):
    return a.page_id or a.advertiser_id


def groups(ads: list[Ad]) -> list[list[Ad]]:
    """Parts that are evidently one product, biggest first (it keeps the original product: id, votes, history).
    Same creative / same copy (≥ 20 chars, identity_product's rule) / same product page join — so 15 shops cloning one
    poncho ad stay one product, while the CTA name alone joins nothing. The seller alone joins nothing either: one
    WhatsApp-only shop ran detox patches, a glucose meter and cockroach gel from the same page."""
    from .services.entity_resolution import landing_key

    parent = list(range(len(ads)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[tuple, int] = {}
    for i, a in enumerate(ads):
        copy = " ".join((a.ad_text or "").lower().split())
        lk = landing_key(a.landing_url)
        keys = [("page", lk)] if lk else []
        if len(copy) >= 20:
            keys.append(("copy", copy))
            if a.creative_fingerprint:
                keys.append(("fp", a.creative_fingerprint))
        for k in keys:
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    g = defaultdict(list)
    for i, a in enumerate(ads):
        g[find(i)].append(a)
    return sorted(g.values(), key=lambda x: (-len(x), min(a.id for a in x)))


def rename_from_ads(p: Product, ads: list[Ad], advs: dict) -> None:
    from .backfill_names import _ad_input
    from .services.entity_resolution import index_name, unindex

    unindex(p.id)
    p.canonical_name, p.name_status, p.name_source, p.name_confidence, p.name_vi, p.name_evidence = N.UNKNOWN_LABEL, "UNKNOWN", None, 0.0, None, []
    for a in ads:
        nm = N.best(_ad_input(a, advs.get(a.advertiser_id)))
        N.absorb(p, nm, [{**e, "ad_id": a.id} for e in nm.get("evidence") or []])
    if p.name_status != "UNKNOWN":
        index_name(p.id, p.canonical_name)


def repair(db, p: Product, ads: list[Ad], advs: dict) -> set[int]:
    from .services.entity_resolution import split_ads

    parts = groups(ads)
    out = [(p, parts[0])]
    for part in parts[1:]:
        # no cannot-link: two parts may still be clones (same picture), the clusterer may join them on image evidence
        out.append((split_ads(db, p, [a.id for a in part], None, BY), part))
    for prod, part in out:
        rename_from_ads(prod, part, advs)
    return {prod.id for prod, _ in out}


def backup(db, pids: list[int]) -> None:
    if set(BAK) & set(inspect(db.connection()).get_table_names()):
        raise SystemExit("bak_cta_* already exist — roll back or drop them before a second --apply")
    db.execute(text("CREATE TABLE bak_cta_products AS SELECT id, canonical_name, name_vi, name_status, name_source, "
                    "name_confidence, name_evidence, category FROM products WHERE id = ANY(:p)"), {"p": pids})
    db.execute(text("CREATE TABLE bak_cta_ads AS SELECT id, product_id FROM ads WHERE product_id = ANY(:p)"), {"p": pids})
    db.commit()


def rollback() -> None:
    from .cluster_models import ProductCluster, AdClusterMembership
    from .models import Vote
    from .services.cluster_membership import sync
    from .services.engine import rescore_products
    from .services.entity_resolution import drop_if_empty

    with SessionLocal() as db:
        if not set(BAK) <= set(inspect(db.connection()).get_table_names()):
            print("no bak_cta_* tables — nothing to roll back")
            return
        ad_ids = [i for (i,) in db.execute(text("SELECT id FROM bak_cta_ads")).all()]
        pids = {i for (i,) in db.execute(text("SELECT id FROM bak_cta_products")).all()}
        holders = set(db.scalars(select(Ad.product_id).where(Ad.id.in_(ad_ids)).distinct())) - pids
        db.execute(text("UPDATE ads SET product_id = b.product_id FROM bak_cta_ads b WHERE ads.id = b.id"))
        db.execute(text("UPDATE creatives SET product_id = a.product_id FROM ads a WHERE creatives.ad_id = a.id "
                        "AND a.id = ANY(:a) AND creatives.product_id IS DISTINCT FROM a.product_id"), {"a": ad_ids})
        # move_ads also re-points comments and the ads' stores
        db.execute(text("UPDATE comments SET product_id = a.product_id FROM ads a WHERE comments.ad_id = a.id "
                        "AND a.id = ANY(:a) AND comments.product_id IS DISTINCT FROM a.product_id"), {"a": ad_ids})
        db.execute(text("UPDATE stores SET product_id = a.product_id FROM ads a WHERE stores.id = a.store_id "
                        "AND a.id = ANY(:a) AND stores.product_id = ANY(:h)"), {"a": ad_ids, "h": list(holders)})
        db.execute(text("UPDATE products SET canonical_name = b.canonical_name, name_vi = b.name_vi, name_status = b.name_status, "
                        "name_source = b.name_source, name_confidence = b.name_confidence, category = b.category "
                        "FROM bak_cta_products b WHERE products.id = b.id"))
        sync(db, ad_ids)
        dropped = 0
        for pid in holders:  # parts the repair created are now empty; products that already existed keep their own ads
            if db.scalar(select(Ad.id).where(Ad.product_id == pid).limit(1)) or db.scalar(select(Vote.id).where(Vote.product_id == pid).limit(1)):
                continue
            if db.scalar(select(AdClusterMembership.ad_id).where(AdClusterMembership.cluster_id == pid).limit(1)):
                continue
            if node := db.get(ProductCluster, pid):
                db.delete(node)
                db.flush()
            dropped += drop_if_empty(db, pid)
        rescore_products(db, pids | (holders - {None}))
        db.execute(text("DELETE FROM events WHERE type = 'PRODUCT_SPLIT' AND data->>'by' = :by"), {"by": BY})  # undo rows to dropped parts
        db.execute(text("DROP TABLE bak_cta_ads"))
        db.execute(text("DROP TABLE bak_cta_products"))
        db.commit()
        print(f"rolled back {len(pids)} products / {len(ad_ids)} ads · dropped {dropped} emptied parts")


def run(apply: bool) -> None:
    auto_migrate()
    with SessionLocal() as db:
        advs = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
        ps = targets(db)
        ads_by = {p.id: list(db.scalars(select(Ad).where(Ad.product_id == p.id))) for p in ps}
        rows = [{"id": p.id, "name": p.canonical_name, "status": p.name_status, "decision": p.recommendation,
                 "ads": len(ads_by[p.id]), "sellers": len({seller(a) for a in ads_by[p.id]} - {None}),
                 "parts": len(groups(ads_by[p.id]))} for p in ps]
        out = {"apply": apply, "products": len(ps), "ads": sum(r["ads"] for r in rows), "parts": sum(r["parts"] for r in rows),
               "decisions_before": dict(Counter(r["decision"] for r in rows)), "rows": sorted(rows, key=lambda r: -r["sellers"])}
        if apply and ps:
            from .services.cluster_membership import sync
            from .services.engine import rescore_products

            backup(db, [p.id for p in ps])
            touched, st = set(), Counter()
            for p in ps:
                try:
                    with db.begin_nested():
                        touched |= repair(db, p, ads_by[p.id], advs)
                        sync(db, [a.id for a in ads_by[p.id]])  # overlay follows the moved ads ("reassigned")
                        st["repaired"] += 1
                except Exception as e:  # one product must not stop the run; it keeps its old name and ads
                    st["errors"] += 1
                    print(f"product {p.id}: {e}")
            db.commit()
            ids = sorted(touched)
            for i in range(0, len(ids), 200):
                rescore_products(db, ids[i:i + 200])
                db.commit()
            after = db.execute(select(Product.id, Product.canonical_name, Product.name_status, Product.recommendation)
                               .where(Product.id.in_(ids))).all()
            out |= {"stats": dict(st), "rescored": len(ids), "after": [list(r) for r in after],
                    "decisions_after": dict(Counter(r.recommendation for r in after)),
                    "names_after": dict(Counter(r.name_status for r in after))}
        REPORT.mkdir(exist_ok=True)
        rep = REPORT / f"repair_cta_names_{'apply' if apply else 'dry'}.json"
        rep.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "after")}, ensure_ascii=False, indent=1))
        for r in out["rows"][:15]:
            print(f"  {r['id']:>6} {r['decision'] or '-':8} {r['sellers']:>3} sellers {r['parts']:>3} parts {r['ads']:>4} ads  {r['name'][:45]}")
        print("report →", rep)


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback()
    else:
        run("--apply" in sys.argv)
