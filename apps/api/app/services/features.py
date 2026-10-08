"""Feature Store (blueprint §2, §6, §18).

`load_product_data` pulls every child row for a product once; `compute_features`
is a pure function of that data and an `as_of` date, so the same code powers the
live score and historical snapshot backfills.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..markets import priority_markets
from ..models import Ad, ad_markets, AdMetric, Comment, Experiment, Order, Product, Store
from ..platforms import REMOVED_NETWORKS
from .enrichment import to_usd


@dataclass
class ProductData:
    product: Product
    ads: list[Ad] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    stores: list[Store] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    metrics: list = field(default_factory=list)


def load_product_data(db: Session, product: Product) -> ProductData:
    pid = product.id
    return ProductData(
        product=product,
        ads=list(db.scalars(select(Ad).where(Ad.product_id == pid))),
        comments=list(db.scalars(select(Comment).where(Comment.product_id == pid))),
        stores=list(db.scalars(select(Store).where(Store.product_id == pid))),
        orders=list(db.scalars(select(Order).where(Order.product_id == pid))),
        experiments=list(db.scalars(select(Experiment).where(Experiment.product_id == pid))),
        metrics=list(db.scalars(select(AdMetric).where(AdMetric.product_id == pid))),
    )


def _end(d: date) -> datetime:
    return datetime.combine(d, time.max)


def _stopped_at(ad: Ad) -> datetime | None:
    if ad.is_active:
        return None
    return ad.inactive_at or ad.last_seen_at


def _active_at(ad: Ad, d: date) -> bool:
    """Running on day d: started by then and not (verified) stopped before it. Live ads are confirmed by the
    liveness job, so silence alone no longer makes an ad look removed."""
    if ad.first_seen_at > _end(d):
        return False
    stop = _stopped_at(ad)
    return stop is None or stop >= _end(d - timedelta(days=1))


def _safe_div(a, b, default=0.0):
    return a / b if b else default


def _variation(active: list) -> dict:
    from .adsignals import variation_stats

    return variation_stats(active)


def compute_features(data: ProductData, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    end = _end(as_of)
    d7, d14, d30 = end - timedelta(days=7), end - timedelta(days=14), end - timedelta(days=30)
    p = data.product

    seen_by = [a for a in data.ads if not a.is_internal and a.first_seen_at <= end]
    # ad metrics come from ad libraries only: a marketplace listing or a viral post is not an ad (platforms.py)
    ext_ads = [a for a in seen_by if a.channel in (None, "ads") and a.network not in REMOVED_NETWORKS]
    active = [a for a in ext_ads if _active_at(a, as_of)]
    new_7d = [a for a in ext_ads if a.first_seen_at > d7]
    new_prev7 = [a for a in ext_ads if d14 < a.first_seen_at <= d7]
    new_30d = [a for a in ext_ads if a.first_seen_at > d30]
    removed_7d = [a for a in ext_ads if _stopped_at(a) and d7 < _stopped_at(a) <= end]
    running_30d = [a for a in active if (end - a.first_seen_at).days >= 30]

    adv_first: dict[int, datetime] = {}
    for a in ext_ads:
        if a.advertiser_id:
            adv_first[a.advertiser_id] = min(adv_first.get(a.advertiser_id, a.first_seen_at), a.first_seen_at)
    active_adv = Counter(a.advertiser_id for a in active if a.advertiser_id)
    advertisers = len(active_adv) or len(adv_first)
    new_adv_7d = sum(1 for t in adv_first.values() if t > d7)
    hhi = sum((c / sum(active_adv.values())) ** 2 for c in active_adv.values()) if active_adv else 1.0

    country_first: dict[str, datetime] = {}
    for a in ext_ads:
        for c in ad_markets(a):
            country_first[c] = min(country_first.get(c, a.first_seen_at), a.first_seen_at)
    markets = sorted({c for a in active for c in ad_markets(a)}) or sorted(country_first)
    new_markets_7d = [c for c, t in country_first.items() if t > d7]

    fps = [a.creative_fingerprint for a in active if a.creative_fingerprint]
    duplication = 1 - _safe_div(len(set(fps)), len(fps), 1.0) if fps else 0.0

    stores = [s for s in data.stores if s.first_seen_at <= end]
    new_stores_30d = [s for s in stores if s.first_seen_at > d30]
    traffic = sum(s.estimated_traffic or 0 for s in stores)
    traffic_growth = _safe_div(sum((s.traffic_growth or 0) * (s.estimated_traffic or 1) for s in stores),
                               sum((s.estimated_traffic or 1) for s in stores), 0.0)

    # prices in USD: ads of one product come in PHP / SAR / AUD … (readers: margin_potential, internal AOV, alerts ratio)
    prices_recent = [to_usd(a.price, a.currency) for a in ext_ads if a.price and a.first_seen_at > d14]
    prices_old = [to_usd(a.price, a.currency) for a in ext_ads if a.price and a.first_seen_at <= d14]
    avg_price = _safe_div(sum(prices_recent + prices_old), len(prices_recent + prices_old), None) if (prices_recent or prices_old) else to_usd(p.price, p.currency)
    price_trend = (_safe_div(sum(prices_recent), len(prices_recent)) / _safe_div(sum(prices_old), len(prices_old)) - 1) \
        if prices_recent and prices_old else 0.0

    engagement = sum((a.likes or 0) + 3 * (a.comments_count or 0) + 5 * (a.shares or 0) for a in active)

    # ---- comments
    comments = [c for c in data.comments if c.created_at <= end and c.overall]
    ncom = len(comments)
    pos = sum(1 for c in comments if c.overall == "positive")
    neg = sum(1 for c in comments if c.overall == "negative")
    intent_high = sum(1 for c in comments if c.purchase_intent == "high")
    complaints = sum(1 for c in comments if any(v == "negative" for v in (c.aspects or {}).values()))
    c7 = [c for c in comments if c.created_at > d7]
    cprev = [c for c in comments if d30 < c.created_at <= d7]
    neg_rate_7d = _safe_div(sum(1 for c in c7 if c.overall == "negative"), len(c7), None) if c7 else None
    neg_rate_prev = _safe_div(sum(1 for c in cprev if c.overall == "negative"), len(cprev), None) if cprev else None

    var = _variation(active)
    fp_groups = Counter(fps)
    prim = priority_markets()
    first_seen = min((a.first_seen_at for a in ext_ads), default=p.first_seen_at)
    data_days = max(0, (end - first_seen).days)
    sources = {a.source for a in ext_ads} | ({"stores"} if stores else set()) | ({"comments"} if comments else set())

    feats = {
        "as_of": as_of.isoformat(),
        "total_ads": len(ext_ads),
        "active_ads": len(active),
        "new_ads_7d": len(new_7d),
        "new_ads_prev_7d": len(new_prev7),
        "new_ads_30d": len(new_30d),
        "removed_ads_7d": len(removed_7d),
        "ads_running_30d": len(running_30d),
        # per-ad strength (sonda-imperial) and deliberate scaling (same page + copy, several live ads)
        "force_top": round(sum(sorted((a.force_score or 0 for a in active), reverse=True)[:5]) / max(1, min(5, len(active))), 1),
        "force_max": max((a.force_score or 0 for a in active), default=0),
        "strong_ads": sum(1 for a in active if (a.force_score or 0) >= 45),
        **{f"variation_{k}": v for k, v in var.items()},
        # what marketers trust most: days the strongest ad has run, copies of the same creative, sellers at the priority market
        "longevity_days": max(((end - a.first_seen_at).days for a in active), default=0),
        "variants": max([var["max_group"], *fp_groups.values()]),  # largest group of live ads sharing page+copy or creative
        "advertisers_at_primary_market": len({a.advertiser_id or a.page_id for a in active if prim & set(ad_markets(a))}),
        "creative_count": len(set(fps)) or len(active),
        "creative_growth_7d": round(_safe_div(len(new_7d), max(3, len(active))), 3),  # 1 new ad on 1 ad is not +100 %
        "creative_growth_30d": round(_safe_div(len(new_30d), max(1, len(active) - len(new_30d))), 3),
        "creative_acceleration": len(new_7d) - len(new_prev7),
        "creative_duplication": round(duplication, 3),
        "advertiser_count": advertisers,
        "new_advertisers_7d": new_adv_7d,
        "advertiser_growth": round(_safe_div(new_adv_7d, max(1, advertisers - new_adv_7d)), 3),
        "advertiser_hhi": round(hhi, 3),
        "market_count": len(markets),
        "markets": markets,
        "new_markets_7d": new_markets_7d,
        "store_count": len(stores),
        "new_stores_30d": len(new_stores_30d),
        "estimated_traffic": round(traffic),
        "traffic_growth": round(traffic_growth, 1),
        "avg_price": round(avg_price, 2) if avg_price else None,
        "price_trend": round(price_trend, 3),
        "engagement_per_ad": round(_safe_div(engagement, len(active)), 1),
        "comments": ncom,
        "positive_comment_rate": round(_safe_div(pos, ncom), 3),
        "negative_comment_rate": round(_safe_div(neg, ncom), 3),
        "complaint_rate": round(_safe_div(complaints, ncom), 3),
        "purchase_intent_rate": round(_safe_div(intent_high, ncom), 3),
        "quality_complaint_rate": round(_safe_div(sum(1 for c in comments if (c.aspects or {}).get("quality") == "negative"), ncom), 3),
        "negative_rate_7d": round(neg_rate_7d, 3) if neg_rate_7d is not None else None,
        "negative_rate_prev": round(neg_rate_prev, 3) if neg_rate_prev is not None else None,
        "search_trend": p.search_trend or 0,
        "keyword_competition": p.keyword_competition if p.keyword_competition is not None else 0.5,
        "data_days": data_days,
        "source_count": len(sources),
    }
    feats.update(compute_channel_features(seen_by, end))
    feats.update(compute_internal_features(data, as_of))
    return feats


def compute_channel_features(rows: list, end: datetime) -> dict:
    """Cross-platform evidence: where else the product shows up. China-source demand (AliExpress sold / reviews),
    supplier price (AliExpress / 1688 / Taobao) and viral organic reach."""
    from ..platforms import REMOVED_NETWORKS
    from .enrichment import to_usd

    rows = [a for a in rows if a.network not in REMOVED_NETWORKS]
    listings = [a for a in rows if a.channel == "commerce"]
    organic = [a for a in rows if a.channel == "organic"]
    ads = [a for a in rows if a.channel in (None, "ads")]
    supplier = [to_usd(a.price, a.currency) for a in listings if a.price]
    return {
        "ad_networks": sorted({a.network or "meta" for a in ads}),
        "networks": sorted({a.network for a in rows if a.network}),
        "listings": len(listings),
        "marketplaces": sorted({a.network for a in listings if a.network}),
        "reviews_total": sum(a.review_count or 0 for a in listings),
        "rating_avg": round(sum(a.rating for a in listings if a.rating) / max(1, sum(1 for a in listings if a.rating)), 2)
        if any(a.rating for a in listings) else None,
        "sold_total": sum(a.sold_count or 0 for a in listings),
        "supplier_price_usd": round(min(supplier), 2) if supplier else None,
        "organic_posts": len(organic),
        "organic_views": sum(a.views or 0 for a in organic),
    }


def compute_internal_features(data: ProductData, as_of: date) -> dict:
    """First-party ads + CRM + COD/logistics features (§9, §16)."""
    end = _end(as_of)
    exps = [e for e in data.experiments if e.started_at <= as_of]
    orders = [o for o in data.orders if o.created_at <= end]
    if not exps and not orders and not data.metrics:
        return {"has_internal": False}

    metrics = [m for m in data.metrics if m.date <= as_of]
    m_spend = sum(m.spend for m in metrics)
    spend = max(sum(e.spend for e in exps), m_spend)  # experiments or live ad-account metrics, never both
    impressions = max(sum(e.impressions for e in exps), sum(m.impressions for m in metrics))
    clicks = max(sum(e.clicks for e in exps), sum(m.clicks for m in metrics))
    lpv = sum(e.landing_views for e in exps)
    ads_sub = sum(e.ads_submitted for e in exps)
    ads_rej = sum(e.ads_rejected for e in exps)

    n = len(orders)
    st = Counter(o.status for o in orders)
    confirmed = n - st["pending"] - st["cancelled"]
    shipped = st["shipped"] + st["delivered"] + st["refused"] + st["failed"] + st["returned"]
    delivered = st["delivered"] + st["returned"]
    refused = st["refused"]
    returned = st["returned"] + sum(1 for o in orders if o.refunded and o.status == "delivered")
    attempts = delivered + refused + st["failed"]

    delivered_orders = [o for o in orders if o.status == "delivered" and not o.refunded]
    revenue_booked = sum(o.amount for o in orders if o.status not in ("cancelled",))
    revenue_delivered = sum(o.amount for o in delivered_orders)
    cogs = sum(o.cogs for o in delivered_orders)
    ship_cost = sum(o.shipping_cost for o in orders if o.status in ("shipped", "delivered", "refused", "failed", "returned"))
    fees = sum((o.cod_fee or 0) + (o.sales_commission or 0) + (o.payment_fee or 0) for o in delivered_orders)         + sum(o.return_cost or 0 for o in orders if o.status in ("refused", "returned", "failed"))
    profit = revenue_delivered - cogs - ship_cost - fees - spend
    closed = delivered + refused + st["failed"]  # in-transit shipments are not failures (realtime §13)

    phones = Counter(o.customer_phone_hash for o in orders if o.customer_phone_hash)
    repeat_rate = _safe_div(sum(1 for c in phones.values() if c > 1), len(phones))

    reasons = Counter(o.refusal_reason for o in orders if o.refusal_reason)

    return {
        "has_internal": True,
        "experiments": len(exps),
        "internal_spend": round(spend, 2),
        "impressions": impressions,
        "clicks": clicks,
        "ctr": round(_safe_div(clicks, impressions), 4),
        "cpc": round(_safe_div(spend, clicks), 3),
        "orders": n,
        "confirmed_orders": confirmed,
        "shipped": shipped,
        "delivered": delivered,
        "refused": refused,
        "returned": returned,
        "revenue": round(revenue_booked, 2),
        "revenue_delivered": round(revenue_delivered, 2),
        "profit": round(profit, 2),
        "contribution_margin": round(_safe_div(profit, revenue_delivered, -1.0), 3),
        "gross_margin": round(_safe_div(revenue_delivered - cogs, revenue_delivered), 3) if revenue_delivered else None,
        "cpa": round(_safe_div(spend, n), 2) if n else None,
        "roas": round(_safe_div(revenue_booked, spend), 2) if spend else None,
        "mer": round(_safe_div(revenue_delivered, spend), 2) if spend else None,
        "cvr": round(_safe_div(n, lpv or clicks), 4),
        "aov": round(_safe_div(revenue_booked, n), 2) if n else None,
        "confirm_rate": round(_safe_div(confirmed, n), 3) if n else None,
        "delivery_rate": round(_safe_div(delivered, closed), 3) if closed else None,
        "in_transit": st["shipped"],
        "fees": round(fees, 2),
        "cost_per_delivered": round(_safe_div(spend, delivered), 2) if delivered else None,
        "refusal_rate": round(_safe_div(refused, attempts), 3) if attempts else None,
        "return_rate": round(_safe_div(returned, delivered), 3) if delivered else None,
        "repeat_rate": round(repeat_rate, 3),
        "ad_rejection_rate": round(_safe_div(ads_rej, ads_sub), 3) if ads_sub else None,
        "refusal_reasons": dict(reasons.most_common()),
    }
