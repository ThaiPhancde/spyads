"""Phase 4: per-MKT preference profile learned from Phase 3 behavior events + current Follows.

Pure SQL + arithmetic, no AI/provider calls. Learned and manual preferences are stored separately: the MKT's own edits
always win, a reset of one never touches the other. The worker persists stale profiles in a light batch; GET recomputes
in memory when the stored copy is stale so a read never writes.
"""
import asyncio
import hashlib
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .behavior import BehaviorEvent
from .db import Base, SessionLocal
from .markets import REGIONS
from .models import Ad, Advertiser, Product

MARKETS = ("OFW_PH", "PH", "SA", "ME", "US", "EU", "AU", "VN")
MARKET_LABEL = {"OFW_PH": "OFW Philippines (người Philippines ở nước ngoài)", "PH": "Philippines", "SA": "Saudi Arabia",
                "ME": "Trung Đông", "US": "Mỹ", "EU": "Châu Âu (+UK)", "AU": "Úc / NZ", "VN": "Việt Nam"}
# Behavior weights. Follow events weigh 0 here: the current Follow list is used directly (state, not history).
WEIGHTS = {"ad_view": 0.2, "ad_detail_view": 0.5, "ad_save": 2.0, "ad_unsave": -2.0, "ad_favorite": 3.0,
           "ad_unfavorite": -3.0, "test_start": 2.0, "test_success": 6.0, "test_fail": -1.0, "test_inconclusive": 0.5,
           "ad_skip": -0.5, "ad_not_interested": -3.0, "advertiser_follow": 0.0, "advertiser_unfollow": 0.0}
FOLLOW_WEIGHT = 2.0  # per followed advertiser, spread over what that advertiser advertises
TAGALOG = {"tl", "fil", "tagalog", "filipino"}
COUNTRY_REGION = {c: r for r, cs in REGIONS.items() for c in cs if r not in ("WW",)}
MAX_EVENTS = 20000  # ponytail: newest 20k events per MKT; aggregate in SQL if one MKT ever exceeds it


def half_life_days():
    return float(os.getenv("PREFERENCE_HALF_LIFE_DAYS", "30"))


def max_age_days():
    return float(os.getenv("PREFERENCE_MAX_AGE_DAYS", "365"))


def default_markets():
    raw = os.getenv("PREFERENCE_DEFAULT_MARKETS", "OFW_PH,PH,SA,US,EU")
    return [m for m in (x.strip().upper() for x in raw.split(",")) if m in MARKETS]


class PreferenceProfile(Base):
    __tablename__ = "mkt_preference_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    learned: Mapped[dict] = mapped_column(JSON, default=dict)
    manual: Mapped[dict] = mapped_column(JSON, default=dict)
    learned_reset_at: Mapped[datetime | None] = mapped_column(DateTime)  # events before this are ignored
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _country(ad_country, ad_countries, product_country):
    for c in [ad_country, *(ad_countries or []), product_country]:
        if c and c.upper() != "ALL":
            return c.upper()[:2]
    return None


def _markets(country, language):
    region = COUNTRY_REGION.get(country)
    out = [region] if region else []
    if country == "SA":
        out.append("SA")
    if region == "ME" and (language or "").lower() in TAGALOG:
        out.append("OFW_PH")  # Filipino-language ads running in the Gulf = OFW audience
    return out


def _top(scores, n=10):
    pos = sorted(((k, v) for k, v in scores.items() if v > 0), key=lambda kv: -kv[1])[:n]
    peak = pos[0][1] if pos else 1
    return [{"key": k, "score": round(v / peak, 3), "weight": round(v, 3)} for k, v in pos]


def _price_range(samples):
    """Weighted 25–75 percentile in the currency with the most positive weight; None below 3 samples."""
    by_cur = defaultdict(list)
    for price, cur, w in samples:
        if price and price > 0 and cur and w > 0:
            by_cur[cur.upper()].append((price, w))
    if not by_cur:
        return None
    cur, rows = max(by_cur.items(), key=lambda kv: sum(w for _, w in kv[1]))
    if len(rows) < 3:
        return None
    rows.sort()
    total = sum(w for _, w in rows)

    def q(f):
        acc = 0.0
        for p, w in rows:
            acc += w
            if acc >= f * total:
                return round(p, 2)
        return round(rows[-1][0], 2)
    return {"currency": cur, "min": q(0.25), "median": q(0.5), "max": q(0.75), "samples": len(rows)}


def follows(db, uid):
    from .workspace.models import Follow
    return sorted(db.scalars(select(Follow.advertiser_id).where(Follow.user_id == uid)))


def _follow_hash(ids):
    return hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:16]


def compute(db, uid, reset_at=None, now=None):
    now = now or datetime.utcnow()
    since = now - timedelta(days=max_age_days())
    if reset_at and reset_at > since:
        since = reset_at
    rows = db.execute(
        select(BehaviorEvent.id, BehaviorEvent.kind, BehaviorEvent.created_at, BehaviorEvent.advertiser_id,
               Ad.country.label("ad_country"), Ad.countries, Ad.language.label("ad_language"), Product.category,
               Product.country.label("product_country"), Product.language.label("product_language"),
               Product.price, Product.currency)
        .outerjoin(Ad, Ad.id == BehaviorEvent.ad_id)
        .outerjoin(Product, Product.id == BehaviorEvent.product_id)
        .where(BehaviorEvent.user_id == uid, BehaviorEvent.created_at >= since)
        .order_by(BehaviorEvent.id.desc()).limit(MAX_EVENTS)).all()
    dims = {k: defaultdict(float) for k in ("categories", "countries", "markets", "languages", "advertisers")}
    prices, tests = [], {"success": 0, "fail": 0, "inconclusive": 0, "by_category": {}}
    hl, last_id = half_life_days(), 0
    for r in rows:
        last_id = max(last_id, r.id)
        base = WEIGHTS.get(r.kind, 0.0)
        if r.kind.startswith("test_") and r.kind != "test_start":
            outcome = r.kind[5:]
            tests[outcome] += 1
            if r.category:
                tests["by_category"].setdefault(r.category, {"success": 0, "fail": 0, "inconclusive": 0})[outcome] += 1
        if not base:
            continue
        w = base * 0.5 ** (max(0.0, (now - r.created_at).total_seconds()) / 86400 / hl)
        lang = (r.ad_language or r.product_language or "").lower() or None
        country = _country(r.ad_country, r.countries, r.product_country)
        if r.category:
            dims["categories"][r.category] += w
        if country:
            dims["countries"][country] += w
        for m in _markets(country, lang):
            dims["markets"][m] += w
        if lang:
            dims["languages"][lang] += w
        if r.advertiser_id:
            dims["advertisers"][r.advertiser_id] += w
        prices.append((r.price, r.currency, w))
    followed = follows(db, uid)
    for adv in followed:  # current Follows: what each followed advertiser actually advertises
        dims["advertisers"][adv] += FOLLOW_WEIGHT
        mix = db.execute(select(Product.category, Ad.country, Ad.language, func.count())
                         .join(Product, Product.id == Ad.product_id, isouter=True)
                         .where(Ad.advertiser_id == adv).group_by(Product.category, Ad.country, Ad.language)).all()
        total = sum(m[3] for m in mix) or 1
        for cat, country, lang, n in mix:
            share = FOLLOW_WEIGHT * n / total
            country = _country(country, None, None)
            if cat:
                dims["categories"][cat] += share
            if country:
                dims["countries"][country] += share
            for m in _markets(country, lang):
                dims["markets"][m] += share
            if lang:
                dims["languages"][lang.lower()] += share
    advertisers = _top(dims.pop("advertisers"))
    names = dict(db.execute(select(Advertiser.id, Advertiser.name).where(Advertiser.id.in_([a["key"] for a in advertisers]))).all())
    for a in advertisers:
        a["name"] = names.get(a["key"])
    return {**{k: _top(v) for k, v in dims.items()}, "advertisers": advertisers, "followed_advertisers": followed,
            "price_range": _price_range(prices), "tests": tests, "events_used": len(rows), "last_event_id": last_id,
            "follow_hash": _follow_hash(followed), "half_life_days": hl}


def is_stale(db, uid, p, now=None):
    if p is None or p.computed_at is None:
        return True
    now = now or datetime.utcnow()
    if now - p.computed_at > timedelta(hours=24):  # decay moves scores even without new events
        return True
    newest = db.scalar(select(func.max(BehaviorEvent.id)).where(BehaviorEvent.user_id == uid)) or 0
    return newest > p.last_event_id or _follow_hash(follows(db, uid)) != (p.learned or {}).get("follow_hash")


def refresh(db, uid):
    """Recompute and persist (caller commits)."""
    p = db.get(PreferenceProfile, uid) or PreferenceProfile(user_id=uid, manual={})
    learned = compute(db, uid, p.learned_reset_at)
    p.learned, p.last_event_id, p.computed_at, p.updated_at = learned, learned["last_event_id"], datetime.utcnow(), datetime.utcnow()
    db.add(p)
    return p


def effective(learned, manual):
    """Manual choices first, learned signal fills the gaps, defaults only when there is neither."""
    keys = lambda name: [x["key"] for x in learned.get(name) or []]
    excluded = set(manual.get("excluded_categories") or [])
    markets = manual.get("priority_markets") or keys("markets") or default_markets()
    categories = list(dict.fromkeys((manual.get("categories") or []) + [c for c in keys("categories") if c not in excluded]))
    price = learned.get("price_range")
    if manual.get("price_min") is not None or manual.get("price_max") is not None:
        price = {"currency": manual.get("currency") or (price or {}).get("currency"),
                 "min": manual.get("price_min"), "max": manual.get("price_max"), "manual": True}
    return {"markets": markets, "categories": categories, "excluded_categories": sorted(excluded),
            "countries": manual.get("countries") or keys("countries"),
            "languages": manual.get("languages") or keys("languages"),
            "price_range": price, "advertisers": learned.get("followed_advertisers") or []}


def view(db, uid, persisted_only=False):
    p = db.get(PreferenceProfile, uid)
    stale = is_stale(db, uid, p)
    learned = (p.learned if p else {}) if persisted_only or not stale else compute(db, uid, p.learned_reset_at if p else None)
    manual = (p.manual if p else {}) or {}
    has_signal = bool(learned.get("events_used") or learned.get("followed_advertisers"))
    return {"user_id": uid, "is_default": not has_signal and not manual, "learned": learned, "manual": manual,
            "effective": effective(learned, manual), "computed_at": p.computed_at if p and not stale else datetime.utcnow(),
            "learned_reset_at": p.learned_reset_at if p else None, "markets_available": MARKET_LABEL}


def validate(manual):
    out = {}
    for key in ("priority_markets",):
        if manual.get(key) is not None:
            bad = [m for m in manual[key] if m not in MARKETS]
            if bad:
                raise HTTPException(422, f"Thị trường không hỗ trợ: {', '.join(bad)}")
            out[key] = list(dict.fromkeys(manual[key]))
    if manual.get("countries") is not None:
        cs = [c.strip().upper() for c in manual["countries"]]
        if any(not re.fullmatch(r"[A-Z]{2}", c) for c in cs):
            raise HTTPException(422, "Mã quốc gia phải là ISO-2, ví dụ PH, SA, US")
        out["countries"] = list(dict.fromkeys(cs))
    if manual.get("languages") is not None:
        out["languages"] = list(dict.fromkeys(l.strip().lower() for l in manual["languages"] if l.strip()))
    for key in ("categories", "excluded_categories"):
        if manual.get(key) is not None:
            out[key] = list(dict.fromkeys(c.strip() for c in manual[key] if c.strip()))
    lo, hi = manual.get("price_min"), manual.get("price_max")
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(422, "Giá tối thiểu lớn hơn giá tối đa")
    for key in ("price_min", "price_max"):
        if manual.get(key) is not None:
            out[key] = manual[key]
    if manual.get("currency"):
        out["currency"] = manual["currency"].upper()
    return out


def refresh_stale(limit=200):
    """Light batch for the worker: persist profiles of active users whose behavior/follows/decay changed."""
    from .workspace.models import User
    done = 0
    with SessionLocal() as db:
        uids = db.scalars(select(User.id).where(User.status == "active").order_by(User.id)).all()
        for uid in uids:
            if done >= limit:
                break
            if is_stale(db, uid, db.get(PreferenceProfile, uid)):
                refresh(db, uid)
                db.commit()
                done += 1
    return done


async def loop():
    every = float(os.getenv("PREFERENCE_REFRESH_MIN", "15"))
    if not every:
        return
    log = logging.getLogger("app.preferences")
    while True:
        try:
            n = await asyncio.to_thread(refresh_stale)
            if n:
                log.info("preferences: %s profiles refreshed", n)
        except Exception:
            log.exception("preferences refresh failed")
        await asyncio.sleep(every * 60)
