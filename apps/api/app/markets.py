"""Target markets & regions.

TARGET_MARKETS (.env) decides which countries count in every "all markets" aggregate: dashboards, radar,
search, rankings. Ads from other countries (e.g. VN) are still stored and visible when that country is
picked explicitly — they just never leak into the global numbers.
"""
from __future__ import annotations

import os

REGIONS: dict[str, list[str]] = {
    "ME": ["SA", "AE", "KW", "QA", "OM", "BH", "JO", "EG", "IQ"],
    "US": ["US"],
    "EU": ["GB", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "SE", "DK", "FI", "IE", "PT", "PL", "CZ", "RO", "GR"],
    "AU": ["AU", "NZ"],
    "WW": ["ALL"],  # ads found by worldwide Ad Library searches (country not disclosed)
}
REGION_LABEL = {"ME": "Trung Đông", "US": "Mỹ", "EU": "Châu Âu (+UK)", "AU": "Úc / NZ", "WW": "Toàn cầu"}
DEFAULT_TARGETS = [c for r in ("ME", "US", "EU", "AU") for c in REGIONS[r]]


def targets() -> list[str]:
    raw = os.getenv("TARGET_MARKETS", "")
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    return codes or DEFAULT_TARGETS


def region_of(code: str | None) -> str | None:
    for r, cs in REGIONS.items():
        if code in cs:
            return r
    return None


def expand(scope: str | None) -> list[str] | None:
    """'ME' → its countries, 'SA' → ['SA'], '' / None → target markets, 'ANY' → no filter (None)."""
    if not scope:
        return targets()
    s = scope.upper()
    if s == "ANY":
        return None
    if s in REGIONS:
        return [c for c in REGIONS[s] if c in targets() or c == "ALL"] or REGIONS[s]
    return [c.strip() for c in s.split(",") if c.strip()]


def ad_in_targets(markets: list[str]) -> bool:
    t = set(targets()) | {"ALL"}
    return any(m in t for m in markets)


def ad_scope_filter(scope: str | None):
    """SQL filter for Ad rows. Default (no scope) = target markets via the precomputed `in_target` flag."""
    from sqlalchemy import or_

    from .models import Ad, ad_in_country

    if not scope:
        return Ad.in_target.is_(True)
    codes = expand(scope)
    if codes is None:
        return None
    return or_(*[ad_in_country(c) for c in codes])


def product_in_scope(p, scope: str | None) -> bool:
    markets = (p.features or {}).get("markets") or ([p.country] if p.country else [])
    if not scope:
        return bool(p.in_target) if p.in_target is not None else ad_in_targets(markets)
    codes = expand(scope)
    return codes is None or any(m in codes for m in markets)
