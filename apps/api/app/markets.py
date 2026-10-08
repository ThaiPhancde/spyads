"""Target markets & regions.

TARGET_MARKETS (.env) decides which countries count in every "all markets" aggregate: dashboards, radar,
search, rankings. Ads from other countries (e.g. VN) are still stored and visible when that country is
picked explicitly — they just never leak into the global numbers.
"""
from __future__ import annotations

import os

REGIONS: dict[str, list[str]] = {  # order = priority order (UI chips, region tables)
    "PH": ["PH"],
    "ME": ["SA", "AE", "KW", "QA", "OM", "BH", "JO", "EG", "IQ"],
    "US": ["US"],
    "EU": ["GB", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "SE", "DK", "FI", "IE", "PT", "PL", "CZ", "RO", "GR"],
    "AU": ["AU", "NZ"],
    "VN": ["VN"],
    "WW": ["ALL"],  # ads found by worldwide Ad Library searches (country not disclosed)
}
REGION_LABEL = {"PH": "Philippines", "ME": "Trung Đông", "US": "Mỹ", "EU": "Châu Âu (+UK)", "AU": "Úc / NZ", "VN": "Việt Nam", "WW": "Toàn cầu"}
DEFAULT_TARGETS = [c for r in ("PH", "ME", "US", "EU", "AU", "VN") for c in REGIONS[r]]


def priority_markets() -> set[str]:
    """Markets ranked above every other one (PRIORITY_MARKETS in .env, default PH)."""
    return {c.strip().upper() for c in os.getenv("PRIORITY_MARKETS", "PH").split(",") if c.strip()}


def priority_boost(markets: list[str]) -> float:
    """Opportunity-score bonus for products running in a priority market."""
    return float(os.getenv("PRIORITY_BOOST", "15")) if priority_markets() & {m.upper() for m in markets if m} else 0.0


def targets() -> list[str]:
    raw = os.getenv("TARGET_MARKETS", "")
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    return codes or DEFAULT_TARGETS


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


def known_country(a) -> str | None:
    """First real ISO-2 code of an Ad / AdRecord; 'ALL' (worldwide search) is a scope, not a country."""
    return next((c.upper()[:2] for c in [getattr(a, "country", None), *(getattr(a, "countries", None) or [])]
                 if c and c.upper() != "ALL"), None)


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
