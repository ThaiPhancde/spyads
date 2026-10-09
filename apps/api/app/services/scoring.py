"""Scoring Engine (blueprint §8, §10-11, §19).

All scores are deterministic formulas over the feature store — AI never sets a
score (§20). Every score returns its component breakdown so the UI / AI agent can
explain "why score = 89".
"""
import math

from .enrichment import to_usd


def lin(x, lo, hi) -> float:
    """Linear normalize x from [lo, hi] → [0, 100] (clamped)."""
    if x is None:
        return 50.0
    if hi == lo:
        return 100.0 if x >= hi else 0.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _weighted(parts: dict[str, tuple[float, float]]) -> float:
    return sum(w * v for w, v in parts.values()) / sum(w for w, _ in parts.values())


def _breakdown(parts: dict[str, tuple[float, float]]) -> dict:
    return {k: {"weight": w, "value": round(v, 1)} for k, (w, v) in parts.items()}


# ------------------------------------------------------------ Saturation (§11)
def saturation_score(f: dict) -> tuple[float, str, dict]:
    accel_ratio = (f["new_ads_7d"] - f["new_ads_prev_7d"]) / max(1, f["new_ads_prev_7d"])
    # Calibrated on the real DB 2026-10-07, p50/p90/p99: advertiser_count 1/1/3 · active_ads 1/2/12 · store_count 1/1/2
    #   · creative_duplication 0/0.33/0.8 · advertisers_at_primary_market 0/0/1 (max 7). Ranges put p99 ≈ 100.
    # Sellers at the priority market: 2-10 is the healthy band (validated, not crowded), >15 is a crowded market.
    adv_pm = f.get("advertisers_at_primary_market") or f["advertiser_count"]
    adv_bell = 15.0 if adv_pm < 2 else 0.0 if adv_pm <= 10 else lin(adv_pm, 10, 20)
    parts = {
        "advertiser_density": (0.25, adv_bell),
        "creative_duplication": (0.15, lin(f["creative_duplication"], 0, 0.45)),
        "ad_volume": (0.15, lin(f["active_ads"], 2, 12)),
        "store_count": (0.15, lin(f["store_count"], 1, 4)),
        "keyword_competition": (0.0, (f.get("keyword_competition") or 0.5) * 100),  # ponytail: no source fills this yet
        "price_compression": (0.0, lin(-(f.get("price_trend") or 0), 0, 0.25)),  # ponytail: no source fills this yet
        "growth_deceleration": (0.10, lin(-accel_ratio, 0, 0.6) if f["new_ads_prev_7d"] >= 3 else 0),
    }
    s = _weighted(parts)
    declining = (f["new_ads_prev_7d"] >= 5 and f["new_ads_7d"] < 0.5 * f["new_ads_prev_7d"]
                 and f["removed_ads_7d"] > f["new_ads_7d"])
    if declining and s >= 35:
        state = "Declining"
    elif s < 25:
        state = "Low Saturation"
    elif s < 45:
        state = "Growing"
    elif s < 68:
        state = "Competitive"
    else:
        state = "Highly Saturated"
    return round(s, 1), state, _breakdown(parts)


# ------------------------------------------------------------ External Win (§8)
def external_win_score(f: dict, saturation: float) -> tuple[float, dict]:
    sentiment = lin(f["positive_comment_rate"] - f["negative_comment_rate"], -0.5, 0.7) if f["comments"] >= 5 else 50
    parts = {
        "ad_longevity": (0.15, 0.4 * lin(f.get("force_top", 0), 15, 70) + 0.3 * lin(f["ads_running_30d"] / max(1, f["active_ads"]), 0, 0.4)
                         + 0.3 * lin(f["ads_running_30d"], 0, 20)),
        "creative_velocity": (0.15, 0.5 * lin(f["new_ads_7d"], 0, 30) + 0.5 * lin(f["creative_growth_7d"], 0, 0.8)),
        "advertiser_growth": (0.10, 0.6 * lin(f["advertiser_growth"], 0, 0.4) + 0.4 * lin(f["new_advertisers_7d"], 0, 6)),
        "advertiser_diversity": (0.10, lin(1 - f["advertiser_hhi"], 0, 0.85)),
        "geographic_expansion": (0.10, 0.7 * lin(f["market_count"], 1, 5) + 0.3 * lin(len(f["new_markets_7d"]), 0, 2)),
        "traffic_momentum": (0.0, lin(f["traffic_growth"], -20, 50) if f["store_count"] else 40),  # ponytail: no source fills this yet
        "engagement_quality": (0.10, lin(f["engagement_per_ad"], 0, 400)),  # purchase_intent_rate dropped: no comments source yet
        "store_momentum": (0.10, 0.5 * lin(f["store_count"], 0, 12) + 0.5 * lin(f["new_stores_30d"], 0, 5)),
        "comment_sentiment": (0.0, sentiment),  # ponytail: no source fills this yet
        "search_trend": (0.0, lin(f["search_trend"], -30, 60)),  # ponytail: no source fills this yet
    }
    xp = cross_platform_signal(f)
    if xp is not None:  # only when the product really shows up elsewhere — absence of evidence is not a penalty
        parts["cross_platform"] = (0.12, xp)
    raw = _weighted(parts)
    penalty = max(0.0, saturation - 60) * 0.6
    b = _breakdown(parts)
    b["saturation_penalty"] = {"weight": -1, "value": round(-penalty, 1)}
    return round(max(0.0, min(100.0, raw - penalty)), 1), b


def cross_platform_signal(f: dict) -> float | None:
    """China-source demand (sold / reviews) + viral reach + number of networks it runs on."""
    if not (f.get("listings") or f.get("organic_posts")):
        return None
    demand = max(lin(f.get("reviews_total"), 50, 20000), lin(f.get("sold_total"), 100, 20000))
    viral = lin(f.get("organic_views"), 100_000, 20_000_000)
    spread = lin(len(f.get("networks") or []), 1, 5)
    return 0.5 * demand + 0.25 * viral + 0.25 * spread


# ------------------------------------------------------------ Rarity & Rare Winner (§10)
def rarity_score(f: dict) -> tuple[float, dict]:
    # same DB percentiles as saturation_score: p99 advertiser_count 3 · active_ads 12 · store_count 2
    parts = {
        "low_advertiser_density": (0.35, 100 - lin(f["advertiser_count"], 1, 4)),
        "low_ad_density": (0.25, 100 - lin(f["active_ads"], 1, 12)),
        "low_store_density": (0.20, 100 - lin(f["store_count"], 1, 4)),
        "low_keyword_competition": (0.0, 100 - (f.get("keyword_competition") or 0.5) * 100),  # ponytail: no source fills this yet
    }
    return round(_weighted(parts), 1), _breakdown(parts)


def growth_velocity(f: dict) -> float:
    # traffic_growth dropped (ponytail: no source fills this yet) — weights renormalised
    return round(0.6 * lin(f["creative_growth_7d"], 0, 1.2) + 0.4 * lin(f["advertiser_growth"], 0, 0.8), 1)


def margin_potential(product, f: dict) -> float:
    if product.price and product.cost:
        return lin(1 - product.cost / product.price, 0.2, 0.75)
    sell = f.get("avg_price") or to_usd(product.price, product.currency)  # features.avg_price is already USD
    if f.get("supplier_price_usd") and sell:  # AliExpress price ≈ landed cost floor (shipping/fees not included)
        return lin(1 - f["supplier_price_usd"] * 1.6 / sell, 0.2, 0.75)
    usd = sell
    if usd is None:
        return 55.0
    # Mid-ticket impulse products leave the best room for COD margins
    return 75.0 if 15 <= usd <= 60 else 55.0 if usd < 15 else 60.0


def rare_winner_score(win: float, rarity: float, growth: float, market_fit: float, margin: float) -> float:
    """§10.2 multiplicative form (weighted geometric mean so one weak factor drags the score down)."""
    factors = [(win, 0.35), (rarity, 0.25), (growth, 0.2), (market_fit, 0.1), (margin, 0.1)]
    log_sum = sum(w * math.log(max(v, 1) / 100) for v, w in factors)
    return round(100 * math.exp(log_sum), 1)


# ------------------------------------------------------------ Confidence (§19)
def confidence_score(f: dict) -> tuple[float, dict]:
    parts = {
        "ad_volume": (0.25, lin(f["total_ads"], 1, 80)),
        "advertisers": (0.20, lin(f["advertiser_count"], 1, 15)),
        "history_days": (0.20, lin(f["data_days"], 3, 60)),
        "comments": (0.0, lin(math.log10(f["comments"] + 1), 0, 3.5)),  # ponytail: no source fills this yet
        "markets": (0.10, lin(f["market_count"], 1, 4)),
        "sources": (0.10, lin(f["source_count"], 1, 3)),
    }
    return round(_weighted(parts), 1), _breakdown(parts)


def confidence_label(c: float) -> str:
    return "High" if c >= 70 else "Medium" if c >= 45 else "Low"


def win_label(win: float) -> str:
    # Never "Guaranteed Winner" (§8)
    if win >= 75:
        return "Likely Winner"
    if win >= 60:
        return "Potential Winner"
    if win >= 45:
        return "Mixed Signals"
    return "Weak Signals"


# ------------------------------------------------------------ Opportunity (§19)
def opportunity_score(win: float, saturation: float, growth: float, margin: float, confidence: float) -> tuple[float, float]:
    raw = 0.5 * win + 0.2 * (100 - saturation) + 0.15 * growth + 0.15 * margin
    adjustment = 0.4 + 0.6 * confidence / 100
    return round(raw, 1), round(raw * adjustment, 1)


def sentiment_score(f: dict) -> float | None:
    if not f["comments"]:
        return None
    return round(lin(f["positive_comment_rate"] - f["negative_comment_rate"], -1, 1), 1)
