"""Phase 8: personalized Library — one MKT's products and ads, assembled on read.

Nothing is copied: the Library is a per-user *view* joining the MKT's own rows (mkt_ad_interactions, product
allocations, test results, follows) onto the shared Ads / Products. Products are shown per cluster root; an MKT only
ever sees their own interactions, allocation and results — another MKT's ownership shows as "taken", nothing more.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from . import allocation as A, behavior
from . import recommend as R
from .markets import product_in_scope
from .models import Ad, Advertiser, Creative, Product

SECTIONS = ("my", "saved", "favorite", "testing", "tested", "similar", "followed")
TEST_FILTERS = ("testing", "tested", "winner", "loser", "inconclusive", "untested")


def state(db, uid):
    """root product id → the MKT's own facts about it (ads, allocation, latest result, last activity)."""
    from .workspace.models import AdInteraction
    red = R.redirects(db)
    out = {}

    def entry(pid):
        return out.setdefault(R.root(red, pid), {"ads": [], "allocation": None, "result": None, "at": datetime.min})

    for ia, ad in db.execute(select(AdInteraction, Ad).join(Ad, Ad.id == AdInteraction.ad_id).where(
            AdInteraction.user_id == uid, Ad.product_id.is_not(None))):
        if not (ia.saved or ia.favorite or ia.tested or (ia.note or "").strip()):
            continue
        e = entry(ad.product_id)
        e["ads"].append({"id": ad.id, "title": ad.title, "text": ad.ad_text, "advertiser_id": ad.advertiser_id,
                         "snapshot_url": ad.snapshot_url, "saved": ia.saved, "favorite": ia.favorite, "tested": ia.tested,
                         "note": ia.note, "updated_at": ia.updated_at})
        e["at"] = max(e["at"], ia.updated_at or datetime.min)
    for a in db.scalars(select(A.ProductAllocation).where(A.ProductAllocation.user_id == uid,
                                                          A.ProductAllocation.status.in_(A.ACTIVE))):
        e = entry(a.product_id)
        e["allocation"] = {"id": a.id, "status": a.status, "reserved_until": a.reserved_until, "scope": a.scope}
        e["at"] = max(e["at"], a.updated_at or datetime.min)
    for r, pid in db.execute(select(A.ProductTestResult, A.ProductAllocation.product_id)
                             .join(A.ProductAllocation, A.ProductAllocation.id == A.ProductTestResult.allocation_id)
                             .where(A.ProductTestResult.user_id == uid).order_by(A.ProductTestResult.id)):
        e = entry(pid)  # latest result wins (ordered by id)
        e["result"] = {"id": r.id, "outcome": r.outcome, "market": r.market, "spend": r.spend, "orders": r.orders,
                       "revenue": r.revenue, "note": r.note, "created_at": r.created_at}
        e["at"] = max(e["at"], r.created_at or datetime.min)
    for e in out.values():
        alloc = e["allocation"]
        if alloc and alloc["status"] == "testing":
            e["test_status"] = "testing"
        elif e["result"] and (not alloc or alloc["status"] == "tested"):
            e["test_status"] = "tested"
        elif any(x["tested"] for x in e["ads"]):
            e["test_status"] = "testing"  # marked "Đã test" on an ad, no result recorded yet
        else:
            e["test_status"] = None
    return out


def in_section(e, section):
    return {"my": True, "saved": any(x["saved"] for x in e["ads"]), "favorite": any(x["favorite"] for x in e["ads"]),
            "testing": e["test_status"] == "testing", "tested": e["test_status"] == "tested"}[section]


def counts(st):
    return {s: sum(1 for e in st.values() if in_section(e, s)) for s in ("my", "saved", "favorite", "testing", "tested")}


def _test_ok(e, want):
    if not want:
        return True
    status = e.get("test_status") if e else None
    if want == "untested":
        return status is None
    if want in ("testing", "tested"):
        return status == want
    return bool(e and e.get("result") and e["result"]["outcome"] == want)


def library(db, uid, section="my", category=None, market=None, product_type=None, test_status=None, advertiser_id=None,
            min_fit=None, limit=24, offset=0):
    from . import personal_search as PS
    from . import similar as S
    from .routers.intel import covers, product_card
    if section not in SECTIONS:
        raise HTTPException(422, f"section phải là một trong: {', '.join(SECTIONS)}")
    if test_status and test_status not in TEST_FILTERS:
        raise HTTPException(422, f"test_status phải là một trong: {', '.join(TEST_FILTERS)}")
    st = state(db, uid)
    extra, advertisers = {}, None
    if section == "similar":
        rows = S.for_me(db, uid, 100)["rows"]
        order = [r["product_id"] for r in rows]
        extra = {r["product_id"]: {"similarity": r["score"], "similar_reasons": r["reasons"], "because_of": r.get("because_of")} for r in rows}
    elif section == "followed":
        res = S.from_followed(db, uid, 200)
        order = [r["product_id"] for r in res["rows"]]
        extra = {r["product_id"]: {"followed_advertiser": r["advertiser"], "followed_advertiser_id": r["advertiser_id"]} for r in res["rows"]}
        advertisers = followed_advertisers(db, uid)
    else:
        order = [pid for pid, e in sorted(st.items(), key=lambda kv: kv[1]["at"], reverse=True) if in_section(e, section)]
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(order)))} if order else {}
    adv_hit = set(db.scalars(select(Ad.product_id).where(Ad.product_id.in_(order), Ad.advertiser_id == advertiser_id))) if advertiser_id and order else None
    keep = []
    for pid in order:
        p, e = prods.get(pid), st.get(pid)
        if p is None or (category and p.category != category) or (product_type and p.subcategory != product_type):
            continue
        if market and not product_in_scope(p, market):
            continue
        if adv_hit is not None and pid not in adv_hit and not any(x["advertiser_id"] == advertiser_id for x in (e or {}).get("ads", [])):
            continue
        if not _test_ok(e, test_status):
            continue
        keep.append(p)
    fit = PS.personal_fit(db, uid, keep) if keep else {}
    if min_fit is not None:
        keep = [p for p in keep if fit.get(p.id, {}).get("score", 0) >= min_fit]
    page = keep[offset:offset + limit]
    cov = covers(db, page)
    from . import interest as I
    owned, taken_state = PS.ownership(db, uid, [p.id for p in page])
    seen = I.others(db, uid)
    rows = []
    for p in page:
        e = st.get(p.id) or {"ads": [], "allocation": None, "result": None, "test_status": None}
        rows.append({**product_card(p, cov.get(p.id)), "subcategory": p.subcategory,
                     "fit": {**fit.get(p.id, {}), **extra.get(p.id, {})} if fit.get(p.id) or extra.get(p.id) else None,
                     "ownership": owned[p.id], "taken_state": taken_state.get(p.id),
                     "interest": I.label(seen.get(p.id)),  # other MKTs' Save / Favorite — never who
                     "library": {"ads": [{k: v for k, v in x.items() if k != "updated_at"} for x in e["ads"]],
                                 "saved": any(x["saved"] for x in e["ads"]), "favorite": any(x["favorite"] for x in e["ads"]),
                                 "allocation": e["allocation"], "result": e["result"], "test_status": e["test_status"]}})
    out = {"section": section, "total": len(keep), "rows": rows, "counts": counts(st)}
    if advertisers is not None:
        out["advertisers"] = advertisers
    return out


def followed_advertisers(db, uid):
    from sqlalchemy import func
    from .workspace.models import Follow
    rows = db.execute(select(Follow, Advertiser).join(Advertiser, Advertiser.id == Follow.advertiser_id)
                      .where(Follow.user_id == uid).order_by(Follow.followed_at.desc())).all()
    n = dict(db.execute(select(Ad.advertiser_id, func.count(func.distinct(Ad.product_id)))
                        .where(Ad.advertiser_id.in_([a.id for _, a in rows])).group_by(Ad.advertiser_id)).all()) if rows else {}
    return [{"id": a.id, "name": a.name, "platform": a.platform, "country": a.country, "followed_at": f.followed_at,
             "interval_hours": f.interval_hours, "products": n.get(a.id, 0)} for f, a in rows]


# ---------------- actions ----------------

def _cluster_ads(db, red, root):
    members = [p for p in red if R.root(red, p) == root] + [root]
    return members


def representative_ad(db, uid, root):
    """The ad a product-level Save / Favorite lands on: one the MKT already touched, else the cover's ad, else the newest."""
    from .workspace.models import AdInteraction
    red = R.redirects(db)
    members = _cluster_ads(db, red, root)
    mine = db.scalar(select(AdInteraction.ad_id).join(Ad, Ad.id == AdInteraction.ad_id)
                     .where(AdInteraction.user_id == uid, Ad.product_id.in_(members)).order_by(AdInteraction.updated_at.desc()))
    if mine:
        return mine
    p = db.get(Product, root)
    if p and p.cover_creative_id:
        aid = db.scalar(select(Creative.ad_id).where(Creative.id == p.cover_creative_id))
        if aid and db.scalar(select(Ad.product_id).where(Ad.id == aid)) in members:
            return aid
    aid = db.scalar(select(Ad.id).where(Ad.product_id.in_(members)).order_by(Ad.is_active.desc(), Ad.id.desc()))
    if aid is None:
        raise HTTPException(404, "Sản phẩm chưa có Ads để lưu")
    return aid


def toggle(db, uid, pid, field, on):
    """Product-level Save / Favorite. On = flag one ad; off = clear the flag on every ad of this product the MKT holds."""
    from .workspace.models import AdInteraction
    from .workspace.routes import set_interaction
    red = R.redirects(db)
    root = R.root(red, pid)
    if not db.get(Product, root):
        raise HTTPException(404, "Không tìm thấy sản phẩm")
    if on:
        set_interaction(db, uid, representative_ad(db, uid, root), **{field: True})
    else:
        members = _cluster_ads(db, red, root)
        for aid in db.scalars(select(AdInteraction.ad_id).join(Ad, Ad.id == AdInteraction.ad_id).where(
                AdInteraction.user_id == uid, Ad.product_id.in_(members), getattr(AdInteraction, field).is_(True))):
            set_interaction(db, uid, aid, **{field: False})
    return root


def start_test(db, uid, pid):
    """Claim if needed (409 if another MKT owns it) and move the allocation to `testing` — one action in the Library."""
    root = R.root(R.redirects(db), pid)
    a = A.claim(db, uid, root)
    if a.status != "testing":
        A.set_status(db, A.owned(db, a.id, {"id": uid, "role": "MKT"}), uid, "testing")
        behavior.track(db, uid, "test_start", product_id=a.product_id, allocation_id=a.id)
    return a


# ---------------- personal dashboard (Phase 9) ----------------

def _mini(p, extra=None):
    from .services import naming as N
    return {"id": p.id, "name": N.display_name(p), "category": p.category, "country": p.country,
            "recommendation": p.recommendation, "opportunity": p.opportunity_score, "cover_creative_id": p.cover_creative_id,
            **(extra or {})}


def dashboard(db, uid, now=None):
    """Everything on the MKT's personal dashboard in a fixed number of queries. Product numbers count Product Clusters
    (roots), Ads numbers count ads — never mixed. Recommendation labels stay TEST / TEST_NOW (no Win / Rare Winner)."""
    from datetime import timedelta
    from sqlalchemy import func
    from . import preferences as PF
    from . import similar as S
    from .workspace.filters import ad_filter
    from .workspace.models import AdInteraction, Follow
    now = now or datetime.utcnow()
    st = state(db, uid)
    roots = {pid: e for pid, e in st.items()}
    by_kind = {k: [pid for pid, e in sorted(roots.items(), key=lambda kv: kv[1]["at"], reverse=True) if f(e)] for k, f in {
        "saved": lambda e: any(x["saved"] for x in e["ads"]), "favorite": lambda e: any(x["favorite"] for x in e["ads"]),
        "claimed": lambda e: bool(e["allocation"]) and e["allocation"]["status"] in ("reserved", "claimed"),
        "testing": lambda e: e["test_status"] == "testing",
        "test_success": lambda e: bool(e["result"]) and e["result"]["outcome"] == "winner",
        "test_fail": lambda e: bool(e["result"]) and e["result"]["outcome"] == "loser",
        "test_inconclusive": lambda e: bool(e["result"]) and e["result"]["outcome"] == "inconclusive"}.items()}
    ad_counts = dict(zip(("saved", "favorite", "tested"), db.execute(select(
        func.count().filter(AdInteraction.saved.is_(True)), func.count().filter(AdInteraction.favorite.is_(True)),
        func.count().filter(AdInteraction.tested.is_(True))).where(AdInteraction.user_id == uid)).one()))
    follows = select(Follow.advertiser_id).where(Follow.user_id == uid)
    fresh = select(Ad).where(Ad.advertiser_id.in_(follows), ad_filter(db))
    new_7d = db.scalar(select(func.count()).select_from(fresh.where(Ad.first_seen_at >= now - timedelta(days=7)).subquery()))
    new_24h = db.scalar(select(func.count()).select_from(fresh.where(Ad.first_seen_at >= now - timedelta(hours=24)).subquery()))
    new_ads = db.scalars(fresh.order_by(Ad.first_seen_at.desc().nullslast(), Ad.id.desc()).limit(10)).all()
    feed = R.feed(db, uid, limit=6)
    rec_ids = [r["product_id"] for r in feed["rows"]]
    rec_ads = db.scalars(select(Ad).where(Ad.product_id.in_(rec_ids), Ad.is_active.is_(True), ad_filter(db))
                         .order_by(Ad.first_seen_at.desc().nullslast()).limit(30)).all() if rec_ids else []
    seen, recommended_ads = set(), []
    for a in rec_ads:  # one newest live ad per recommended product
        if a.product_id not in seen:
            seen.add(a.product_id)
            recommended_ads.append(a)
    sim = S.for_me(db, uid, limit=6, per_seed=6, max_seeds=3)
    want = set(rec_ids) | {r["product_id"] for r in sim["rows"]} | {p for ids in by_kind.values() for p in ids[:5]}
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(want)))} if want else {}
    names = dict(db.execute(select(Advertiser.id, Advertiser.name).where(
        Advertiser.id.in_({a.advertiser_id for a in new_ads + recommended_ads if a.advertiser_id}))).all())
    prof = PF.view(db, uid)
    ad_row = lambda a: {"id": a.id, "title": a.title, "text": (a.ad_text or "")[:200], "product_id": a.product_id,
                        "advertiser_id": a.advertiser_id, "advertiser": names.get(a.advertiser_id), "country": a.country,
                        "first_seen_at": a.first_seen_at, "snapshot_url": a.snapshot_url, "active": a.is_active}
    lists = {k: [_mini(prods[p], {"result": roots[p]["result"], "allocation": roots[p]["allocation"]}) for p in ids[:5] if p in prods]
             for k, ids in by_kind.items()}
    return {
        "kpis": {
            "products": {k: len(v) for k, v in by_kind.items()} | {
                "recommended": feed["total"],
                "test_candidates": sum(1 for p in rec_ids if p in prods and prods[p].recommendation in ("TEST", "TEST_NOW"))},
            "ads": {"saved": ad_counts["saved"], "favorite": ad_counts["favorite"], "marked_tested": ad_counts["tested"],
                    "new_from_followed_24h": new_24h, "new_from_followed_7d": new_7d},
            "followed_advertisers": db.scalar(select(func.count()).select_from(Follow).where(Follow.user_id == uid)),
        },
        "recommended": [_mini(prods[r["product_id"]], {"score": r["score"], "reasons": r["reasons"][:3], "explore": r["explore"]})
                        for r in feed["rows"] if r["product_id"] in prods],
        "recommended_ads": [ad_row(a) for a in recommended_ads],
        "lists": lists,
        "followed": followed_advertisers(db, uid)[:10],
        "new_ads": [ad_row(a) for a in new_ads],
        "top_categories": (prof["learned"].get("categories") or [])[:5],
        "manual_categories": prof["manual"].get("categories") or [],
        "similar": [_mini(prods[r["product_id"]], {"score": r["score"], "because_of": r.get("because_of"), "reasons": r["reasons"][:2]})
                    for r in sim["rows"] if r["product_id"] in prods],
        "cold_start": feed["cold_start"], "is_default_profile": prof["is_default"], "computed_at": now,
    }
