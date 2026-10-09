"""Alert Engine (blueprint §29). Compares today's state with snapshots from ~7 days ago."""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ADS_ONLY, Ad, Advertiser, Alert, Product, ProductDailySnapshot, Vote
from .engine import is_hidden_winner
from .hidden import MIN_SCORE


# 1.062 of 1.227 alerts were hidden_winner and 140 milestone: daily noise that buried the rare real signals.
# The products already show in Hidden Winners / the product page; re-enable here if you want the push back.
MUTED = {"hidden_winner", "milestone"}


def _emit(db: Session, type_: str, severity: str, title: str, message: str, product_id=None, advertiser_id=None,
          data=None, period: str | None = None) -> bool:
    if type_ in MUTED:
        return False
    key = f"{type_}:{product_id or ''}:{advertiser_id or ''}:{period or datetime.utcnow().date().isoformat()}"
    if db.scalar(select(Alert.id).where(Alert.dedupe_key == key)):
        return False
    db.add(Alert(type=type_, severity=severity, title=title, message=message, product_id=product_id,
                 advertiser_id=advertiser_id, data=data or {}, dedupe_key=key, created_at=datetime.utcnow()))
    return True


def _week() -> str:
    y, w, _ = datetime.utcnow().date().isocalendar()
    return f"{y}W{w}"


def run_alerts(db: Session) -> int:
    n = 0
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    old = {s.product_id: s for s in db.scalars(select(ProductDailySnapshot).where(ProductDailySnapshot.date == week_ago))}
    yday = {s.product_id: s for s in db.scalars(select(ProductDailySnapshot).where(ProductDailySnapshot.date == today - timedelta(days=1)))}
    watched = set(db.scalars(select(Vote.product_id).distinct()))
    # an ad crossing 14 / 30 days running today (what marketers trust most) — one query, grouped per product
    marks = {str(today - timedelta(days=d)): d for d in (14, 30)}
    milestones: dict[int, set[int]] = {}
    for pid, fs in db.execute(select(Ad.product_id, func.date(Ad.first_seen_at)).where(
            Ad.is_active.is_(True), Ad.is_internal.is_(False), ADS_ONLY, Ad.product_id.is_not(None), func.date(Ad.first_seen_at).in_(list(marks)))):
        milestones.setdefault(pid, set()).add(marks[fs])

    for p in db.scalars(select(Product)).all():
        f = p.features or {}
        if not f:
            continue
        prev = old.get(p.id)
        name = f"{p.product_code} · {p.canonical_name}"

        # only on the transition into the hidden-winner set (980 repeats/day otherwise); state lives in features["hidden_alerted"]
        hidden = is_hidden_winner(p) and (f.get("hidden_score") or 0) >= MIN_SCORE
        if hidden != bool(f.get("hidden_alerted")):
            p.features = {**f, "hidden_alerted": hidden}
        if hidden and not f.get("hidden_alerted"):
            n += _emit(db, "hidden_winner", "info", f"💎 Hidden Winner: {p.canonical_name}",
                       f"{f['advertiser_count']} advertisers · Growth 7d {f['creative_growth_7d']:+.0%} · Win {p.win_score:.0f} · "
                       f"Rarity {p.rarity_score:.0f} · Opportunity {p.opportunity_score:.0f}",
                       p.id, period=_week(), data={"rare_winner_score": p.rare_winner_score})

        y = yday.get(p.id)
        if p.id in watched and y and f.get("advertiser_count", 0) > y.advertisers:
            n += _emit(db, "new_entrant", "warn", f"🆕 New entrant: {name}",
                       f"{y.advertisers} → {f['advertiser_count']} advertisers kể từ hôm qua.", p.id,
                       data={"from": y.advertisers, "to": f["advertiser_count"]})

        for days in sorted(milestones.get(p.id, ())):
            if p.in_target:
                n += _emit(db, "milestone", "info", f"⏳ {days} ngày running: {name}",
                           f"Một ad của sản phẩm vừa chạy đủ {days} ngày.", p.id, period=f"{today.isoformat()}:{days}", data={"days": days})

        if f.get("new_markets_7d") and p.win_score >= 55:
            n += _emit(db, "new_market", "info", f"🌍 New market: {name}",
                       f"Bắt đầu chạy ở {', '.join(f['new_markets_7d'])} trong 7 ngày qua.", p.id, period=_week())

        if f["new_ads_7d"] >= 10 and f["new_ads_7d"] >= 2 * max(1, f["new_ads_prev_7d"]):
            n += _emit(db, "creative_velocity_spike", "warn", f"🚀 Creative velocity spike: {name}",
                       f"{f['new_ads_7d']} ads mới 7d (tuần trước {f['new_ads_prev_7d']}).", p.id, period=_week())

        if prev and prev.avg_price and f.get("avg_price") and abs(f["avg_price"] / prev.avg_price - 1) >= 0.15:
            ch = f["avg_price"] / prev.avg_price - 1
            n += _emit(db, "price_change", "info", f"💲 Price change {ch:+.0%}: {name}",
                       f"Giá TB {prev.avg_price:.0f} → {f['avg_price']:.0f}.", p.id, period=_week())

        if f.get("traffic_growth", 0) >= 50 and f.get("store_count"):
            n += _emit(db, "store_traffic_spike", "info", f"📈 Store traffic spike: {name}",
                       f"Traffic store tăng {f['traffic_growth']:.0f}%.", p.id, period=_week())

        nr7, nrp = f.get("negative_rate_7d"), f.get("negative_rate_prev")
        if nr7 is not None and nrp is not None and nr7 - nrp >= 0.10 and f["comments"] >= 20:
            n += _emit(db, "negative_comments_increasing", "critical", f"⚠ Negative sentiment tăng: {name}",
                       f"Negative {nrp:.0%} → {nr7:.0%}.", p.id, period=_week(), data={"from": nrp, "to": nr7})

        if prev and p.saturation_score - prev.saturation_score >= 12:
            n += _emit(db, "saturation_increasing", "warn", f"📉 Saturation tăng: {name}",
                       f"Saturation {prev.saturation_score:.0f} → {p.saturation_score:.0f} ({p.saturation_state}).", p.id, period=_week())

    n += _competitor_alerts(db)
    db.flush()
    return n


def _competitor_alerts(db: Session) -> int:
    n = 0
    now = datetime.utcnow()
    rows = db.execute(
        select(Ad.advertiser_id, func.count(Ad.id))
        .where(Ad.first_seen_at > now - timedelta(days=7), Ad.is_internal.is_(False), ADS_ONLY, Ad.advertiser_id.is_not(None))
        .group_by(Ad.advertiser_id)
    ).all()
    for adv_id, new7 in rows:
        prev = db.scalar(select(func.count(Ad.id)).where(
            Ad.advertiser_id == adv_id, Ad.first_seen_at <= now - timedelta(days=7), Ad.first_seen_at > now - timedelta(days=14)))
        if new7 >= 8 and new7 >= 2 * max(prev, 1):
            adv = db.get(Advertiser, adv_id)
            n += _emit(db, "competitor_scaling", "warn", f"🏁 Competitor scaling: {adv.name}",
                       f"{new7} ads mới trong 7 ngày (tuần trước {prev}).", advertiser_id=adv_id, period=_week())
    return n
