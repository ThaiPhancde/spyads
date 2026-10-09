"""Glue: feature → score → decision for products, plus the Daily Snapshot Engine (§18)."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (ADS_ONLY, Ad, ad_markets, AdvertiserDailySnapshot, MarketDailySnapshot, Product,
                      ProductDailySnapshot)
from . import scoring as S
from .decision import auto_lifecycle
from .features import ProductData, compute_features, load_product_data
from ..markets import priority_boost

POTENTIAL_WINNER_MIN = 65
NEUTRAL_MARKET_FIT = 60.0  # rare_winner_score factor; per-market fit lives in discovery.market_fit_score


def compute_scores(product: Product, f: dict) -> dict:
    sat, sat_state, sat_b = S.saturation_score(f)
    win, ext_b = S.external_win_score(f, sat)
    rarity, rar_b = S.rarity_score(f)
    growth = S.growth_velocity(f)
    fit = NEUTRAL_MARKET_FIT
    margin = S.margin_potential(product, f)
    rare = S.rare_winner_score(win, rarity, growth, fit, margin)
    conf, conf_b = S.confidence_score(f)
    opp_raw, opp = S.opportunity_score(win, sat, growth, margin, conf)
    # priority market (PH) ranks first everywhere opportunity_score sorts — lists, radar, media download order.
    # Applied once, here; the pre-boost value is kept (features.opportunity_raw) so text / UI can show both.
    boost = priority_boost(f.get("markets") or [product.country])
    unboosted, opp = opp, min(100.0, round(opp + boost, 1))
    return {
        "opportunity_unboosted": unboosted, "priority_boost": boost,
        "external_win_score": win, "win_score": win,
        "saturation_score": sat, "saturation_state": sat_state,
        "rarity_score": rarity, "rare_winner_score": rare, "growth_velocity": growth,
        "market_fit": fit, "margin_potential": round(margin, 1),
        "confidence_score": conf, "opportunity_raw": opp_raw, "opportunity_score": opp,
        "sentiment_score": S.sentiment_score(f),
        "breakdown": {"external": ext_b, "saturation": sat_b,
                      "rarity": rar_b, "confidence": conf_b},
    }


class ScoreContext:
    """Expensive shared inputs (taste model, market DNA), cached for incremental scoring."""

    _cache: "ScoreContext | None" = None

    def __init__(self, db: Session):
        from .discovery import TasteModel, market_dna

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


def score_product(db: Session, product: Product, data: ProductData, ctx: ScoreContext) -> dict:
    from ..models import Creative, Vote
    from .discovery import discovery_decision, potential_vector

    prev_rec = product.recommendation
    f = compute_features(data)
    sc = compute_scores(product, f)
    for k in ("external_win_score", "win_score", "saturation_score", "saturation_state",
              "rarity_score", "rare_winner_score", "confidence_score", "opportunity_score", "sentiment_score"):
        setattr(product, k, sc[k])
    f["_scores"] = {k: sc[k] for k in ("growth_velocity", "market_fit", "margin_potential", "opportunity_raw")}
    f["_breakdown"] = sc["breakdown"]
    f["opportunity_raw"], f["priority_boost"] = sc["opportunity_unboosted"], sc["priority_boost"]
    f["hidden_alerted"] = (product.features or {}).get("hidden_alerted", False)  # alert state survives rescoring (alerts.py)
    from .hidden import evaluate

    hw = evaluate(product, data.ads, f)
    f["hidden_score"] = hw["score"] if hw else None
    product.tags = hw["tags"] if hw else []
    product.features = f
    product.last_seen_at = max((a.last_seen_at for a in data.ads), default=product.last_seen_at)

    # Product Potential Vector (discovery) — needs creatives + votes
    creatives = list(db.scalars(select(Creative).where(Creative.product_id == product.id)))
    votes = list(db.scalars(select(Vote).where(Vote.product_id == product.id)))
    pv = potential_vector(product, f, data.ads, creatives, votes, ctx.taste, ctx.dna)
    v = pv["vector"]
    product.potential = pv
    product.classification = pv["quadrant"]
    product.novelty_score, product.wave_score = v["novelty"], v["wave_potential"]
    product.creative_potential, product.mkt_appeal = v["creative_potential"], v["mkt_appeal"]
    product.compliance_risk = v["compliance_risk"]
    product.market_scores = pv["per_market"]
    product.funnel_mix = dict(Counter(a.funnel for a in data.ads if a.funnel and not a.is_internal))
    from ..markets import ad_in_targets

    product.in_target = ad_in_targets(f.get("markets") or ([product.country] if product.country else []))
    stored = [c for c in creatives if c.status == "stored"]
    if stored:
        product.cover_creative_id = sorted(stored, key=lambda c: (c.type != "video", c.id))[0].id
    action, reasons = discovery_decision(v, pv["quadrant"])
    product.recommendation, product.recommendation_reasons = action, reasons
    product.scored_at = datetime.utcnow()
    auto_lifecycle(db, product)
    if prev_rec and prev_rec != action:
        from ..events import publish

        publish("DECISION_CHANGED", {"from": prev_rec, "to": action, "reasons": reasons, "name": product.canonical_name},
                product_id=product.id, db=db)
    return f


def rescore_products(db: Session, product_ids) -> int:
    """Incremental computation (realtime §4): only the products touched by new events."""
    ctx = ScoreContext.get(db)
    ids = set(product_ids)
    n = 0
    for pid in ids:
        p = db.get(Product, pid)
        if p:
            score_product(db, p, load_product_data(db, p), ctx)
            n += 1
            if len(ids) > 50 and n % 50 == 0:  # big batches (live search): let other writers in between
                db.commit()
    db.flush()
    return n


def is_hidden_winner(p: Product) -> bool:
    """Stored verdict of services/hidden.evaluate (set by score_product): same rule as the Hidden Winners page."""
    return (p.features or {}).get("hidden_score") is not None


def is_potential_winner(p: Product) -> bool:
    return p.win_score >= POTENTIAL_WINNER_MIN and p.confidence_score >= 45


def score_all(db: Session, commit_every: int | None = None) -> int:
    """`commit_every`: background jobs commit in small batches — one transaction over every product held the SQLite
    write lock for minutes, and every other write (votes, live search, ingest) timed out with HTTP 500."""
    ScoreContext.invalidate()
    ctx = ScoreContext.get(db)
    products = db.scalars(select(Product)).all()
    for i, p in enumerate(products, 1):
        score_product(db, p, load_product_data(db, p), ctx)
        if commit_every and i % commit_every == 0:
            db.commit()
    db.flush()
    return len(products)


# ------------------------------------------------------------ Daily snapshots (§18)
def _product_snapshot(p: Product, data: ProductData, d: date) -> tuple[ProductDailySnapshot, dict, dict]:
    f = compute_features(data, d)
    sc = compute_scores(p, f)
    snap = ProductDailySnapshot(
        product_id=p.id, date=d,
        active_ads=f["active_ads"], new_ads=sum(1 for a in data.ads if not a.is_internal and a.first_seen_at.date() == d),
        removed_ads=f["removed_ads_7d"], advertisers=f["advertiser_count"], new_advertisers=f["new_advertisers_7d"],
        markets=f["market_count"], new_markets=len(f["new_markets_7d"]), stores=f["store_count"],
        comments=f["comments"],
        positive_comments=round(f["comments"] * f["positive_comment_rate"]),
        negative_comments=round(f["comments"] * f["negative_comment_rate"]),
        traffic=f["estimated_traffic"], avg_price=f["avg_price"],
        external_win_score=sc["external_win_score"], rarity_score=sc["rarity_score"],
        saturation_score=sc["saturation_score"], opportunity_score=sc["opportunity_score"],
    )
    return snap, f, sc


def build_snapshots(db: Session, d: date | None = None, products: list[Product] | None = None,
                    preloaded: dict[int, ProductData] | None = None, commit_every: int | None = None) -> int:
    d = d or date.today()
    products = products if products is not None else db.scalars(select(Product)).all()
    db.execute(delete(ProductDailySnapshot).where(ProductDailySnapshot.date == d))
    db.execute(delete(MarketDailySnapshot).where(MarketDailySnapshot.date == d))
    db.execute(delete(AdvertiserDailySnapshot).where(AdvertiserDailySnapshot.date == d))

    market = defaultdict(lambda: Counter())
    market_opp = defaultdict(list)
    market_adv = defaultdict(set)
    n = 0
    for p in products:
        data = preloaded[p.id] if preloaded else load_product_data(db, p)
        if not any(a.first_seen_at.date() <= d for a in data.ads) and p.first_seen_at.date() > d:
            continue
        snap, f, sc = _product_snapshot(p, data, d)
        db.add(snap)
        n += 1
        if commit_every and n % commit_every == 0:
            db.commit()
        active = [a for a in data.ads if not a.is_internal and a.first_seen_at.date() <= d
                  and a.last_seen_at.date() >= d - timedelta(days=2)]
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
            m["hidden_winners"] += 1 if is_hidden_winner(p) else 0
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
    ads = db.scalars(select(Ad).where(Ad.advertiser_id.is_not(None), Ad.is_internal.is_(False), ADS_ONLY)).all()
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
