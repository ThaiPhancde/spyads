"""Backfill product names on existing data (docs/PRODUCT_NAMING.md §5).

    python -m app.backfill_names                 # dry run: what would change, nothing written
    python -m app.backfill_names --apply         # backup → ads → unnamed / bad names → legacy names → rescore
    python -m app.backfill_names --llm 200       # model pass for up to 200 UNKNOWN ads (free Gemini tier, resumable)
    python -m app.backfill_names --translate 400 # Vietnamese names for up to 400 products
    python -m app.backfill_names --rollback      # restore names / ad → product links from the backup tables

Order (spec §5): 1. products without a name ("(chưa đặt tên)", "Unknown…") · 2. CTA / shop / advertiser / headline
names · 3. names that contradict their own ads' listing / product-page evidence · 4. everything else gets a status.
Checkpoints are the columns themselves (ads.name_source, products.name_status IS NULL = not done): re-running resumes,
a failing product is logged, rolled back alone and retried next run. No model / OCR call here unless --llm / --translate.
Backup: bak_naming_* tables (created once, on the first --apply); a pg_dump before --apply is still recommended.
"""
from __future__ import annotations

import logging
import sys
import time
from collections import Counter, defaultdict

from sqlalchemy import func, inspect, or_, select, text

from .db import SessionLocal, auto_migrate, engine
from .models import Ad, Advertiser, Product
from .services import enrichment as E
from .services import lexicon
from .services import naming as N
from .services.entity_resolution import drop_if_empty, move_ads, resolve_product, unindex

log = logging.getLogger("backfill_names")
BAK = ("bak_naming_products", "bak_naming_ads", "bak_naming_meta")


def _ad_input(a: Ad, adv: str | None) -> dict:
    return {"channel": a.channel, "title": a.title, "ad_text": a.ad_text, "landing_url": a.landing_url, "advertiser": adv,
            "cta_type": a.cta_type, "cta_text": a.cta_text, "funnel": a.funnel}


# ------------------------------------------------------------ backup / rollback
def backup() -> None:
    with engine.begin() as c:
        have = set(inspect(c).get_table_names())
        if "bak_naming_meta" in have:
            log.info("backup tables already exist — kept (first --apply wins)")
            return
        c.execute(text("CREATE TABLE bak_naming_products AS SELECT id, canonical_name, category, name_vi, name_status, "
                       "name_source, name_confidence, manual_override FROM products"))
        c.execute(text("CREATE TABLE bak_naming_ads AS SELECT id, product_id, raw_product_name, ad_type, name_source FROM ads"))
        c.execute(text("CREATE TABLE bak_naming_meta AS SELECT MAX(id) AS max_product_id, MAX(id) AS max_alias_id FROM products"))
        c.execute(text("UPDATE bak_naming_meta SET max_alias_id = (SELECT COALESCE(MAX(id), 0) FROM product_aliases)"))
    log.info("backup → bak_naming_products / bak_naming_ads / bak_naming_meta")


def rollback() -> None:
    with SessionLocal() as db:
        if "bak_naming_meta" not in inspect(db.connection()).get_table_names():
            print("no backup tables — nothing to roll back")
            return
        max_pid, max_alias = db.execute(text("SELECT max_product_id, max_alias_id FROM bak_naming_meta")).one()
        db.execute(text("UPDATE ads SET product_id = b.product_id, raw_product_name = b.raw_product_name, ad_type = b.ad_type, "
                        "name_source = b.name_source FROM bak_naming_ads b WHERE ads.id = b.id"))
        db.execute(text("UPDATE creatives SET product_id = a.product_id FROM ads a WHERE creatives.ad_id = a.id "
                        "AND creatives.product_id IS DISTINCT FROM a.product_id"))
        db.execute(text("UPDATE products SET canonical_name = b.canonical_name, category = b.category, name_vi = b.name_vi, "
                        "name_status = b.name_status, name_source = b.name_source, name_confidence = b.name_confidence, "
                        "manual_override = b.manual_override, name_evidence = NULL, name_history = NULL "
                        "FROM bak_naming_products b WHERE products.id = b.id"))
        db.execute(text("DELETE FROM product_aliases WHERE id > :a"), {"a": max_alias})
        # ponytail: products the backfill deleted (emptied buckets) are not re-created — their ads point back at the old
        # id only if it still exists; re-run the old ingest names otherwise. Restore from pg_dump for a full revert.
        missing = db.execute(text("SELECT COUNT(*) FROM bak_naming_products b WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = b.id)")).scalar()
        for (pid,) in db.execute(text("SELECT id FROM products WHERE id > :m"), {"m": max_pid}).all():
            drop_if_empty(db, pid)
        db.commit()
        print(f"rolled back · {missing} deleted bucket products not restorable here (use the pg_dump)")


# ------------------------------------------------------------ phases
def phase_ads(db, advs: dict, apply: bool) -> Counter:
    """Every ad: evidence name + ad type (rules only)."""
    st, n = Counter(), 0
    while True:
        ads = db.scalars(select(Ad).where(Ad.name_source.is_(None)).order_by(Ad.id).limit(1000)).all()
        if not ads:
            break
        for a in ads:
            d = _ad_input(a, advs.get(a.advertiser_id))
            nm = N.best(d)
            a.raw_product_name, a.name_source = nm["name"], nm["source"] or "none"
            a.ad_type = N.ad_type(d, nm)
            st[a.ad_type] += 1
            st["named" if nm["name"] else "no_evidence"] += 1
        n += len(ads)
        if not apply:
            break  # dry run: one page is enough to show the shape (the full count is in eval_naming.py coverage)
        db.commit()
        log.info("ads %d", n)
    return st


def _top_adv(ads: list[Ad], advs: dict) -> str | None:
    c = Counter(advs.get(a.advertiser_id) for a in ads if a.advertiser_id)
    return c.most_common(1)[0][0] if c else None


def recluster(db, p: Product, ads: list[Ad], advs: dict) -> set[int]:
    """Placeholder / bad name: every ad is resolved again on its own evidence; ads without any become UNKNOWN on their own."""
    unindex(p.id)
    p.canonical_name, p.name_status, p.name_source, p.name_confidence, p.name_vi, p.name_evidence = N.UNKNOWN_LABEL, "UNKNOWN", None, 0.0, None, []
    touched, kept = {p.id}, False
    for a in ads:
        adv = advs.get(a.advertiser_id)
        nm = N.best(_ad_input(a, adv))
        if not nm["name"] and not kept:
            kept = True  # the first unidentified ad keeps the old product (id, code, lifecycle)
            p.name_evidence = []
            continue
        t, _, _ = resolve_product(db, {"name": nm["name"] or "", "naming": nm, "advertiser": adv, "ad_id": a.id,
                                       "landing_url": a.landing_url, "fingerprint": a.creative_fingerprint, "copy": a.ad_text,
                                       "country": a.country, "currency": a.currency, "price": a.price,
                                       "category": E.classify_category(nm["name"] or "", a.ad_text) if nm["name"] else p.category},
                                  source="backfill")
        if t.id != p.id:
            move_ads(db, [a.id], t.id)
        touched.add(t.id)
    if drop_if_empty(db, p.id):
        touched.discard(p.id)
    return touched


def generic_mixed(name: str, ads: list[Ad], named: list) -> bool:
    """A one-word / modifier-only name ("Fengshui", "Bracelet") over ads of several shops or several evidenced products:
    a search-keyword bucket (old TikTok mapper named every ad after the query), not one product."""
    hits = lexicon.concepts_in(name or "")
    generic = len((name or "").split()) < 2 or (len(hits) == 1 and hits[0][0] == lexicon.fold(name))  # "Massage Gun" = a keyword
    return generic and (len({a.advertiser_id for a in ads} - {None}) >= 2 or len({n["name"].lower() for n, _ in named}) >= 2)


def classify_legacy(p: Product, ads: list[Ad], advs: dict) -> str:
    """A name from before this module: confirm it from its ads' evidence, correct it, or keep it as SUGGESTED."""
    cands = [(N.best(_ad_input(a, advs.get(a.advertiser_id))), a.id) for a in ads]
    named = [(nm, aid) for nm, aid in cands if nm["name"]]
    if generic_mixed(p.canonical_name, ads, named):
        return "weak"  # "Fengshui" is contained in every feng shui listing name: never a confirmation
    agree = sorted(((nm, aid) for nm, aid in named if N.overlap(p.canonical_name, nm["name"]) >= 0.5),
                   key=lambda x: -x[0]["confidence"])
    if agree:
        nm = agree[0][0]
        p.name_status, p.name_source, p.name_confidence = nm["status"], nm["source"], nm["confidence"]
        p.name_evidence = [{"source": n["source"], "value": n["name"], "ad_id": aid} for n, aid in agree[:6]]
        return "confirmed"
    strong = Counter(nm["name"] for nm, _ in named if nm["status"] == "AUTO_CONFIRMED")
    if strong and strong.most_common(1)[0][1] * 2 >= len(ads):  # the listing / product page says otherwise for most ads
        name = strong.most_common(1)[0][0]
        nm = next(n for n, _ in named if n["name"] == name)
        N._push_history(p, "backfill")
        N._rename(p, name)
        p.name_status, p.name_source, p.name_confidence = nm["status"], nm["source"], nm["confidence"]
        p.name_evidence = [{"source": n["source"], "value": n["name"], "ad_id": aid} for n, aid in named if n["name"] == name][:6]
        return "corrected"
    if not lexicon.has_product_noun(p.canonical_name):
        return "weak"  # 10 / 40 old names without a product noun named a product: re-derive like a bad name
    p.name_status, p.name_source, p.name_confidence = "SUGGESTED", "legacy", N.SOURCE_PRECISION["legacy"]
    p.name_evidence = [{"source": "legacy", "value": p.canonical_name}]
    return "legacy"


def phase_products(db, advs: dict, apply: bool) -> tuple[Counter, set[int], list[dict]]:
    st, touched, suspects = Counter(), set(), []
    last = 0
    while True:
        ps = db.scalars(select(Product).where(Product.name_status.is_(None), Product.id > last).order_by(Product.id).limit(200)).all()
        if not ps:
            break
        last = ps[-1].id
        by = defaultdict(list)
        for a in db.scalars(select(Ad).where(Ad.product_id.in_([p.id for p in ps]))):
            by[a.product_id].append(a)
        for p in ps:
            ads = by.get(p.id, [])
            if p.manual_override:
                p.name_status = p.name_status or "VERIFIED"
                continue
            try:
                with db.begin_nested():
                    if N.is_bad_product_name(p.canonical_name, _top_adv(ads, advs)):
                        st["placeholder" if N.PLACEHOLDER.search(p.canonical_name or "") else "bad_name"] += 1
                        if apply:
                            touched |= recluster(db, p, ads, advs)
                    elif (r := classify_legacy(p, ads, advs)) == "weak":
                        st["weak_legacy"] += 1
                        if apply:
                            touched |= recluster(db, p, ads, advs)
                    else:
                        st[r] += 1
                        if r == "corrected":
                            touched.add(p.id)
                        if len(ads) >= 3 and len({N.best(_ad_input(a, advs.get(a.advertiser_id)))["name"] for a in ads} - {None}) >= 3:
                            suspects.append({"id": p.id, "name": p.canonical_name, "ads": len(ads)})  # ≥3 different evidenced names
            except Exception as e:  # one bad product must not stop the run; it stays NULL → retried next run
                log.exception("product %s: %s", p.id, e)
                st["errors"] += 1
        if not apply:
            db.rollback()
            continue
        db.commit()
        log.info("products up to id %d · %s", last, dict(st))
    return st, touched, suspects


def regroup_unknown(db, apply: bool) -> tuple[Counter, set[int]]:
    """UNKNOWN ads that are evidently one product — same copy from the same page, same product page, same picture —
    join one product: the named one when the identity points at exactly one, else one UNKNOWN group (a 46-ad
    "Reliefwave Pro" cluster had become 46 singletons). Picture hashes shared by > 8 ads are stock frames: ignored."""
    from .models import Creative
    from .services.entity_resolution import TRIVIAL_PHASH, landing_key

    rows = db.execute(select(Ad.id, Ad.product_id, Ad.creative_fingerprint, Ad.variation_key, Ad.landing_url, Ad.ad_text,
                             Product.name_status).join(Product, Product.id == Ad.product_id)).all()
    ph: dict[int, str] = {}
    for aid, h in db.execute(select(Creative.ad_id, Creative.phash).where(Creative.phash.is_not(None), Creative.ad_id.is_not(None))
                             .order_by(Creative.ad_id, Creative.position)):
        ph.setdefault(aid, h)
    keys: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if len((r.ad_text or "").strip()) >= 20:
            if r.creative_fingerprint:
                keys["f:" + r.creative_fingerprint].append(r.id)
            if r.variation_key:
                keys["v:" + r.variation_key].append(r.id)
        if lk := landing_key(r.landing_url):
            keys["l:" + lk].append(r.id)
        if (h := ph.get(r.id)) and h not in TRIVIAL_PHASH:
            keys["p:" + h].append(r.id)
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    for k, ids in keys.items():
        if len(ids) < 2 or (k.startswith("p:") and len(ids) > 8):
            continue
        for i in ids[1:]:
            a, b = find(ids[0]), find(i)
            if a != b:
                parent[max(a, b)] = min(a, b)
    comps: dict[int, list] = defaultdict(list)
    for r in rows:
        comps[find(r.id)].append(r)
    old_name = dict(db.execute(text("SELECT a.id, p.canonical_name FROM bak_naming_ads a JOIN bak_naming_products p ON p.id = a.product_id")).all()) \
        if "bak_naming_ads" in inspect(db.connection()).get_table_names() else {}
    st, touched = Counter(), set()
    for comp in comps.values():
        unknown = [r for r in comp if r.name_status == "UNKNOWN"]
        if not unknown or len(comp) < 2:
            continue
        named = {r.product_id for r in comp if r.name_status != "UNKNOWN"}
        joined = len(named) == 1
        target = next(iter(named)) if joined else min(r.product_id for r in unknown)
        movers = [r.id for r in unknown if r.product_id != target]
        if not movers:
            continue
        st["joined_named" if joined else "grouped_unknown"] += 1
        st["ads_moved"] += len(movers)
        if not apply:
            continue
        olds = move_ads(db, movers, target)
        for pid in olds:
            drop_if_empty(db, pid)
        touched.add(target)
        p = db.get(Product, target)
        hints = {old_name.get(r.id) for r in unknown} - {None}
        if p.name_status == "UNKNOWN" and len(hints) == 1 and not N.is_bad_product_name(h := hints.pop()):
            p.name_evidence = [{"source": "legacy_hint", "value": h}]  # shown as a hint to confirm, never as the name
    if apply:
        db.commit()
    return st, touched


def snapshot(db) -> dict:
    """Before / after numbers for the report."""
    q = lambda s: db.execute(text(s)).scalar()
    return {
        "products": q("select count(*) from products"), "ads": q("select count(*) from ads"),
        "ads_without_product": q("select count(*) from ads where product_id is null"),
        "placeholder_names": q("select count(*) from products where canonical_name ilike '%chưa đặt tên%' or canonical_name ilike 'unknown%' or canonical_name ilike '% creative'"),
        "status": dict(db.execute(text("select coalesce(name_status,'NULL'), count(*) from products group by 1")).all()),
        "unknown_products_in_target": q("select count(*) from products where name_status='UNKNOWN' and in_target"),
        "with_name_vi": q("select count(*) from products where name_vi is not null"),
        "ad_types": dict(db.execute(text("select coalesce(ad_type,'NULL'), count(*) from ads group by 1")).all()),
        "decisions": dict(db.execute(text("select coalesce(recommendation,'NULL'), count(*) from products group by 1")).all()),
        "avg_opportunity_in_target": q("select round(avg(opportunity_score)::numeric, 2) from products where in_target"),
    }


def run(apply: bool) -> None:
    t0 = time.time()
    auto_migrate()
    if apply:
        backup()
    with SessionLocal() as db:
        before = snapshot(db)
        advs = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
        st_ads = phase_ads(db, advs, apply)
        st_p, touched, suspects = phase_products(db, advs, apply)
        st_g, t2 = regroup_unknown(db, apply)
        touched |= t2
        st_p.update({f"regroup_{k}": v for k, v in st_g.items()})
        if apply and touched:
            from .services.engine import rescore_products

            log.info("rescoring %d products", len(touched))
            ids = sorted(touched)
            for i in range(0, len(ids), 200):
                rescore_products(db, ids[i:i + 200])
                db.commit()
        after = snapshot(db) if apply else None
    import json

    out = {"apply": apply, "seconds": round(time.time() - t0, 1), "ads": dict(st_ads), "products": dict(st_p),
           "rescored": len(touched), "suspect_clusters": len(suspects), "suspects_top": sorted(suspects, key=lambda s: -s["ads"])[:30],
           "before": before, "after": after}
    from pathlib import Path

    rep = Path(__file__).resolve().parents[1] / "data" / f"backfill_names_{'apply' if apply else 'dry'}.json"
    rep.parent.mkdir(exist_ok=True)
    rep.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "suspects_top"}, ensure_ascii=False, indent=1, default=str))
    print("report →", rep)


def shop_step(apply: bool) -> None:
    """Shop-based fallback: verified inheritance / grouping, then provisional "Sản phẩm từ <shop>" labels (shop_naming.py)."""
    import json

    from .services import shop_naming
    from .services.engine import rescore_products

    auto_migrate()
    with SessionLocal() as db:
        if apply and "bak_shop_products" not in inspect(db.connection()).get_table_names():
            db.execute(text("CREATE TABLE bak_shop_products AS SELECT id, canonical_name, name_status, shop_name, name_evidence FROM products"))
            db.execute(text("CREATE TABLE bak_shop_ads AS SELECT id, product_id FROM ads"))
            db.commit()
        before = snapshot(db)
        p = shop_naming.plan(db)
        print({"inherit": len(p["inherit"]), "ambiguous_left_alone": p["ambiguous"], "unknown_groups": len(p["groups"]),
               "unknown_in_groups": sum(len(g) for g in p["groups"])})
        res = {}
        if apply:
            res = shop_naming.apply(db, p)
            db.commit()
        labelled = shop_naming.refresh(db)
        after = snapshot(db)
        after["provisional_shop_names"] = db.execute(text("select count(*) from products where name_status='UNKNOWN' and shop_name is not null")).scalar()
        after["unknown_no_label"] = db.execute(text("select count(*) from products where name_status='UNKNOWN' and shop_name is null")).scalar()
        if apply:
            db.commit()
            ids = res.get("touched", [])
            for i in range(0, len(ids), 200):
                rescore_products(db, ids[i:i + 200])
                db.commit()
        else:
            db.rollback()
    out = {"apply": apply, "plan_inherit": len(p["inherit"]), "result": res, "labelled": labelled,
           "before": {k: before[k] for k in ("products", "ads", "status", "decisions")}, "after": {k: after[k] for k in ("products", "ads", "status", "decisions", "provisional_shop_names", "unknown_no_label")}}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    from pathlib import Path

    (Path(__file__).resolve().parents[1] / "data" / f"shop_names_{'apply' if apply else 'dry'}.json").write_text(
        json.dumps({**out, "inherit_sample": list(p["inherit"].items())[:40]}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def model_pass(kind: str, n: int) -> None:
    from . import naming_jobs

    done = 0
    with SessionLocal() as db:
        while done < n:
            if kind == "llm":
                from . import naming_ai

                naming_ai.rule_pass(db)
                r = naming_ai.ai_pass(db, batches=1)
            else:
                r = naming_jobs.translate(db)
            db.commit()
            if ids := r.get("product_ids"):
                from .services.engine import rescore_products

                rescore_products(db, ids)
                db.commit()
            print(r)
            step = r.get("asked", 0)
            if not step or r.get("skipped"):
                break
            done += step


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("backfill_names.log", encoding="utf-8")])
    a = sys.argv[1:]
    if "--rollback" in a:
        rollback()
    elif "--shop" in a:
        shop_step("--apply" in a)
    elif "--recheck-generic" in a:  # one-off for runs made before generic_mixed(): re-derive keyword buckets
        with SessionLocal() as db:
            advs = dict(db.execute(select(Advertiser.id, Advertiser.name)).all())
            ps = db.scalars(select(Product).where(Product.name_status.in_(["AUTO_CONFIRMED", "SUGGESTED"]),
                                                  or_(Product.manual_override.is_(None), Product.manual_override.is_(False)))).all()
            by = defaultdict(list)
            for ad in db.scalars(select(Ad).where(Ad.product_id.in_([p.id for p in ps]))):
                by[ad.product_id].append(ad)
            hits = []
            for p in ps:
                ads = by.get(p.id, [])
                named = [(n, x.id) for x in ads if (n := N.best(_ad_input(x, advs.get(x.advertiser_id))))["name"]]
                if generic_mixed(p.canonical_name, ads, named):
                    hits.append((p, ads))
            print(f"{len(hits)} generic mixed clusters, {sum(len(x) for _, x in hits)} ads:", [(p.canonical_name, len(x)) for p, x in hits[:15]])
            touched = set()
            if "--apply" in a:
                for p, ads in hits:
                    touched |= recluster(db, p, ads, advs)
                db.commit()
                st, t2 = regroup_unknown(db, True)
                touched |= t2
                print("regroup", dict(st))
                from .services.engine import rescore_products

                ids = sorted(touched)
                for i in range(0, len(ids), 200):
                    rescore_products(db, ids[i:i + 200])
                    db.commit()
                print("rescored", len(ids), snapshot(db))
    elif "--regroup" in a:
        with SessionLocal() as db:
            st, touched = regroup_unknown(db, "--apply" in a)
            print(dict(st))
            if "--apply" in a:
                from .services.engine import rescore_products

                ids = sorted(touched)
                for i in range(0, len(ids), 200):
                    rescore_products(db, ids[i:i + 200])
                    db.commit()
                print("rescored", len(ids), snapshot(db))
    elif "--llm" in a or "--translate" in a:
        k = "llm" if "--llm" in a else "translate"
        model_pass(k, int(a[a.index(f"--{k}") + 1]) if len(a) > a.index(f"--{k}") + 1 else 100)
    else:
        run("--apply" in a)
