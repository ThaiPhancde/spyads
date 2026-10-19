"""Phase 5: personalized fit score (0–100) between one MKT and each Product Cluster root.

Initial weights: category 25, saved/favorite/test similarity 25, semantic 15, market/language 15, verified ads 10,
freshness 10. Missing data is handled by side: a component the MKT has no data for yet (no categories, no seeds, no
follows — cold start) is dropped and the remaining weights renormalised, identically for every product; a component the
*product* lacks data for scores nothing (unknown is never rewarded) and lowers `coverage`. Only stored facts are used —
no engagement estimate is ever invented. Batch-scored by the worker into `mkt_recommendations`; reads filter live ownership on top of the cache.
"""
import asyncio
import hashlib
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, delete, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from . import preferences as PF
from .allocation import AllocationLock, ProductAllocation, ProductTestResult
from .behavior import BehaviorEvent
from .cluster_models import ProductCluster
from .db import Base, SessionLocal
from .models import Ad, Creative, Product

WEIGHTS = {"category": 0.25, "similarity": 0.25, "semantic": 0.15, "market": 0.15, "verified": 0.10, "freshness": 0.10}
LABEL = {"category": "Danh mục quan tâm", "similarity": "Giống sản phẩm đã lưu / yêu thích / test", "semantic": "Tương đồng ngữ nghĩa / hình ảnh",
         "market": "Thị trường / ngôn ngữ", "verified": "Tín hiệu Ads thật", "freshness": "Độ mới"}
VERSION = "reco-v1"


def env(name, default):
    return float(os.getenv(name, default))


class Recommendation(Base):
    __tablename__ = "mkt_recommendations"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # cluster root; no FK (legacy merges delete ids)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    explore: Mapped[bool] = mapped_column(Boolean, default=False)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationState(Base):
    __tablename__ = "mkt_recommendation_state"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    signature: Mapped[str] = mapped_column(String(64))  # profile + seeds fingerprint at compute time
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    cold_start: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(16), default=VERSION)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------- context: everything about the MKT, loaded once per scoring run ----------------

def redirects(db):
    return dict(db.execute(select(ProductCluster.id, ProductCluster.merged_into_id)
                           .where(ProductCluster.merged_into_id.is_not(None))).all())


def root(red, pid):
    seen = set()
    while pid in red and pid not in seen:
        seen.add(pid)
        pid = red[pid]
    return pid


def tokens(text):
    return {t for t in re.findall(r"[^\W\d_]{3,}", (text or "").lower())}


def context(db, uid):
    from .workspace.models import AdInteraction
    prof = PF.view(db, uid)
    red = redirects(db)
    learned, eff = prof["learned"], prof["effective"]
    cats = {x["key"]: x["score"] for x in learned.get("categories") or []}
    cats.update({c: 1.0 for c in prof["manual"].get("categories") or []})  # MKT's own picks are full interest
    for c in eff["excluded_categories"]:
        cats.pop(c, None)
    markets = {x["key"]: x["score"] for x in learned.get("markets") or []}
    for i, m in enumerate(prof["manual"].get("priority_markets") or (eff["markets"] if not markets else [])):
        markets[m] = max(markets.get(m, 0), max(0.4, 1 - 0.15 * i))  # manual / default order: 1, .85, .7 …
    seeds = {}  # root product → weight (negative = failed test)
    for ad_pid, saved, fav, tested in db.execute(
            select(Ad.product_id, AdInteraction.saved, AdInteraction.favorite, AdInteraction.tested)
            .join(Ad, Ad.id == AdInteraction.ad_id).where(AdInteraction.user_id == uid, Ad.product_id.is_not(None))):
        w = max(1.0 if fav else 0, 0.8 if saved else 0, 0.5 if tested else 0)
        if w:
            r = root(red, ad_pid)
            seeds[r] = max(seeds.get(r, 0), w)
    for pid, outcome in db.execute(select(ProductAllocation.product_id, ProductTestResult.outcome)
                                   .join(ProductAllocation, ProductAllocation.id == ProductTestResult.allocation_id)
                                   .where(ProductTestResult.user_id == uid).order_by(ProductTestResult.id)):
        r = root(red, pid)
        # latest outcome wins; inconclusive is interest, never success
        seeds[r] = {"winner": 1.2, "loser": -1.0, "inconclusive": 0.5}[outcome]
    not_interested = {root(red, p) for p in db.scalars(select(BehaviorEvent.product_id).where(
        BehaviorEvent.user_id == uid, BehaviorEvent.kind == "ad_not_interested", BehaviorEvent.product_id.is_not(None)))}
    mine = {root(red, p) for p in db.scalars(select(AllocationLock.product_id).join(
        ProductAllocation, ProductAllocation.id == AllocationLock.allocation_id).where(ProductAllocation.user_id == uid))}
    followed = set(eff["advertisers"])
    seed_rows = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(seeds))))} if seeds else {}
    sig = hashlib.sha1(repr((sorted(cats.items()), sorted(markets.items()), sorted(seeds.items()), sorted(followed),
                             sorted(not_interested), eff["languages"], eff["excluded_categories"], VERSION)).encode()).hexdigest()
    return {"uid": uid, "profile": prof, "red": red, "categories": cats, "markets": markets,
            "languages": set(eff["languages"]), "excluded": set(eff["excluded_categories"]), "seeds": seeds,
            "seed_rows": seed_rows, "followed": followed, "not_interested": not_interested, "mine": mine,
            "cold_start": not cats and not seeds and not followed, "signature": sig}


# ---------------- candidates, vectors ----------------

def pool(db, ctx):
    """Bounded candidate set: top global opportunities + favourite categories + followed advertisers' products."""
    base = select(Product.id).where(Product.in_target.is_(True), Product.is_demo.is_not(True),
                                    Product.shop_service.is_not(True), Product.name_status != "UNKNOWN",
                                    Product.last_seen_at >= datetime.utcnow() - timedelta(days=env("RECO_MAX_AGE_DAYS", "120")))
    ids = set(db.scalars(base.order_by(Product.opportunity_score.desc(), Product.id).limit(int(env("RECO_POOL", "1500")))))
    top_cats = [c for c, _ in sorted(ctx["categories"].items(), key=lambda kv: -kv[1])[:5]]
    if top_cats:
        ids |= set(db.scalars(base.where(Product.category.in_(top_cats)).order_by(Product.opportunity_score.desc()).limit(500)))
    if ctx["followed"]:
        ids |= set(db.scalars(base.where(Product.id.in_(select(Ad.product_id).where(Ad.advertiser_id.in_(ctx["followed"]))))
                              .limit(500)))
    return ids


_VEC = {"at": 0.0, "v": {}}


def vectors(db, pids):
    """One stored CLIP vector per product (cover creative, else first embedded creative). Process cache, 1 h."""
    if time.time() - _VEC["at"] > 3600:
        _VEC.update(at=time.time(), v={})
    cache, need = _VEC["v"], [p for p in pids if p not in _VEC["v"]]
    if need:
        from .visual import embed
        from .visual.models import ProductImage
        first = (select(Creative.product_id, func.min(ProductImage.id).label("img"))
                 .join(ProductImage, (ProductImage.source == "creative") & (ProductImage.source_id == Creative.id))
                 .where(Creative.product_id.in_(need), ProductImage.embedding.is_not(None))
                 .group_by(Creative.product_id).subquery())
        for pid, blob in db.execute(select(first.c.product_id, ProductImage.embedding).join(ProductImage, ProductImage.id == first.c.img)):
            v = embed.from_bytes(blob).astype(np.float32)
            n = float(np.linalg.norm(v))
            cache[pid] = v / n if n else None
        for p in need:
            cache.setdefault(p, None)
    return {p: cache[p] for p in pids}


# ---------------- components ----------------

def _cat(ctx, p):
    if not ctx["categories"] or not p.category:
        return None, None
    s = ctx["categories"].get(p.category, 0.0)
    return s, (f"Danh mục \"{p.category}\" bạn quan tâm" if s >= 0.3 else None)


def _attr_sim(p, q):
    s = 0.5 if p.category and p.category == q.category else 0.0
    if s and p.subcategory and p.subcategory == q.subcategory:
        s += 0.2
    if p.price and q.price and p.currency and p.currency == q.currency and abs(p.price - q.price) <= 0.3 * q.price:
        s += 0.3
    return s


def _similarity(ctx, p, followed_hit):
    pos = [(w, q) for pid, w in ctx["seeds"].items() if w > 0 and (q := ctx["seed_rows"].get(pid))]
    neg = [q for pid, w in ctx["seeds"].items() if w < 0 and (q := ctx["seed_rows"].get(pid))]
    if not pos and not neg and not ctx["followed"]:
        return None, []
    best, best_q = 0.0, None
    for w, q in pos:
        s = _attr_sim(p, q) * min(1.0, w)
        if s > best:
            best, best_q = s, q
    reasons = []
    if best_q is not None and best >= 0.4:
        reasons.append(f"Giống sản phẩm bạn đã lưu/test: {best_q.canonical_name}")
    if followed_hit:
        best = max(best, 0.8)
        reasons.append("Advertiser bạn đang Follow đang chạy sản phẩm này")
    worst = max((_attr_sim(p, q) for q in neg), default=0.0)
    if worst >= 0.5:
        best = max(0.0, best - worst)
        reasons.append("Lưu ý: giống sản phẩm bạn đã test thất bại")
    return best, reasons


def _semantic(ctx, p, vecs):
    pos = [pid for pid, w in ctx["seeds"].items() if w > 0]
    if not pos:
        return None, None
    v = vecs.get(p.id)
    sv = [vecs.get(s) for s in pos if vecs.get(s) is not None]
    if v is not None and sv:
        cos = max(float(np.dot(v, s)) for s in sv)
        s = min(1.0, max(0.0, (cos - 0.70) / 0.25))  # CLIP: .70 unrelated … .95 near-identical
        return s, ("Hình ảnh gần với sản phẩm bạn đã chọn" if s >= 0.5 else None)
    seed_tokens = set().union(*(tokens(ctx["seed_rows"][s].canonical_name) for s in pos if s in ctx["seed_rows"]))
    mine = tokens(p.canonical_name)
    if not seed_tokens or not mine:
        return None, None
    s = len(mine & seed_tokens) / len(mine | seed_tokens)
    return min(1.0, s * 2), ("Tên sản phẩm gần với lựa chọn trước của bạn" if s >= 0.25 else None)


def _market(ctx, p):
    if not ctx["markets"]:
        return None, None
    feats = p.features or {}
    primary = [c for c in [p.country] if c and c.upper() != "ALL"]
    others = [c for c in feats.get("markets") or [] if c and c.upper() != "ALL" and c not in primary]
    if not primary and not others:
        return None, None
    lang = (p.language or "").lower() or None
    best, where = 0.0, None
    for countries, factor in ((primary, 1.0), (others, 0.6)):
        for c in countries:
            for m in PF._markets(c.upper()[:2], lang) + [c.upper()[:2]]:
                s = ctx["markets"].get(m, 0) * factor
                if s > best:
                    best, where = s, m
    if ctx["languages"] and lang:
        best = 0.75 * best + 0.25 * (1.0 if lang in ctx["languages"] else 0.0)
    label = PF.MARKET_LABEL.get(where, where)
    return best, (f"Chạy ở {label} — thị trường bạn ưu tiên" if best >= 0.5 and where else None)


def _verified(p):
    f = p.features or {}
    ads, advs, days = f.get("unique_ads"), f.get("advertiser_count"), f.get("longevity_days")
    if not ads:
        return None, None
    s = 0.4 * min(1.0, math.log1p(ads) / math.log1p(20)) + 0.3 * min(1.0, (advs or 0) / 5) + 0.3 * min(1.0, (days or 0) / 30)
    return s, (f"{ads} Ads đang chạy từ {advs or 0} advertiser, Ads lâu nhất {days or 0} ngày" if s >= 0.4 else None)


def _freshness(p, now):
    if not p.first_seen_at:
        return None, None
    age = max(0.0, (now - p.first_seen_at).days)
    idle = max(0.0, (now - (p.last_seen_at or p.first_seen_at)).days - 7)
    s = 0.5 ** (age / 30) * 0.5 ** (idle / 14)
    return s, (f"Mới xuất hiện {int(age)} ngày trước" if s >= 0.5 else None)


def user_side(ctx):
    """Components the MKT has data for; the rest are renormalised away (cold start)."""
    has_pos = any(w > 0 for w in ctx["seeds"].values())
    return [k for k, ok in (("category", bool(ctx["categories"])), ("similarity", bool(ctx["seeds"] or ctx["followed"])),
                            ("semantic", has_pos), ("market", bool(ctx["markets"])), ("verified", True), ("freshness", True)) if ok]


def score_product(ctx, p, vecs, followed_hit=False, now=None):
    now = now or datetime.utcnow()
    parts, reasons = {}, []
    s, r = _cat(ctx, p); parts["category"] = s; reasons += [r] if r else []
    s, rs = _similarity(ctx, p, followed_hit); parts["similarity"] = s; reasons += rs
    s, r = _semantic(ctx, p, vecs); parts["semantic"] = s; reasons += [r] if r else []
    s, r = _market(ctx, p); parts["market"] = s; reasons += [r] if r else []
    s, r = _verified(p); parts["verified"] = s; reasons += [r] if r else []
    s, r = _freshness(p, now); parts["freshness"] = s; reasons += [r] if r else []
    usable = user_side(ctx)
    denom = sum(WEIGHTS[k] for k in usable)
    raw = sum(WEIGHTS[k] * (parts[k] or 0.0) for k in usable) / denom
    coverage = sum(WEIGHTS[k] for k in usable if parts[k] is not None) / denom
    score = round(100 * raw, 1)
    comps = {k: {"score": None if v is None else round(v, 3), "weight": WEIGHTS[k],
                 "effective_weight": round(WEIGHTS[k] / denom, 3) if k in usable else 0.0,
                 "missing": None if v is not None else ("product" if k in usable else "mkt"), "label": LABEL[k]}
             for k, v in parts.items()}
    if ctx["cold_start"]:
        reasons.append("Hồ sơ mới: xếp theo thị trường ưu tiên, tín hiệu Ads thật và độ mới")
    return score, round(coverage, 3), comps, reasons


def exclusion(ctx, p, owners):
    if p.id in ctx["red"]:
        return "merged_into_other_cluster"
    if owners.get(p.id) not in (None, ctx["uid"]):
        return "owned_by_other_mkt"
    if p.id in ctx["mine"]:
        return "already_yours"
    if p.id in ctx["seeds"]:
        return "already_in_your_history"
    if p.id in ctx["not_interested"]:
        return "not_interested"
    if p.category and p.category in ctx["excluded"]:
        return "excluded_category"
    return None


def lock_owners(db, pids):
    return dict(db.execute(select(AllocationLock.product_id, ProductAllocation.user_id)
                           .join(ProductAllocation, ProductAllocation.id == AllocationLock.allocation_id)
                           .where(AllocationLock.product_id.in_(list(pids)))).all()) if pids else {}


def followed_hits(db, ctx, pids):
    if not ctx["followed"] or not pids:
        return set()
    return set(db.scalars(select(Ad.product_id).where(Ad.product_id.in_(list(pids)), Ad.advertiser_id.in_(ctx["followed"])).distinct()))


def _explore_order(uid, pid, day):
    return hashlib.sha1(f"{uid}:{day}:{pid}".encode()).hexdigest()


def rank(ctx, scored, limit):
    """Main list by score; every 1/share-th slot is an exploration pick outside the MKT's known categories."""
    share = env("RECO_EXPLORE_SHARE", "0.2") if not ctx["cold_start"] else 0.0  # cold start: everything is exploration
    known = {c for c, s in ctx["categories"].items() if s >= 0.3}
    day = datetime.utcnow().date().isoformat()
    explore = [x for x in scored if x["category"] not in known and (x["components"]["verified"]["score"] or 0) >= 0.4
               and (x["components"]["freshness"]["score"] or 0) >= 0.2] if share > 0 else []
    explore.sort(key=lambda x: _explore_order(ctx["uid"], x["product_id"], day))
    n_explore = int(round(min(limit, len(scored)) * share)) if explore else 0  # 20 % of what is actually listed
    picks = {x["product_id"] for x in explore[:n_explore]}
    main = [x for x in sorted(scored, key=lambda x: (-x["score"], x["product_id"])) if x["product_id"] not in picks]
    step = max(2, int(round(1 / share))) if share > 0 else 0
    out, ex = [], [x for x in explore[:n_explore]]
    while (main or ex) and len(out) < limit:
        if ex and step and (len(out) + 1) % step == 0 or not main:
            x = ex.pop(0)
            x["explore"] = True
            x["reasons"] = x["reasons"] + ["Khám phá: ngoài danh mục quen thuộc, có tín hiệu Ads thật và còn mới"]
        else:
            x = main.pop(0)
        out.append(x)
    return out


def compute(db, uid, limit=None, ctx=None):
    limit = limit or int(env("RECO_KEEP", "200"))
    ctx = ctx or context(db, uid)
    ids = pool(db, ctx)
    owners = lock_owners(db, ids)
    prods = list(db.scalars(select(Product).where(Product.id.in_(list(ids))))) if ids else []
    prods = [p for p in prods if not exclusion(ctx, p, owners)]
    pos = [pid for pid, w in ctx["seeds"].items() if w > 0]
    vecs = vectors(db, [p.id for p in prods] + pos) if pos else {}
    hits = followed_hits(db, ctx, [p.id for p in prods])
    now = datetime.utcnow()
    scored = []
    for p in prods:
        score, cov, comps, reasons = score_product(ctx, p, vecs, p.id in hits, now)
        scored.append({"product_id": p.id, "category": p.category, "score": score, "coverage": cov,
                       "components": comps, "reasons": reasons, "explore": False})
    return ctx, rank(ctx, scored, limit), len(prods)


def refresh(db, uid):
    ctx, rows, n = compute(db, uid)
    now = datetime.utcnow()
    db.execute(delete(Recommendation).where(Recommendation.user_id == uid))
    for i, x in enumerate(rows, 1):
        db.add(Recommendation(user_id=uid, product_id=x["product_id"], rank=i, score=x["score"], coverage=x["coverage"],
                              explore=x["explore"], components=x["components"], reasons=x["reasons"], computed_at=now))
    st = db.get(RecommendationState, uid) or RecommendationState(user_id=uid)
    st.signature, st.candidates, st.cold_start, st.version, st.computed_at = ctx["signature"], n, ctx["cold_start"], VERSION, now
    db.add(st)
    _MEMO.pop(uid, None)
    return st


def is_stale(db, uid, st=None, ctx=None):
    st = st or db.get(RecommendationState, uid)
    if st is None or st.version != VERSION:
        return True
    if datetime.utcnow() - st.computed_at > timedelta(hours=env("RECO_TTL_HOURS", "6")):
        return True
    return (ctx or context(db, uid))["signature"] != st.signature


_MEMO: dict = {}  # uid → (time, rows) for cache misses before the worker has run


def feed(db, uid, limit=40, offset=0, explore=True):
    """Cached rows when fresh, else an in-memory compute (memoised 10 min). Ownership is filtered live."""
    st = db.get(RecommendationState, uid)
    ctx = context(db, uid)
    cached = st is not None and not is_stale(db, uid, st, ctx)
    if cached:
        rows = [{"product_id": r.product_id, "score": r.score, "coverage": r.coverage, "explore": r.explore,
                 "components": r.components, "reasons": r.reasons}
                for r in db.scalars(select(Recommendation).where(Recommendation.user_id == uid).order_by(Recommendation.rank))]
        computed_at, cold = st.computed_at, st.cold_start
    else:
        hit = _MEMO.get(uid)
        if not hit or time.time() - hit[0] > 600 or hit[1] != ctx["signature"]:
            _, rows, _ = compute(db, uid, ctx=ctx)
            hit = _MEMO[uid] = (time.time(), ctx["signature"], rows, ctx["cold_start"])
        _, _, rows, cold = hit
        computed_at = datetime.utcnow()
    owners = lock_owners(db, {r["product_id"] for r in rows})
    rows = [r for r in rows if owners.get(r["product_id"]) in (None,) and (explore or not r["explore"])]
    rows = soften(db, uid, rows)
    page = rows[offset:offset + limit]
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r["product_id"] for r in page])))} if page else {}
    from .services import naming as N
    out = []
    for r in page:
        p = prods.get(r["product_id"])
        if p is None:
            continue
        out.append({**r, "product": {"id": p.id, "name": N.display_name(p), "category": p.category, "country": p.country,
                                     "price": p.price, "currency": p.currency, "cover_creative_id": p.cover_creative_id,
                                     "opportunity_score": p.opportunity_score}})
    return {"total": len(rows), "rows": out, "cached": cached, "cold_start": cold, "computed_at": computed_at,
            "weights": WEIGHTS, "version": VERSION}


def soften(db, uid, rows):
    """Soft dedup at read time (app/interest.py): other MKTs' Save / Favorite lower the score, then the main list is
    re-ranked; exploration slots keep their positions. Read live, so Unsave / Unfavorite count at once."""
    from . import interest as I
    seen = I.others(db, uid)
    out = []
    for r in rows:
        e = seen.get(r["product_id"])
        fit = I.apply({"score": r["score"], "reasons": r["reasons"]}, e)
        out.append({**r, **fit, "interest": I.label(e)})
    slots = [i for i, r in enumerate(out) if r["explore"]]
    main = sorted((r for r in out if not r["explore"]), key=lambda r: (-r["score"], r["product_id"]))
    ranked, it = [], iter(main)
    for i in range(len(out)):
        ranked.append(out[i] if i in slots else next(it))
    return ranked


def explain(db, uid, pid):
    ctx = context(db, uid)
    p = db.get(Product, pid)
    if p is None:
        return None
    r = root(ctx["red"], pid)
    p = db.get(Product, r) or p
    owners = lock_owners(db, {p.id})
    pos = [s for s, w in ctx["seeds"].items() if w > 0]
    vecs = vectors(db, [p.id] + pos) if pos else {}
    score, cov, comps, reasons = score_product(ctx, p, vecs, p.id in followed_hits(db, ctx, [p.id]))
    from . import interest as I
    e = I.others(db, uid).get(p.id)
    fit = I.apply({"score": score, "reasons": reasons}, e)
    return {"product_id": p.id, "requested_product_id": pid, "coverage": cov, "components": comps, **fit,
            "interest": I.label(e), "excluded": exclusion(ctx, p, owners), "cold_start": ctx["cold_start"], "weights": WEIGHTS}


def refresh_stale(limit=50):
    from .workspace.models import User
    done = 0
    with SessionLocal() as db:
        for uid in db.scalars(select(User.id).where(User.status == "active").order_by(User.id)).all():
            if done >= limit:
                break
            if is_stale(db, uid):
                refresh(db, uid)
                db.commit()
                done += 1
    return done


async def loop():
    every = env("RECO_REFRESH_MIN", "20")
    if not every:
        return
    log = logging.getLogger("app.recommend")
    while True:
        try:
            n = await asyncio.to_thread(refresh_stale)
            if n:
                log.info("recommendations: %s MKT feeds refreshed", n)
        except Exception:
            log.exception("recommendation refresh failed")
        await asyncio.sleep(every * 60)
