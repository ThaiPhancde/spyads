"""Phase 7: personalized modes and filters for GET /api/search (routers/intel.search).

Glue only — the scores come from Phase 5 (recommend), the look-alikes from Phase 6 (similar), ownership from Phase 2
(allocation locks). Nothing here writes: search never reserves or claims a product.
"""
import time
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import recommend as R
from .models import Ad

MODES = ("for_me", "claimable", "followed", "similar")
FIT_CAP = 1500  # ponytail: personal fit for at most 1500 products per query (by opportunity); add batch fit if lists grow


@dataclass
class Personal:
    mode: str
    restrict: list | None = None       # product ids the mode allows (None = no restriction)
    advertisers: list | None = None    # ads must come from these advertisers (followed mode)
    scores: dict = field(default_factory=dict)  # precomputed fit / similarity by product id
    info: dict = field(default_factory=dict)


def candidates(db, uid, mode, similar_to=None):
    if mode not in MODES:
        raise HTTPException(422, f"mode phải là một trong: {', '.join(MODES)}")
    if mode == "for_me":
        feed = R.feed(db, uid, limit=200)
        scores = {r["product_id"]: {"score": r["score"], "reasons": r["reasons"], "coverage": r["coverage"], "explore": r["explore"],
                                    **({"score_before_interest": r["score_before_interest"]} if "score_before_interest" in r else {})}
                  for r in feed["rows"]}
        return Personal(mode, restrict=list(scores), scores=scores,
                        info={"cold_start": feed["cold_start"], "cached": feed["cached"], "candidates": feed["total"]})
    if mode == "followed":
        from .workspace.models import Follow
        ids = list(db.scalars(select(Follow.advertiser_id).where(Follow.user_id == uid)))
        return Personal(mode, advertisers=ids, info={"followed_advertisers": len(ids)})
    if mode == "similar":
        from . import similar as S
        if similar_to:
            res = S.similar_products(db, similar_to, uid, 100, exclusive=True)
            if res is None:
                raise HTTPException(404, "Không tìm thấy sản phẩm gốc")
            rows, info = res["similar"], {"similar_to": res["product_id"], "duplicates_hidden": len(res["duplicates"])}
        else:
            res = S.for_me(db, uid, 100)
            rows, info = res["rows"], {"similar_to": None, "seeds": res["seeds"]}
        scores = {r["product_id"]: {"similarity": r["score"], "similar_reasons": r["reasons"],
                                    "because_of": r.get("because_of")} for r in rows}
        return Personal(mode, restrict=list(scores), scores=scores, info=info)
    return Personal(mode)  # claimable: everything still available


def filter_signals(db, products, min_demand=None, min_engagement=None):
    """Real-data filters only: demand from stored ad facts; engagement only where the source reported likes/comments/shares."""
    if min_demand is not None:
        products = [p for p in products if ((p.potential or {}).get("vector") or {}).get("market_demand", 0) >= min_demand]
    if min_engagement is not None and products:
        real = set(db.scalars(select(Ad.product_id).where(
            Ad.product_id.in_([p.id for p in products]), Ad.is_active.is_(True),
            or_(Ad.likes.is_not(None), Ad.comments_count.is_not(None), Ad.shares.is_not(None))).distinct()))
        products = [p for p in products if p.id in real and ((p.features or {}).get("engagement_per_ad") or 0) >= min_engagement]
    return products


_FIT: dict = {}  # (uid, signature) → (time, {pid: fit}) — pages of the same query reuse the scores


def fit_scores(db, uid, products):
    ctx = R.context(db, uid)
    key = (uid, ctx["signature"])
    hit = _FIT.get(key)
    if not hit or time.time() - hit[0] > 600:
        hit = _FIT[key] = (time.time(), {})
        for k in [k for k in _FIT if k[0] == uid and k != key]:
            _FIT.pop(k, None)
    memo = hit[1]
    todo = sorted((p for p in products if p.id not in memo), key=lambda p: -(p.opportunity_score or 0))[:FIT_CAP]
    if todo:
        pos = [s for s, w in ctx["seeds"].items() if w > 0]
        vecs = R.vectors(db, [p.id for p in todo] + pos) if pos else {}
        hits = R.followed_hits(db, ctx, [p.id for p in todo])
        for p in todo:
            score, cov, _, reasons = R.score_product(ctx, p, vecs, p.id in hits)
            memo[p.id] = {"score": score, "coverage": cov, "reasons": reasons}
    return {p.id: memo[p.id] for p in products if p.id in memo}


def ownership(db, uid, pids):
    """Live locks → per product: available / mine / taken, and for "taken" the other MKT's state (never who)."""
    from .allocation import AllocationLock, ProductAllocation
    # the lock table holds only live allocations (small): read it whole instead of an IN over thousands of product ids
    rows = {pid: (owner, st) for pid, owner, st in db.execute(
        select(AllocationLock.product_id, ProductAllocation.user_id, ProductAllocation.status)
        .join(ProductAllocation, ProductAllocation.id == AllocationLock.allocation_id))}
    alloc, state = {}, {}
    for pid in pids:
        owner, st = rows.get(pid, (None, None))
        alloc[pid] = "available" if owner is None else "mine" if owner == uid else "taken"
        if alloc[pid] == "taken":
            state[pid] = st
    return alloc, state


def personal_fit(db, uid, products):
    """Personal fit with the soft-dedup penalty for other MKTs' interest applied (app/interest.py)."""
    from . import interest as I
    seen = I.others(db, uid)
    return {pid: I.apply(f, seen.get(pid)) for pid, f in fit_scores(db, uid, products).items()}


def annotate(db, uid, products, personal=None, need_fit=False):
    """(fit, allocation, extra) by product. Allocation = available / mine / taken from live locks; extra carries the
    other-MKT signals kept apart on purpose: `interest` (Saved / Favorite elsewhere) vs `taken_state` (claimed / testing)."""
    if not uid or not products:
        return {}, {}, {}
    from . import interest as I
    alloc, state = ownership(db, uid, [p.id for p in products])
    seen = I.others(db, uid)
    extra = {p.id: {"interest": I.label(seen.get(p.id)), "taken_state": state.get(p.id)} for p in products}
    fit = {}
    if need_fit:
        pre = personal.scores if personal is not None else {}
        if personal is not None and personal.mode == "for_me":
            fit = {pid: s for pid, s in pre.items()}  # the feed already applied the interest penalty
        else:
            fit = {pid: I.apply(f, seen.get(pid)) for pid, f in fit_scores(db, uid, products).items()}
            for pid, more in pre.items():  # similar mode: keep the look-alike score next to the personal fit
                if pid in fit:
                    fit[pid] = {**fit[pid], **more}
    return fit, alloc, extra
