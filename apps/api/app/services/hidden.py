"""Hidden Winners — the ONE definition used everywhere (page, dashboard, product cards, alerts, agent, snapshots).
What the Ad Library actually shows: few sellers, but ads that keep running / get duplicated / multiply this week.
No internal sales data or comments needed. engine.score_product stores evaluate()'s result on the product
(features["hidden_score"], tags); compute() re-evaluates live for a market scope.

  score = 40% strongest ad (force: days running + variants + placements)
        + 25% rarity (fewer advertisers → higher)
        + 20% growth (new ads in the last 7 days)
        + 15% scaling (active duplicated variants)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..markets import ad_scope_filter
from ..models import ADS_ONLY, Ad, Product
from ..platforms import REMOVED_NETWORKS
from .adsignals import days_running

MAX_ADVERTISERS = 5  # "ít người chạy"
MIN_SCORE = 55       # 0 → everything qualifies (≈ 470 products), 60 → too few; 55 ≈ the top ~40 %

TAGS: dict[str, str] = {
    "fast_growing": "🔥 Đang tăng nhanh",   # ≥ 2 new ads in 7 days, or creatives +30 % week over week
    "proven": "⏳ Chạy lâu ≥ 90 ngày",       # an ad still running after 90 days = it pays for itself
    "scaling": "📈 Đang nhân bản",           # ≥ 5 active duplicated variants
    "rare": "💎 Chỉ 1 người bán",            # a single advertiser — nobody has copied it yet
    "new_market": "🌍 Thị trường mới",       # appeared in a new country this week
    "multi_market": "🗺 Nhiều thị trường",   # running in ≥ 3 countries
}


def evaluate(p: Product, ads: list[Ad], f: dict | None = None, now: datetime | None = None) -> dict | None:
    """One product -> its hidden-winner row, or None. `ads` may be all of the product's rows: listings, viral posts and
    our own ads are ignored here."""
    if p.category == "non_product":
        return None
    now = now or datetime.utcnow()
    week = now - timedelta(days=7)
    ads = [a for a in ads if a.channel in (None, "ads") and not a.is_internal and a.network not in REMOVED_NETWORKS]
    active = [a for a in ads if a.is_active]
    if not active:
        return None
    n_adv = len({a.advertiser_id or a.page_id for a in ads})
    if n_adv > MAX_ADVERTISERS:
        return None
    strongest = max(active, key=lambda a: a.force_score or 0)
    force = strongest.force_score or 0
    if force < 45:  # needs at least one strong ad (sonda tier "strong" or better)
        return None
    max_days = max(days_running(a, now) for a in active)
    new_7d = sum(1 for a in ads if a.first_seen_at and a.first_seen_at >= week)
    variants = sum(max((a.variants or 1) - 1, 0) for a in active)
    countries = {c for a in active for c in (a.countries or [a.country]) if c and c != "ALL"}
    f = f if f is not None else (p.features or {})
    rarity = (1 - (n_adv - 1) / MAX_ADVERTISERS) * 100
    score = round(0.40 * force + 0.25 * rarity + 0.20 * min(new_7d / 5, 1) * 100 + 0.15 * min(variants / 10, 1) * 100, 1)
    if score < MIN_SCORE:
        return None
    tags = []
    if new_7d >= 2 or f.get("creative_growth_7d", 0) >= 0.3:
        tags.append("fast_growing")
    if max_days >= 90:
        tags.append("proven")
    if variants >= 5:
        tags.append("scaling")
    if n_adv == 1:
        tags.append("rare")
    if f.get("new_markets_7d"):
        tags.append("new_market")
    if len(countries) >= 3:
        tags.append("multi_market")
    why = [f"{n_adv} người bán", f"ad mạnh nhất {force:.0f}/100 ({strongest.force_tier or '—'})", f"chạy {max_days} ngày"]
    if new_7d:
        why.append(f"+{new_7d} ads / 7 ngày")
    if variants:
        why.append(f"{variants} biến thể đang chạy")
    return {"product": p, "score": score, "tags": tags, "why": why, "advertisers": n_adv, "active_ads": len(active),
            "max_days": max_days, "new_7d": new_7d, "variants": variants, "force": force,
            "strongest_ad_id": strongest.id, "countries": sorted(countries)}


def compute(db: Session, scope: str | None = None) -> list[dict]:
    """Live hidden winners for a market scope (only the ads that reached that scope count)."""
    now = datetime.utcnow()
    by_p: dict[int, list[Ad]] = defaultdict(list)
    q = select(Ad).where(Ad.product_id.is_not(None), ADS_ONLY)
    if (flt := ad_scope_filter(scope)) is not None:  # "ANY" = no market filter
        q = q.where(flt)
    for a in db.scalars(q):
        by_p[a.product_id].append(a)
    if not by_p:
        return []
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(by_p))))}
    out = [r for pid, ads in by_p.items() if pid in prods and (r := evaluate(prods[pid], ads, now=now))]
    out.sort(key=lambda r: -r["score"])
    return out
