"""Scoring Engine (blueprint §8-11, §16, §19).

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
    parts = {
        "advertiser_density": (0.25, lin(f["advertiser_count"], 5, 150)),
        "creative_duplication": (0.15, lin(f["creative_duplication"], 0.1, 0.6)),
        "ad_volume": (0.15, lin(f["active_ads"], 20, 600)),
        "store_count": (0.15, lin(f["store_count"], 3, 60)),
        "keyword_competition": (0.10, (f.get("keyword_competition") or 0.5) * 100),
        "price_compression": (0.10, lin(-(f.get("price_trend") or 0), 0, 0.25)),
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
        "traffic_momentum": (0.10, lin(f["traffic_growth"], -20, 50) if f["store_count"] else 40),
        "engagement_quality": (0.10, 0.5 * lin(f["engagement_per_ad"], 0, 400) + 0.5 * lin(f["purchase_intent_rate"], 0, 0.25)),
        "store_momentum": (0.10, 0.5 * lin(f["store_count"], 0, 12) + 0.5 * lin(f["new_stores_30d"], 0, 5)),
        "comment_sentiment": (0.05, sentiment),
        "search_trend": (0.05, lin(f["search_trend"], -30, 60)),
    }
    raw = sum(w * v for w, v in parts.values())
    penalty = max(0.0, saturation - 60) * 0.6
    b = _breakdown(parts)
    b["saturation_penalty"] = {"weight": -1, "value": round(-penalty, 1)}
    return round(max(0.0, min(100.0, raw - penalty)), 1), b


# ------------------------------------------------------------ Internal Win (§9)
def internal_win_score(f: dict, price_usd: float | None = None) -> tuple[float | None, dict]:
    if not f.get("has_internal") or not f.get("orders"):
        return None, {}
    aov, cpa = f.get("aov") or 0, f.get("cpa") or 0
    parts = {
        "contribution_margin": (0.20, lin(f["contribution_margin"], -0.1, 0.3)),
        "roas_mer": (0.15, 0.5 * lin(f.get("roas"), 1, 4) + 0.5 * lin(f.get("mer"), 0.8, 3)),
        "cpa": (0.10, 100 - lin(cpa / aov if aov else 1, 0.15, 0.6)),
        "cvr": (0.10, lin(f.get("cvr"), 0.005, 0.04)),
        # order amounts are booked in USD; compare against the market price converted to USD
        "aov": (0.10, lin(aov / price_usd, 0.9, 1.5) if price_usd else 50.0),
        "delivery_rate": (0.15, lin(f.get("delivery_rate"), 0.5, 0.9)),
        "return_refund": (0.10, 100 - lin(f.get("return_rate"), 0.02, 0.2)),
        "customer_sentiment": (0.05, lin(f["positive_comment_rate"] - f["negative_comment_rate"], -0.5, 0.7) if f["comments"] else 50),
        "repeat_order": (0.05, lin(f.get("repeat_rate"), 0, 0.15)),
    }
    return round(_weighted(parts), 1), _breakdown(parts)


def blend_win(external: float, internal: float | None, f: dict) -> tuple[float, dict]:
    """§9.1 — the more internal data, the less we rely on spy tools."""
    if internal is None:
        return external, {"external": 1.0, "internal": 0.0, "stage": "untested"}
    enough = f.get("orders", 0) >= 50 and f.get("internal_spend", 0) >= 500
    we, wi = (0.3, 0.7) if enough else (0.6, 0.4)
    return round(we * external + wi * internal, 1), {"external": we, "internal": wi, "stage": "enough_data" if enough else "little_data"}


# ------------------------------------------------------------ Rarity & Rare Winner (§10)
def rarity_score(f: dict) -> tuple[float, dict]:
    parts = {
        "low_advertiser_density": (0.35, 100 - lin(f["advertiser_count"], 3, 60)),
        "low_ad_density": (0.25, 100 - lin(f["active_ads"], 5, 250)),
        "low_store_density": (0.20, 100 - lin(f["store_count"], 2, 40)),
        "low_keyword_competition": (0.20, 100 - (f.get("keyword_competition") or 0.5) * 100),
    }
    return round(_weighted(parts), 1), _breakdown(parts)


def growth_velocity(f: dict) -> float:
    return round(0.5 * lin(f["creative_growth_7d"], 0, 1.2) + 0.3 * lin(f["advertiser_growth"], 0, 0.8)
                 + 0.2 * lin(f["traffic_growth"], 0, 120), 1)


def margin_potential(product, f: dict) -> float:
    if f.get("gross_margin") is not None:
        return lin(f["gross_margin"], 0.2, 0.75)
    if product.price and product.cost:
        return lin(1 - product.cost / product.price, 0.2, 0.75)
    usd = to_usd(f.get("avg_price") or product.price, product.currency)
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
        "comments": (0.15, lin(math.log10(f["comments"] + 1), 0, 3.5)),
        "markets": (0.10, lin(f["market_count"], 1, 4)),
        "sources": (0.10, lin(f["source_count"], 1, 3)),
    }
    s = _weighted(parts)
    if f.get("has_internal") and f.get("orders", 0) >= 30:
        s = min(100.0, s + 10)
    return round(s, 1), _breakdown(parts)


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


# ------------------------------------------------------------ Customer rejection (§16)
def customer_rejection_score(f: dict) -> float | None:
    if not f.get("has_internal") or not f.get("orders"):
        return None
    reasons = f.get("refusal_reasons") or {}
    total_r = sum(reasons.values())
    trust_share = (reasons.get("fake_order", 0) + reasons.get("trust_issue", 0) + reasons.get("expectation_mismatch", 0)) / total_r if total_r else 0
    parts = {
        "refusal": (0.5, lin(f.get("refusal_rate"), 0.05, 0.35)),
        "unconfirmed": (0.2, lin(1 - (f.get("confirm_rate") or 1), 0.1, 0.45)),
        "returns": (0.2, lin(f.get("return_rate"), 0.02, 0.2)),
        "trust_mismatch_share": (0.1, lin(trust_share, 0, 0.6)),
    }
    return round(_weighted(parts), 1)


def sentiment_score(f: dict) -> float | None:
    if not f["comments"]:
        return None
    return round(lin(f["positive_comment_rate"] - f["negative_comment_rate"], -1, 1), 1)
