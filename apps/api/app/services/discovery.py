"""Product Discovery Intelligence (product_discovery_intelligence_summary.md).

Product Potential Vector instead of one Win Score:
  market_demand (validation) · novelty · wave_potential · creative_potential · market_fit (per market)
  · mkt_appeal (team votes / taste model) · competition · economics · operational_fit
  · compliance_risk · confidence · company_fit (internal, realtime §19)
→ opportunity, radar quadrant (BREAKOUT / EXPERIMENTAL / STABLE_WINNER / LOW_SIGNAL), decision.

Every number is a formula over stored data. AI never assigns these scores.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ad, Creative, Product, Vote
from .scoring import lin

VOTE_VALUE = {"LOVE": 100, "TEST": 80, "WATCH": 55, "NORMAL": 35, "SKIP": 0}
VOTE_REASONS = ["Visual mạnh", "Hook dễ", "Demo được", "Before/After", "UGC dễ làm", "Hợp Saudi/GCC", "Hợp VN", "Giá tốt",
                "Độc lạ", "Quá phổ biến", "Không có angle mới", "Khó ship / dễ vỡ", "Rủi ro policy", "Margin thấp"]

# ------------------------------------------------------------ compliance (discovery §16)
COMPLIANCE_PATTERNS = [
    (r"\b(replica|1:1|aaa\+?|super ?fake|mirror quality|master copy|first copy|rep)\b|hàng fake|hàng nhái|like auth", 45, "counterfeit"),
    (r"\b(rolex|cartier|louis vuitton|\blv\b|gucci|chanel|dior|hermes|hermès|prada|van cleef|bvlgari|tiffany|apple watch|airpods|dyson)\b", 25, "brand"),
    (r"chữa (khỏi|dứt điểm)|trị dứt|cure[sd]?\b|100% (hiệu quả|effective)|guaranteed results|cam kết khỏi|يعالج نهائيا", 35, "medical_claim"),
    (r"giảm \d+ ?kg|lose \d+ ?(kg|lbs|pounds)|weight loss in \d+ days|tan mỡ cấp tốc", 30, "weight_loss_claim"),
    (r"\b(viagra|cialis|sex|tăng size|enlarge(ment)?|kéo dài thời gian)\b", 35, "adult"),
    (r"\b(gun|vape|cbd|thc|nicotine|e-?cig)\b|thuốc lá điện tử", 40, "restricted"),
    (r"before\s*/?\s*after|trước và sau", 10, "before_after"),
]


def compliance_risk(texts: list[str], rejected_rate: float | None = None) -> tuple[float, list[str]]:
    blob = " ".join(texts).lower()
    score, flags = 0.0, []
    for pat, w, flag in COMPLIANCE_PATTERNS:
        if re.search(pat, blob):
            score += w
            flags.append(flag)
    if "brand" in flags and "counterfeit" not in flags and re.search(r"giá rẻ|cheap|\$\d{1,2}\b|99k|49k", blob):
        score += 20
        flags.append("cheap_luxury")
    if rejected_rate:
        score += 40 * rejected_rate
    return round(min(100.0, score), 1), flags


# ------------------------------------------------------------ creative signals
CURIOSITY = re.compile(r"\?|bí mật|secret|bạn có biết|did you know|you won't believe|wait for it|cái gì|what is this|"
                       r"tiktok made me|viral|nobody|không ai|hack|trick|mẹo|لن تصدق|سر", re.I)
DEMO = re.compile(r"watch|xem|demo|how (it|to)|cách (dùng|làm)|in \d+ (sec|giây)|chỉ cần|just|step|test|thử", re.I)
TRANSFORM = re.compile(r"before|after|trước|sau \d+|results?|kết quả|transformation|thay đổi|قبل|بعد", re.I)
UGC = re.compile(r"\b(i |my |me |tôi |mình |em |chị |anh )|review|unbox|trải nghiệm|testimonial|tried|dùng thử", re.I)
EMOTION = re.compile(r"mẹ|mom|mother|gift|quà|love|yêu|family|gia đình|baby|bé|con|eid|ramadan|valentine|هدية|أم", re.I)


def creative_signals(ads: list[Ad], creatives: list[Creative]) -> dict:
    texts = [" ".join(filter(None, [a.title, a.ad_text])) for a in ads]
    n = max(1, len(texts))
    # Bayesian shrink: with 1-2 ads a single regex hit must not look like 100 % of the creatives
    share = lambda rx: (sum(1 for t in texts if rx.search(t)) + 0.1) / (len(texts) + 2)
    families = {c.family_id or c.id for c in creatives if c.status == "stored"} or {a.creative_fingerprint for a in ads}
    hooks = Counter(a.hook for a in ads if a.hook)
    angles = Counter(a.angle for a in ads if a.angle)
    video_share = sum(1 for a in ads if (a.media_type or "") == "video") / n
    likes = sum(a.likes or 0 for a in ads)
    comments = sum(a.comments_count or 0 for a in ads)
    shares = sum(a.shares or 0 for a in ads)
    variants = sum(a.variants or 1 for a in ads)
    return {
        "curiosity": share(CURIOSITY), "demo": share(DEMO), "transformation": share(TRANSFORM), "ugc": share(UGC),
        "emotion": share(EMOTION), "families": len(families), "hook_types": len(hooks), "angle_types": len(angles),
        "video_share": video_share, "variants": variants, "variants_per_ad": variants / n,
        "commentability": (comments / likes) if likes >= 50 else None, "shareability": (shares / likes) if likes >= 50 else None,
    }


# ------------------------------------------------------------ taste model + market DNA
class TasteModel:
    """Marketing Team Taste Model (discovery §11): learns vote value per feature (category, market, angle,
    hook, funnel, price band) from all votes; predicts appeal for unvoted products."""

    def __init__(self, db: Session):
        self.weights: dict[str, list[float]] = defaultdict(list)
        votes = db.scalars(select(Vote)).all()
        self.n_votes = len(votes)
        if not votes:
            return
        prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_({v.product_id for v in votes})))}
        ad_feats = self._ad_features(db, list(prods))
        for v in votes:
            p = prods.get(v.product_id)
            if not p:
                continue
            for feat in self.features(p, ad_feats.get(p.id, set()), v.market):
                self.weights[feat].append(VOTE_VALUE.get(v.decision, 50))

    @staticmethod
    def _ad_features(db: Session, pids: list[int]) -> dict[int, set[str]]:
        out: dict[int, set[str]] = defaultdict(set)
        if not pids:
            return out
        for pid, hook, angle, funnel in db.execute(select(Ad.product_id, Ad.hook, Ad.angle, Ad.funnel).where(Ad.product_id.in_(pids))):
            out[pid] |= {f"hook:{hook}", f"angle:{angle}", f"funnel:{funnel}"}
        return out

    @staticmethod
    def features(p: Product, ad_feats: set[str], market: str | None = None) -> list[str]:
        feats = [f"category:{p.category}", f"market:{market or p.country}", *ad_feats]
        if p.category and (market or p.country):
            feats.append(f"cat_market:{p.category}|{market or p.country}")
        return feats

    def predict(self, p: Product, ad_feats: set[str], market: str | None = None) -> float | None:
        vals = []
        for f in self.features(p, ad_feats, market):
            w = self.weights.get(f)
            if w:
                # shrink towards neutral 50 when few votes
                k = len(w)
                vals.append((sum(w) + 50 * 2) / (k + 2))
        return round(sum(vals) / len(vals), 1) if vals else None


def market_dna(db: Session) -> dict[str, dict]:
    """Learned, not hard-coded (discovery §12-14): per market, what wins / what the team loves / what runs."""
    dna: dict[str, dict] = defaultdict(lambda: {"categories": Counter(), "angles": Counter(), "funnels": Counter(),
                                                "loved": Counter(), "won": Counter(), "price": []})
    for country, cat, angle, funnel, price in db.execute(
            select(Ad.country, Product.category, Ad.angle, Ad.funnel, Ad.price).join(Product, Product.id == Ad.product_id)
            .where(Ad.is_active.is_(True), Ad.is_internal.is_(False))):
        if not country:
            continue
        d = dna[country]
        d["categories"][cat] += 1
        d["angles"][angle] += 1
        d["funnels"][funnel] += 1
    for v in db.scalars(select(Vote).where(Vote.decision.in_(["LOVE", "TEST"]))).all():
        p = db.get(Product, v.product_id)
        if p and (v.market or p.country):
            dna[v.market or p.country]["loved"][p.category] += 1
    from ..models import Experiment
    for e in db.scalars(select(Experiment).where(Experiment.status == "WIN")).all():
        p = db.get(Product, e.product_id)
        if p and e.market:
            dna[e.market]["won"][p.category] += 1
    out = {}
    for m, d in dna.items():
        tot = sum(d["categories"].values()) or 1
        out[m] = {
            "active_ads": tot,
            "top_categories": [(k, round(v / tot, 3)) for k, v in d["categories"].most_common(6)],
            "top_angles": [(k, round(v / tot, 3)) for k, v in d["angles"].most_common(6)],
            "funnels": {k: round(v / tot, 3) for k, v in d["funnels"].most_common()},
            "loved_categories": dict(d["loved"]), "won_categories": dict(d["won"]),
        }
    return out


def market_fit_score(dna: dict, market: str, category: str | None, angle: str | None) -> float:
    d = dna.get(market)
    if not d:
        return 50.0
    weight = min(1.0, d["active_ads"] / 150)  # little market data → stay near neutral
    cats = dict(d["top_categories"])
    parts = [lin(cats.get(category, 0), 0, 0.35)]
    angles = dict(d["top_angles"])
    if angle:
        parts.append(lin(angles.get(angle, 0), 0, 0.3))
    if d["loved_categories"]:
        parts.append(100 if category in d["loved_categories"] else 40)
    if d["won_categories"]:
        parts.append(100 if category in d["won_categories"] else 35)
    raw = sum(parts) / len(parts)
    return round(50 + (raw - 50) * weight, 1)


# ------------------------------------------------------------ the vector
def potential_vector(p: Product, f: dict, ads: list[Ad], creatives: list[Creative], votes: list[Vote],
                     taste: TasteModel, dna: dict) -> dict:
    now = datetime.utcnow()
    ext_ads = [a for a in ads if not a.is_internal]
    cs = creative_signals(ext_ads, creatives)
    age_days = (now - p.first_seen_at).days if p.first_seen_at else 0

    demand = 0.35 * lin(f.get("active_ads", 0), 0, 120) + 0.25 * lin(f.get("advertiser_count", 0), 1, 30) \
        + 0.25 * lin(f.get("ads_running_30d", 0), 0, 15) + 0.15 * lin(f.get("market_count", 0), 1, 4)
    novelty = 0.30 * (100 - lin(f.get("advertiser_count", 0), 2, 40)) + 0.20 * (100 - lin(f.get("store_count", 0), 1, 25)) \
        + 0.20 * lin(60 - age_days, 0, 60) + 0.15 * (100 - (f.get("keyword_competition") or 0.5) * 100) \
        + 0.15 * (100 - lin(f.get("market_count", 0), 1, 6))
    wave_parts = {
        "scroll_stop_curiosity": lin(cs["curiosity"], 0, 0.5),
        "demo_strength": lin(cs["demo"], 0, 0.5),
        "transformation": lin(cs["transformation"], 0, 0.4),
        "commentability": lin(cs["commentability"], 0, 0.08) if cs["commentability"] is not None else lin(f.get("purchase_intent_rate", 0), 0, 0.2),
        "shareability": lin(cs["shareability"], 0, 0.05) if cs["shareability"] is not None else 40.0,
        "velocity": lin(f.get("creative_growth_7d", 0), 0, 1.0),
        "scaling_variants": max(lin(cs["variants_per_ad"], 1, 4), lin(f.get("variation_max_group", 1), 1, 5)),
        "emotional_trigger": lin(cs["emotion"], 0, 0.4),
    }
    wave = sum(wave_parts.values()) / len(wave_parts)
    creative_parts = {
        "concept_families": lin(cs["families"], 1, 12),
        "angle_diversity": lin(cs["angle_types"], 1, 5),
        "hook_diversity": lin(cs["hook_types"], 1, 5),
        "video": lin(cs["video_share"], 0, 0.8),
        "demo_or_before_after": lin(max(cs["demo"], cs["transformation"]), 0, 0.4),
        "ugc_reproducible": lin(cs["ugc"], 0, 0.4),
    }
    creative = sum(creative_parts.values()) / len(creative_parts)

    voted = [VOTE_VALUE.get(v.decision, 50) for v in votes]
    ad_feats = {f"hook:{a.hook}" for a in ext_ads} | {f"angle:{a.angle}" for a in ext_ads} | {f"funnel:{a.funnel}" for a in ext_ads}
    predicted = taste.predict(p, ad_feats)
    if voted:
        mkt_appeal, appeal_src = sum(voted) / len(voted), f"{len(voted)} votes"
    elif predicted is not None:
        mkt_appeal, appeal_src = predicted, f"taste model ({taste.n_votes} votes)"
    else:
        mkt_appeal, appeal_src = 50.0, "chưa có vote"

    top_angle = Counter(a.angle for a in ext_ads if a.angle).most_common(1)
    markets = f.get("markets") or ([p.country] if p.country else [])
    per_market = {m: market_fit_score(dna, m, p.category, top_angle[0][0] if top_angle else None) for m in markets}
    market_fit = max(per_market.values()) if per_market else 50.0

    risk, flags = compliance_risk([a.ad_text or "" for a in ext_ads[:60]] + [p.canonical_name], f.get("ad_rejection_rate"))
    competition = p.saturation_score or 0
    economics = (f.get("_scores") or {}).get("margin_potential") or 55.0
    company_fit = company_fit_score(f)
    operational = company_fit if company_fit is not None else 60.0
    conf = p.confidence_score or 0

    raw = (0.16 * market_fit + 0.10 * mkt_appeal + 0.08 * operational + 0.14 * novelty + 0.18 * wave + 0.14 * creative
           + 0.10 * economics + 0.10 * demand) - 0.12 * max(0, competition - 30) - 0.5 * max(0, risk - 20)
    opportunity = max(0.0, min(100.0, raw * (0.75 + 0.25 * conf / 100)))

    quadrant = ("BREAKOUT" if wave >= 55 and demand >= 50 else "EXPERIMENTAL" if wave >= 55 else
                "STABLE_WINNER" if demand >= 50 else "LOW_SIGNAL")
    vec = {
        "market_demand": demand, "novelty": novelty, "wave_potential": wave, "creative_potential": creative,
        "market_fit": market_fit, "mkt_appeal": mkt_appeal, "competition": competition, "saturation": p.saturation_score,
        "economics": economics, "operational_fit": operational, "compliance_risk": risk, "confidence": conf,
        "company_fit": company_fit, "opportunity": opportunity,
    }
    vec = {k: (round(v, 1) if isinstance(v, (int, float)) else v) for k, v in vec.items()}
    return {
        "vector": vec, "quadrant": quadrant, "compliance_flags": flags, "mkt_appeal_source": appeal_src,
        "mkt_appeal_predicted": predicted, "wave_parts": {k: round(v, 1) for k, v in wave_parts.items()},
        "creative_parts": {k: round(v, 1) for k, v in creative_parts.items()},
        "per_market": {m: {"market_fit": s, "decision": discovery_decision(vec | {"market_fit": s}, quadrant)[0]}
                       for m, s in per_market.items()},
        "signals": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in cs.items()},
    }


def discovery_decision(v: dict, quadrant: str) -> tuple[str, list[str]]:
    """Discovery §20 — actions beyond win/lose."""
    if v["compliance_risk"] >= 60:
        return "REVIEW", [f"Compliance risk {v['compliance_risk']:.0f} — kiểm tra hàng nhái / claim trước khi test"]
    if quadrant == "BREAKOUT" and v["opportunity"] >= 62 and v["market_fit"] >= 55:
        return "TEST_NOW", [f"Breakout: wave {v['wave_potential']:.0f} + demand {v['market_demand']:.0f}", f"Opportunity {v['opportunity']:.0f}"]
    if v["novelty"] >= 70 and v["wave_potential"] >= 60 and v["creative_potential"] >= 55 and v["market_fit"] >= 50:
        return "TEST", [f"Novelty {v['novelty']:.0f}, Wave {v['wave_potential']:.0f}, Creative {v['creative_potential']:.0f}"]
    if quadrant == "EXPERIMENTAL" and v["confidence"] < 60:
        return "EXPERIMENT", [f"Wave {v['wave_potential']:.0f} cao nhưng thị trường chưa xác nhận — test nhỏ"]
    if v["competition"] >= 70:
        return "SKIP", [f"Bão hoà {v['competition']:.0f}"]
    if quadrant == "STABLE_WINNER":
        return "WATCH", ["Đã được xác nhận nhưng ít tín hiệu sóng mới — theo dõi đối thủ"]
    if v["opportunity"] < 30:
        return "SKIP", [f"Opportunity {v['opportunity']:.0f}"]
    return "DISCOVER", ["Mới phát hiện — cần thêm dữ liệu"]


# ------------------------------------------------------------ Company Product Fit (realtime §19)
def company_fit_score(f: dict) -> float | None:
    if not f.get("has_internal") or not f.get("orders"):
        return None
    parts = {
        "marketing": (0.20, 0.5 * lin(f.get("roas"), 1, 4) + 0.5 * (100 - lin((f.get("cpa") or 0) / (f.get("aov") or 1), 0.15, 0.6))),
        "sales": (0.15, lin(f.get("cvr"), 0.005, 0.04)),
        "confirmation": (0.10, lin(f.get("confirm_rate"), 0.5, 0.9)),
        "delivery": (0.20, lin(f.get("delivery_rate"), 0.5, 0.9)),
        "refusal": (0.10, 100 - lin(f.get("refusal_rate"), 0.05, 0.35)),
        "return": (0.05, 100 - lin(f.get("return_rate"), 0.02, 0.2)),
        "margin": (0.15, lin(f.get("contribution_margin"), -0.1, 0.3)),
        "customer": (0.05, lin((f.get("positive_comment_rate") or 0) - (f.get("negative_comment_rate") or 0), -0.5, 0.7)),
    }
    return round(sum(w * v for w, v in parts.values()), 1)
