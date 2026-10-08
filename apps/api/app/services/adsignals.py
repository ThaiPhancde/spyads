"""Per-ad signals.

* force score — "índice de força" from sonda-imperial (MIT, © 2026 SaldanhaC3, fb_ads_scraper/scoring.py):
    time      = min(days_running / 180, 1) · 60
    variants  = min(max(collation_count − 1, 0) / 9, 1) · 25
    platforms = min(len(publisher_platforms) / 4, 1) · 15
    score     = sum, × 0.7 when the ad is no longer running
  tiers: ≥70 legendary · ≥45 strong · ≥20 regular · else testing
  An ad that survives months, in many variants, on every placement is being paid for because it sells.
* variation key — same page + same normalized copy (NFKD, lower, no punctuation, 150 chars). Several live
  ads sharing it = the advertiser is deliberately scaling that message.
* in_target — the ad reached a TARGET_MARKETS country.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..markets import ad_in_targets
from ..models import Ad, ad_markets

TIERS = [(70, "legendary"), (45, "strong"), (20, "regular"), (0, "testing")]


def days_running(a: Ad, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    end = now if a.is_active else (a.inactive_at or a.last_seen_at or now)
    return max(0, (min(end, now) - a.first_seen_at).days) if a.first_seen_at else 0


def force(a: Ad, now: datetime | None = None) -> tuple[float, str]:
    t = min(days_running(a, now) / 180, 1) * 60
    v = min(max((a.variants or 1) - 1, 0) / 9, 1) * 25
    p = min(len(a.platforms or ([a.platform] if a.platform else [])) / 4, 1) * 15
    s = t + v + p
    if not a.is_active:
        s *= 0.7
    s = round(s, 1)
    return s, next(name for th, name in TIERS if s >= th)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^\w]+", " ", s).strip()[:150]


def variation_key(a: Ad) -> str | None:
    body = _norm(a.ad_text or a.title or a.landing_url or "")
    who = a.page_id or (str(a.advertiser_id) if a.advertiser_id else "")
    if not body or not who:
        return None
    return hashlib.sha1(f"{who}|{body}".encode()).hexdigest()[:16]


def refresh(db: Session, ad_ids: list[int] | None = None) -> int:
    """Recompute force / tier / variation key / in_target for the given ads (all when None)."""
    q = select(Ad)
    if ad_ids is not None:
        if not ad_ids:
            return 0
        q = q.where(Ad.id.in_(ad_ids))
    now = datetime.utcnow()
    n = 0
    for a in db.scalars(q):
        a.force_score, a.force_tier = force(a, now)
        a.variation_key = variation_key(a)
        markets = ad_markets(a)
        # a listing with no country (AliExpress, 1688) sells worldwide: it belongs to every target market
        a.in_target = ad_in_targets(markets) or (a.channel == "commerce" and not markets)
        n += 1
    db.flush()
    return n


def variation_stats(ads: list[Ad]) -> dict:
    """Groups of live ads sharing page + copy (≥2 = scaled message)."""
    groups = Counter(a.variation_key for a in ads if a.is_active and a.variation_key)
    big = [n for n in groups.values() if n >= 2]
    return {"groups": len(big), "max_group": max(big, default=1), "ads_in_groups": sum(big)}
