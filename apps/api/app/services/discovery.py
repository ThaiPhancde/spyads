"""Product Discovery Intelligence (product_discovery_intelligence_summary.md).

Product Potential Vector instead of one Win Score:
  market_demand (validation) · novelty · wave_potential · creative_potential · market_fit (per market)
  · mkt_appeal (team votes / taste model) · competition · economics · compliance_risk · confidence
→ opportunity, radar quadrant (BREAKOUT / EXPERIMENTAL / STABLE_WINNER / LOW_SIGNAL), decision.

Every number is a formula over stored data. AI never assigns these scores.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..markets import priority_boost
from ..models import Ad, Creative, Product, Vote
from .creative_text import CURIOSITY, DEMO, EMOTION, TRANSFORM, UGC, ad_text_evidence, summarize
from .scoring import lin

VOTE_VALUE = {"LOVE": 100, "TEST": 80, "WATCH": 55, "NORMAL": 35, "SKIP": 0}
VOTE_REASONS = ["Visual mạnh", "Hook dễ", "Demo được", "Before/After", "UGC dễ làm", "Hợp Saudi/GCC", "Hợp VN", "Giá tốt",
                "Độc lạ", "Quá phổ biến", "Không có angle mới", "Khó ship / dễ vỡ", "Rủi ro policy", "Margin thấp"]

# ------------------------------------------------------------ compliance (discovery §16)
COMPLIANCE_PATTERNS = [
    (r"\b(replica|1:1|aaa\+?|super ?fake|mirror quality|master copy|first copy|rep)\b|hàng fake|hàng nhái|like auth", 45, "counterfeit"),
    (r"\b(rolex|cartier|louis vuitton|\blv\b|gucci|chanel|dior|hermes|hermès|prada|van cleef|bvlgari|tiffany|apple watch|airpods|dyson)\b", 25, "brand"),
    (r"chữa (khỏi|dứt điểm)|trị dứt|\bcures?\b|(?<!semi[ -])\bcured\b|100% (hiệu quả|effective)|guaranteed results|cam kết khỏi|يعالج نهائيا", 35, "medical_claim"),
    (r"giảm \d+ ?kg|lose \d+ ?(kg|lbs|pounds)|weight loss in \d+ days|tan mỡ cấp tốc", 30, "weight_loss_claim"),
    (r"\b(viagra|cialis|sex|tăng size|enlarge(ment)?|kéo dài thời gian)\b", 35, "adult"),
    (r"\b(air gun|pistol|firearm|rifle|vape|cbd|thc|nicotine|e-?cig)\b|thuốc lá điện tử", 40, "restricted"),
    (r"before\s*/?\s*after|trước và sau", 10, "before_after"),
]


TOOL_GUNS = re.compile(r"\b(massage|fascia|glue|nail|heat|spray|paint|tattoo|caulk|staple|grease) guns?\b")


def compliance_risk(texts: list[str]) -> tuple[float, list[str]]:
    blob = TOOL_GUNS.sub(" ", " ".join(texts).lower())  # "massage gun" is a tool, not a weapon
    score, flags = 0.0, []
    for pat, w, flag in COMPLIANCE_PATTERNS:
        if re.search(pat, blob):
            score += w
            flags.append(flag)
    if "brand" in flags and "counterfeit" not in flags and re.search(r"giá rẻ|cheap|\$\d{1,2}\b|99k|49k", blob):
        score += 20
        flags.append("cheap_luxury")
    return round(min(100.0, score), 1), flags


# ------------------------------------------------------------ creative signals
NEUTRAL_PART = 30.0  # what a text-derived part is worth when no ad copy could be read (not 0: unknown is not negative)


def creative_signals(ads: list[Ad], creatives: list[Creative]) -> dict:
    extra: dict[int, list[str]] = defaultdict(lambda: ["", ""])  # ad id → [ocr, transcript] the creatives already carry
    for c in creatives:
        if c.ad_id:
            extra[c.ad_id][0] += f" {c.ocr_text or ''}"
            extra[c.ad_id][1] += f" {c.transcript or ''}"
    evs = [ad_text_evidence(a, *extra[a.id]) for a in ads]
    texts = [e["text"] for e in evs if e["reliability"] > 0]  # ads with no readable copy are *unknown*, not "no curiosity / no demo"
    n = max(1, len(ads))
    # Bayesian shrink: with 1-2 ads a single regex hit must not look like 100 % of the creatives
    share = lambda rx: (sum(1 for t in texts if rx.search(t)) + 0.1) / (len(texts) + 2)
    families = {c.family_id or c.id for c in creatives if c.status == "stored"} or {a.creative_fingerprint for a in ads}
    hooks = Counter(a.hook for a in ads if a.hook and a.hook != "statement")  # classifier defaults are not evidence
    angles = Counter(a.angle for a in ads if a.angle and a.angle != "general")
    video_share = sum(1 for a in ads if (a.media_type or "") == "video") / n
    likes = sum(a.likes or 0 for a in ads)
    comments = sum(a.comments_count or 0 for a in ads)
    shares = sum(a.shares or 0 for a in ads)
    variants = sum(a.variants or 1 for a in ads)
    return {
        "curiosity": share(CURIOSITY), "demo": share(DEMO), "transformation": share(TRANSFORM), "ugc": share(UGC),
        "emotion": share(EMOTION), "families": len(families), "hook_types": len(hooks), "angle_types": len(angles),
        "text": summarize(evs), "video_share": video_share, "variants": variants, "variants_per_ad": variants / n,
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
            out[pid] |= ad_feature_set(hook, angle, funnel)
        return out

    MIN_VOTES = 3  # a feature needs this many votes before it says anything (2 votes used to move ~4,800 products)

    @staticmethod
    def features(p: Product, ad_feats: set[str], market: str | None = None) -> list[str]:
        feats = [*ad_feats]
        if p.category:
            feats.append(f"category:{p.category}")
        if market or p.country:
            feats.append(f"market:{market or p.country}")
        if p.category and (market or p.country):
            feats.append(f"cat_market:{p.category}|{market or p.country}")
        return feats

    def predict(self, p: Product, ad_feats: set[str], market: str | None = None) -> float | None:
        vals = []
        for f in self.features(p, ad_feats, market):
            w = self.weights.get(f)
            if w and len(w) >= self.MIN_VOTES:
                # shrink towards neutral 50 when few votes
                k = len(w)
                vals.append((sum(w) + 50 * 2) / (k + 2))
        return round(sum(vals) / len(vals), 1) if vals else None


_GENERIC = {"hook:statement", "angle:general"}  # classifier defaults, not taste signals


def ad_feature_set(hook, angle, funnel) -> set[str]:
    return {f for f in (f"hook:{hook}" if hook else None, f"angle:{angle}" if angle else None,
                        f"funnel:{funnel}" if funnel else None) if f and f not in _GENERIC}


def market_dna(db: Session) -> dict[str, dict]:
    """Learned, not hard-coded (discovery §12-14): per market, what the team loves / what runs."""
    dna: dict[str, dict] = defaultdict(lambda: {"categories": Counter(), "angles": Counter(), "funnels": Counter(),
                                                "loved": Counter(), "price": []})
    for country, cat, angle, funnel, price in db.execute(
            select(Ad.country, Product.category, Ad.angle, Ad.funnel, Ad.price).join(Product, Product.id == Ad.product_id)
            .where(Ad.is_active.is_(True), Ad.is_internal.is_(False))):
        if not country or cat is None:  # unclassified products are not a category (H13)
            continue
        d = dna[country]
        d["categories"][cat] += 1
        d["angles"][angle] += 1
        d["funnels"][funnel] += 1
    for v in db.scalars(select(Vote).where(Vote.decision.in_(["LOVE", "TEST"]))).all():
        p = db.get(Product, v.product_id)
        if p and (v.market or p.country):
            dna[v.market or p.country]["loved"][p.category] += 1
    out = {}
    for m, d in dna.items():
        tot = sum(d["categories"].values()) or 1
        out[m] = {
            "active_ads": tot,
            "top_categories": [(k, round(v / tot, 3)) for k, v in d["categories"].most_common(6)],
            "top_angles": [(k, round(v / tot, 3)) for k, v in d["angles"].most_common(6)],
            "funnels": {k: round(v / tot, 3) for k, v in d["funnels"].most_common()},
            "loved_categories": dict(d["loved"]),
        }
    return out


def market_fit_score(dna: dict, market: str, category: str | None, angle: str | None) -> float:
    d = dna.get(market)
    if not d or category is None:
        return 50.0
    weight = min(1.0, d["active_ads"] / 150)  # little market data → stay near neutral
    cats = dict(d["top_categories"])
    parts = [lin(cats.get(category, 0), 0, 0.35)]
    angles = dict(d["top_angles"])
    if angle:
        parts.append(lin(angles.get(angle, 0), 0, 0.3))
    if d["loved_categories"]:
        parts.append(100 if category in d["loved_categories"] else 40)
    raw = sum(parts) / len(parts)
    return round(50 + (raw - 50) * weight, 1)


# ------------------------------------------------------------ ranking v2 (2026-10-09): what marketers trust
# The old demand = lin(active_ads, 1, 12)… sat at 0 for the 82 % of products with one ad, so 98 % were LOW_SIGNAL and
# the decision fell through to WATCH. Signals below are log-scaled for the skewed data (p50 1 ad / 108 days).
def _log_lin(x, lo, hi) -> float:
    return lin(math.log1p(max(x or 0, 0)), math.log1p(lo), math.log1p(hi))


def _sellers(n: int) -> float:
    """Validation bell: 1 seller proves nothing, 2-10 = validated, crowded beyond (saturation penalises it too)."""
    return 0.0 if n <= 1 else 60.0 if n == 2 else 100.0 if n <= 10 else 70.0 if n <= 15 else 40.0


def market_demand(f: dict) -> float:
    """Proof the product sells: strongest live ad keeps running · the advertiser scales it · several sellers · reach."""
    return (0.35 * _log_lin(f.get("longevity_days"), 7, 120)
            + 0.30 * max(_log_lin(f.get("unique_ads", f.get("active_ads")), 1, 10), lin(f.get("variants") or 1, 1, 5), lin(f.get("strong_ads") or 0, 1, 5))
            + 0.20 * _sellers(f.get("advertiser_count") or 0) + 0.15 * lin(f.get("market_count") or 0, 1, 4))


def momentum(f: dict) -> float:
    """Growing right now: new ads and new sellers in the last 7 days."""
    return 0.6 * lin(f.get("new_ads_7d") or 0, 0, 5) + 0.4 * lin(f.get("new_advertisers_7d") or 0, 0, 3)


def evidence(p: Product, f: dict, ads: list[Ad]) -> dict:
    """Raw facts discovery_decision gates on (kept on product.potential so the UI can show them)."""
    mix = Counter(a.funnel for a in ads if a.funnel and a.channel in (None, "ads"))
    return {"active_ads": f.get("active_ads", 0), "unique_ads": f.get("unique_ads", f.get("active_ads", 0)), "longevity_days": f.get("longevity_days", 0),
            "advertisers": f.get("advertiser_count", 0), "advertisers_at_primary_market": f.get("advertisers_at_primary_market", 0),
            "variants": f.get("variants", 1), "non_commerce_share": round((mix.get("app", 0) + mix.get("form", 0)) / max(1, sum(mix.values())), 2),
            "non_product": p.category == "non_product",
            "unnamed": p.name_status == "UNKNOWN" or (p.name_status is None and bool(NO_PRODUCT_NAME.search(p.canonical_name or "")))}


# placeholder names, CTA strings and ad headlines that ended up as a cluster name ("Chat trong Messenger", "Natural Relief In
# Weeks", "Sculpiflex Is Just A Scam I M"): a product name is a noun phrase, not a sentence. Single-letter tokens are
# apostrophe cuts ("Don T", "I M").
NO_PRODUCT_NAME = re.compile(
    r"\(chưa đặt tên\)|^(chat|nhắn tin|message|send message|inbox|contact)(?!\w)|messenger|whatsapp|دردش|راسلنا|تواصل"
    r"|(^|\s)[A-Za-z](\s|$)|(?<!\w)(scam|reasons?|in \d+ \w+|in weeks|money back|miss out|is just|are you|how to|why you|you need)(?!\w)", re.I)


# ------------------------------------------------------------ the vector
def potential_vector(p: Product, f: dict, ads: list[Ad], creatives: list[Creative], votes: list[Vote],
                     taste: TasteModel, dna: dict) -> dict:
    now = datetime.utcnow()
    ext_ads = [a for a in ads if not a.is_internal]
    cs = creative_signals(ext_ads, creatives)
    age_days = (now - p.first_seen_at).days if p.first_seen_at else 0

    demand, mom, ev = market_demand(f), momentum(f), evidence(p, f, ext_ads)
    # keyword_competition dropped (weight 0): no source fills it yet — remaining weights renormalised to 100
    novelty = (0.30 * (100 - lin(f.get("advertiser_count", 0), 2, 15)) + 0.20 * (100 - lin(f.get("store_count", 0), 1, 4))
               + 0.20 * lin(60 - age_days, 0, 60) + 0.15 * (100 - lin(f.get("market_count", 0), 1, 6))) / 0.85
    cov = cs["text"]["coverage"]  # share of the ad copy the regexes could actually read (creative_text.py)
    blend = lambda x: cov * x + (1 - cov) * NEUTRAL_PART  # no readable copy → neutral and low confidence, never a guessed 0
    wave_parts = {
        "scroll_stop_curiosity": blend(lin(cs["curiosity"], 0, 0.5)),
        "demo_strength": blend(lin(cs["demo"], 0, 0.5)),
        "transformation": blend(lin(cs["transformation"], 0, 0.4)),
        # ponytail: no source fills comments / purchase_intent yet — commentability dropped (weight 0)
        "shareability": lin(cs["shareability"], 0, 0.05) if cs["shareability"] is not None else 40.0,
        "velocity": lin(f.get("creative_growth_7d", 0), 0, 1.0),
        "scaling_variants": max(lin(cs["variants_per_ad"], 1, 4), lin(f.get("variation_max_group", 1), 1, 5)),
        "emotional_trigger": blend(lin(cs["emotion"], 0, 0.4)),
    }
    wave = sum(wave_parts.values()) / len(wave_parts)
    creative_parts = {
        "concept_families": lin(cs["families"], 1, 12),
        "angle_diversity": lin(cs["angle_types"], 1, 5),
        "hook_diversity": lin(cs["hook_types"], 1, 5),
        "video": lin(cs["video_share"], 0, 0.8),
        "demo_or_before_after": blend(lin(max(cs["demo"], cs["transformation"]), 0, 0.4)),
        "ugc_reproducible": blend(lin(cs["ugc"], 0, 0.4)),
    }
    creative = sum(creative_parts.values()) / len(creative_parts)

    voted = [VOTE_VALUE.get(v.decision, 50) for v in votes]
    ad_feats = set().union(*(ad_feature_set(a.hook, a.angle, a.funnel) for a in ext_ads)) if ext_ads else set()
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

    risk, flags = compliance_risk([a.ad_text or "" for a in ext_ads[:60]] + [p.canonical_name])
    competition = p.saturation_score or 0
    economics = (f.get("_scores") or {}).get("margin_potential") or 55.0
    conf = p.confidence_score or 0

    # market evidence (demand + momentum) carries 42 %; regex creative signals (wave, creative) only 16 % — they are weak
    raw = (0.28 * demand + 0.14 * mom + 0.12 * market_fit + 0.10 * mkt_appeal + 0.08 * novelty + 0.08 * wave + 0.08 * creative
           + 0.12 * economics) - 0.12 * max(0, competition - 30) - 0.5 * max(0, risk - 20)
    opportunity_raw = max(0.0, min(100.0, raw * (0.75 + 0.25 * conf / 100)))
    boost = priority_boost(markets)  # PH first — applied once here; engine copies this onto product.opportunity_score
    opportunity = min(100.0, opportunity_raw + boost)

    # validated by the market (demand) × growing this week (momentum)
    quadrant = ("BREAKOUT" if mom >= 40 and demand >= 50 else "EXPERIMENTAL" if mom >= 40 else
                "STABLE_WINNER" if demand >= 50 else "LOW_SIGNAL")
    vec = {
        "market_demand": demand, "momentum": mom, "novelty": novelty, "wave_potential": wave, "creative_potential": creative,
        "market_fit": market_fit, "mkt_appeal": mkt_appeal, "competition": competition, "saturation": p.saturation_score,
        "economics": economics, "compliance_risk": risk, "confidence": conf, "wave_confidence": round(100 * cov), "opportunity": opportunity, "opportunity_raw": opportunity_raw, "priority_boost": boost,
    }
    vec = {k: (round(v, 1) if isinstance(v, (int, float)) else v) for k, v in vec.items()}
    decision = discovery_decision(vec, quadrant, ev)
    # every list sorts by opportunity: a gated product (stopped, app, crowded, unnamed…) must not outrank a TEST
    cap = OPPORTUNITY_CAP.get(decision[0])
    if cap is not None and vec["opportunity"] > cap:
        vec["opportunity"] = float(cap)
    return {
        "vector": vec, "quadrant": quadrant, "decision": decision, "compliance_flags": flags, "mkt_appeal_source": appeal_src,
        "mkt_appeal_predicted": predicted, "wave_parts": {k: round(v, 1) for k, v in wave_parts.items()},
        "creative_parts": {k: round(v, 1) for k, v in creative_parts.items()},
        "per_market": {m: {"market_fit": s, "decision": discovery_decision(vec | {"market_fit": s}, quadrant, ev)[0]}
                       for m, s in per_market.items()},
        "signals": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in cs.items() if k != "text"},
        "creative_evidence": cs["text"],
        "evidence": ev,
    }


OPPORTUNITY_CAP = {"SKIP": 30, "REVIEW": 30, "WATCH": 55}


def discovery_decision(v: dict, quadrant: str, e: dict | None = None) -> tuple[str, list[str]]:
    """Before a test (market evidence only): TEST_NOW · TEST · WATCH · REVIEW · SKIP. Gates on raw facts first (no live
    ad, not a product, compliance, crowded), then demand × momentum. On the 2026-10-09 DB: ≈0.2 % TEST_NOW, ≈7 % TEST."""
    e = e or {}
    d, m = v["market_demand"], v.get("momentum", 0)
    facts = f"{e.get('active_ads', 0)} ads đang chạy · {e.get('advertisers', 0)} người bán · ad lâu nhất {e.get('longevity_days', 0)} ngày"
    if e and not e.get("active_ads"):
        return "SKIP", ["Không có quảng cáo nào đang chạy (chỉ có listing nguồn hàng hoặc ad đã dừng)"]
    if e.get("non_product") or e.get("non_commerce_share", 0) >= 0.5:
        return "SKIP", ["App / dịch vụ / form đăng ký — không phải sản phẩm vật lý"]
    if v["compliance_risk"] >= 60:
        return "REVIEW", [f"Compliance risk {v['compliance_risk']:.0f} — kiểm tra hàng nhái / claim trước khi test"]
    if max(e.get("advertisers_at_primary_market", 0), e.get("advertisers", 0)) > 15 or v["competition"] >= 70:
        return "SKIP", [f"Bão hoà: {e.get('advertisers', 0)} người bán ({e.get('advertisers_at_primary_market', 0)} ở thị trường ưu tiên), saturation {v['competition']:.0f}"]
    if e.get("unnamed") and d >= 45:
        return "WATCH", ["Tín hiệu mạnh nhưng chưa xác định được sản phẩm — mở ad để xem", facts]
    if quadrant == "BREAKOUT" and e.get("longevity_days", 0) >= 14 and v["market_fit"] >= 50:
        return "TEST_NOW", [f"Đã được xác nhận (demand {d:.0f}) và đang tăng tuần này (momentum {m:.0f})", facts]
    if quadrant in ("BREAKOUT", "EXPERIMENTAL"):
        return "TEST", [f"Đang tăng nhanh (momentum {m:.0f}) nhưng chưa chạy đủ lâu / chưa được xác nhận — test ngân sách nhỏ", facts]
    if d >= 55 and (e.get("advertisers", 0) >= 2 or e.get("variants", 1) >= 2):
        return "TEST", [f"Đã được xác nhận (demand {d:.0f}): nhiều người bán hoặc đang nhân bản creative", facts]
    if d >= 45 or m >= 20:
        return "WATCH", [f"Có tín hiệu (demand {d:.0f}, momentum {m:.0f}) — chờ thêm người bán / ads mới", facts]
    return "SKIP", [f"Tín hiệu yếu (demand {d:.0f}, momentum {m:.0f})", facts]
