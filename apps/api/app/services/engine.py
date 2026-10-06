"""Glue: feature → score → decision for products, plus the Daily Snapshot Engine (§18)."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (Ad, ad_markets, AdvertiserDailySnapshot, CreativeDailySnapshot, Experiment, MarketDailySnapshot, Product,
                      ProductDailySnapshot)
from . import scoring as S
from .decision import analyze_experiment, auto_lifecycle, learning_profile, market_fit, recommend
from .features import ProductData, compute_features, load_product_data

HIDDEN_WINNER_MIN = 60
POTENTIAL_WINNER_MIN = 65


def compute_scores(product: Product, f: dict, profile: dict) -> dict:
    sat, sat_state, sat_b = S.saturation_score(f)
    ext, ext_b = S.external_win_score(f, sat)
    internal, int_b = S.internal_win_score(f, S.to_usd(f.get("avg_price") or product.price, product.currency))
    win, blend = S.blend_win(ext, internal, f)
    rarity, rar_b = S.rarity_score(f)
    growth = S.growth_velocity(f)
    fit = market_fit(profile, product.country, product.category)
    margin = S.margin_potential(product, f)
    rare = S.rare_winner_score(win, rarity, growth, fit, margin)
    conf, conf_b = S.confidence_score(f)
    opp_raw, opp = S.opportunity_score(win, sat, growth, margin, conf)
    return {
        "external_win_score": ext, "internal_win_score": internal, "win_score": win,
        "saturation_score": sat, "saturation_state": sat_state,
        "rarity_score": rarity, "rare_winner_score": rare, "growth_velocity": growth,
        "market_fit": fit, "margin_potential": round(margin, 1),
        "confidence_score": conf, "opportunity_raw": opp_raw, "opportunity_score": opp,
        "customer_rejection_score": S.customer_rejection_score(f),
        "sentiment_score": S.sentiment_score(f),
        "ad_rejection_rate": f.get("ad_rejection_rate"),
        "breakdown": {"external": ext_b, "internal": int_b, "blend": blend, "saturation": sat_b,
                      "rarity": rar_b, "confidence": conf_b},
    }


def hidden_winner_tags(product: Product, f: dict) -> list[str]:
    tags = []
    if f["creative_growth_7d"] >= 0.5 or f["advertiser_growth"] >= 0.3:
        tags.append("fast_growing")
    if product.rarity_score >= 70:
        tags.append("rare")
    if product.saturation_score < 30:
        tags.append("low_competition")
    if f["new_markets_7d"]:
        tags.append("new_market")
    if (product.features or {}).get("_scores", {}).get("margin_potential", 0) >= 70:
        tags.append("good_margin")
    if f["comments"] >= 10 and f["positive_comment_rate"] >= 0.6:
        tags.append("positive_feedback")
    return tags


class ScoreContext:
    """Expensive shared inputs (learning profile, taste model, market DNA), cached for incremental scoring."""

    _cache: "ScoreContext | None" = None

    def __init__(self, db: Session):
        from .discovery import TasteModel, market_dna

        self.profile = learning_profile(db)
        self.taste = TasteModel(db)
        self.dna = market_dna(db)
        self.at = datetime.utcnow()

    @classmethod
    def get(cls, db: Session, max_age_s: int = 300) -> "ScoreContext":
        if cls._cache is None or (datetime.utcnow() - cls._cache.at).total_seconds() > max_age_s:
            cls._cache = ScoreContext(db)
        return cls._cache

    @classmethod
    def invalidate(cls):
        cls._cache = None


def score_product(db: Session, product: Product, data: ProductData, ctx: "ScoreContext | dict") -> dict:
    from ..models import Creative, Vote
    from .discovery import discovery_decision, potential_vector

    profile = ctx if isinstance(ctx, dict) else ctx.profile
    prev_rec = product.recommendation
    f = compute_features(data)
    sc = compute_scores(product, f, profile)
    for k in ("external_win_score", "internal_win_score", "win_score", "saturation_score", "saturation_state",
              "rarity_score", "rare_winner_score", "confidence_score", "opportunity_score",
              "customer_rejection_score", "sentiment_score", "ad_rejection_rate"):
        setattr(product, k, sc[k])
    f["_scores"] = {k: sc[k] for k in ("growth_velocity", "market_fit", "margin_potential", "opportunity_raw")}
    f["_breakdown"] = sc["breakdown"]
    product.features = f
    if f.get("gross_margin") is not None and product.price:
        product.cost = product.cost or round(product.price * (1 - f["gross_margin"]), 2)
    product.last_seen_at = max((a.last_seen_at for a in data.ads), default=product.last_seen_at)

    # Product Potential Vector (discovery) — needs creatives + votes
    if not isinstance(ctx, dict):
        creatives = list(db.scalars(select(Creative).where(Creative.product_id == product.id)))
        votes = list(db.scalars(select(Vote).where(Vote.product_id == product.id)))
        pv = potential_vector(product, f, data.ads, creatives, votes, ctx.taste, ctx.dna)
        v = pv["vector"]
        product.potential = pv
        product.classification = pv["quadrant"]
        product.novelty_score, product.wave_score = v["novelty"], v["wave_potential"]
        product.creative_potential, product.mkt_appeal = v["creative_potential"], v["mkt_appeal"]
        product.compliance_risk, product.company_fit_score = v["compliance_risk"], v["company_fit"]
        product.market_scores = pv["per_market"]
        product.funnel_mix = dict(Counter(a.funnel for a in data.ads if a.funnel and not a.is_internal))
        from ..markets import ad_in_targets

        product.in_target = ad_in_targets(f.get("markets") or ([product.country] if product.country else []))
        stored = [c for c in creatives if c.status == "stored"]
        if stored:
            product.cover_creative_id = sorted(stored, key=lambda c: (c.type != "video", c.id))[0].id
        if f.get("has_internal") and product.internal_win_score is not None:
            action, reasons = recommend(product, f)  # company data wins over market signals (realtime §10)
        else:
            action, reasons = discovery_decision(v, pv["quadrant"])
    else:
        action, reasons = recommend(product, f)
    product.recommendation, product.recommendation_reasons = action, reasons
    product.tags = hidden_winner_tags(product, f) if is_hidden_winner(product) else []
    product.scored_at = datetime.utcnow()
    auto_lifecycle(db, product, f, data.experiments)
    if prev_rec and prev_rec != action:
        from ..events import publish

        publish("DECISION_CHANGED", {"from": prev_rec, "to": action, "reasons": reasons, "name": product.canonical_name},
                product_id=product.id, db=db)
    return f


def rescore_products(db: Session, product_ids) -> int:
    """Incremental computation (realtime §4): only the products touched by new events."""
    from ..db import write_lock

    with write_lock():
        return _rescore(db, product_ids)


def _rescore(db: Session, product_ids) -> int:
    ctx = ScoreContext.get(db)
    n = 0
    for pid in set(product_ids):
        p = db.get(Product, pid)
        if p:
            score_product(db, p, load_product_data(db, p), ctx)
            n += 1
    db.flush()
    return n


def is_hidden_winner(p: Product) -> bool:
    f = p.features or {}
    return (p.rare_winner_score >= HIDDEN_WINNER_MIN and p.confidence_score >= 35
            and f.get("advertiser_count", 999) <= 20 and p.saturation_score < 45)


def is_potential_winner(p: Product) -> bool:
    return p.win_score >= POTENTIAL_WINNER_MIN and p.confidence_score >= 45


# ------------------------------------------------------------ Experiments
def analyze_all_experiments(db: Session) -> int:
    exps = db.scalars(select(Experiment)).all()
    for e in exps:
        p = db.get(Product, e.product_id)
        f = (p.features or {}) if p else {}
        market_price = f.get("avg_price")
        mismatch = 0.0
        if p:
            mismatch = (p.features or {}).get("quality_complaint_rate", 0.0)
        r = analyze_experiment(e, market_price, mismatch)
        # status = funnel verdict; whether it is still running is `ended_at is None`
        e.status = r["status"]
        e.failure_types, e.diagnosis, e.decision = r["failure_types"], r["diagnosis"], r["decision"]
    return len(exps)


def score_all(db: Session) -> int:
    analyze_all_experiments(db)
    ScoreContext.invalidate()
    ctx = ScoreContext.get(db)
    products = db.scalars(select(Product)).all()
    for p in products:
        score_product(db, p, load_product_data(db, p), ctx)
    db.flush()
    return len(products)


# ------------------------------------------------------------ Daily snapshots (§18)
def _product_snapshot(p: Product, data: ProductData, d: date, profile: dict) -> tuple[ProductDailySnapshot, dict, dict]:
    f = compute_features(data, d)
    sc = compute_scores(p, f, profile)
    snap = ProductDailySnapshot(
        product_id=p.id, date=d,
        active_ads=f["active_ads"], new_ads=sum(1 for a in data.ads if not a.is_internal and a.first_seen_at.date() == d),
        removed_ads=f["removed_ads_7d"], advertisers=f["advertiser_count"], new_advertisers=f["new_advertisers_7d"],
        markets=f["market_count"], new_markets=len(f["new_markets_7d"]), stores=f["store_count"],
        comments=f["comments"],
        positive_comments=round(f["comments"] * f["positive_comment_rate"]),
        negative_comments=round(f["comments"] * f["negative_comment_rate"]),
        traffic=f["estimated_traffic"], avg_price=f["avg_price"],
        external_win_score=sc["external_win_score"], internal_win_score=sc["internal_win_score"],
        rarity_score=sc["rarity_score"], saturation_score=sc["saturation_score"],
        opportunity_score=sc["opportunity_score"], refusal_rate=f.get("refusal_rate"),
    )
    return snap, f, sc


def build_snapshots(db: Session, d: date | None = None, products: list[Product] | None = None,
                    preloaded: dict[int, ProductData] | None = None, profile: dict | None = None) -> int:
    d = d or date.today()
    products = products if products is not None else db.scalars(select(Product)).all()
    profile = profile or learning_profile(db)
    db.execute(delete(ProductDailySnapshot).where(ProductDailySnapshot.date == d))
    db.execute(delete(MarketDailySnapshot).where(MarketDailySnapshot.date == d))
    db.execute(delete(AdvertiserDailySnapshot).where(AdvertiserDailySnapshot.date == d))
    db.execute(delete(CreativeDailySnapshot).where(CreativeDailySnapshot.date == d))

    market = defaultdict(lambda: Counter())
    market_opp = defaultdict(list)
    market_adv = defaultdict(set)
    n = 0
    for p in products:
        data = preloaded[p.id] if preloaded else load_product_data(db, p)
        if not any(a.first_seen_at.date() <= d for a in data.ads) and p.first_seen_at.date() > d:
            continue
        snap, f, sc = _product_snapshot(p, data, d, profile)
        db.add(snap)
        n += 1
        # creative snapshot
        active = [a for a in data.ads if not a.is_internal and a.first_seen_at.date() <= d
                  and a.last_seen_at.date() >= d - timedelta(days=2)]
        fps = Counter(a.creative_fingerprint for a in active)
        db.add(CreativeDailySnapshot(
            product_id=p.id, date=d, unique_creatives=len(fps),
            duplicated_creatives=sum(c - 1 for c in fps.values() if c > 1),
            top_hooks=dict(Counter(a.hook for a in active if a.hook).most_common(5)),
            top_angles=dict(Counter(a.angle for a in active if a.angle).most_common(5)),
        ))
        # market rollup by country × category
        for c in (f["markets"] or [p.country or "??"]):
            key = (c, p.category or "other")
            m = market[key]
            m["products"] += 1
            m["new_products"] += 1 if p.first_seen_at.date() == d or (data.ads and min(a.first_seen_at for a in data.ads).date() == d) else 0
            m["active_ads"] += sum(1 for a in active if c in ad_markets(a))
            m["new_ads"] += sum(1 for a in active if c in ad_markets(a) and a.first_seen_at.date() == d)
            market_adv[key] |= {a.advertiser_id for a in active if c in ad_markets(a)}
            pseudo = Product(win_score=sc["win_score"], confidence_score=sc["confidence_score"],
                             rare_winner_score=sc["rare_winner_score"], saturation_score=sc["saturation_score"], features=f)
            m["potential_winners"] += 1 if is_potential_winner(pseudo) else 0
            m["hidden_winners"] += 1 if is_hidden_winner(pseudo) else 0
            market_opp[key].append(sc["opportunity_score"])

    for (country, cat), m in market.items():
        opps = market_opp[(country, cat)]
        db.add(MarketDailySnapshot(
            country=country, category=cat, date=d, products=m["products"], new_products=m["new_products"],
            active_ads=m["active_ads"], new_ads=m["new_ads"], advertisers=len(market_adv[(country, cat)] - {None}),
            potential_winners=m["potential_winners"], hidden_winners=m["hidden_winners"],
            avg_opportunity=round(sum(opps) / len(opps), 1) if opps else 0,
        ))

    # advertiser snapshot
    ads = db.scalars(select(Ad).where(Ad.advertiser_id.is_not(None), Ad.is_internal.is_(False))).all()
    by_adv = defaultdict(list)
    for a in ads:
        if a.first_seen_at.date() <= d and a.last_seen_at.date() >= d - timedelta(days=2):
            by_adv[a.advertiser_id].append(a)
    for adv_id, lst in by_adv.items():
        db.add(AdvertiserDailySnapshot(
            advertiser_id=adv_id, date=d, active_ads=len(lst),
            new_ads=sum(1 for a in lst if a.first_seen_at.date() > d - timedelta(days=7)),
            products=len({a.product_id for a in lst}), countries=len({c for a in lst for c in ad_markets(a)}),
        ))
    db.flush()
    return n


def backfill_snapshots(db: Session, days: int = 30) -> int:
    products = db.scalars(select(Product)).all()
    pre = {p.id: load_product_data(db, p) for p in products}
    profile = learning_profile(db)
    total = 0
    today = date.today()
    for i in range(days, -1, -1):
        total += build_snapshots(db, today - timedelta(days=i), products, pre, profile)
    return total
