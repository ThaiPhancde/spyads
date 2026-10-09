"""Plain-assert self-check for the 2026-10-07 scoring fixes. Run: cd apps/api && .venv/Scripts/python.exe test_scoring_rules.py"""
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("PRIORITY_MARKETS", "PH")

from app.models import Ad, Product
from app.services import agent
from app.services.discovery import TasteModel, compliance_risk, market_fit_score, potential_vector
from app.services.enrichment import classify_category, to_usd
from app.services.entity_resolution import clean_name, title_similarity
from app.services.features import ProductData, compute_features
from app.services.scoring import rarity_score, saturation_score

# ---- compliance regex
assert "medical_claim" not in compliance_risk(["secure a flawless look", "semi cured gel nails", "manicure kit"])[1]
assert "restricted" not in compliance_risk(["Massage Gun deep tissue"])[1]
assert "restricted" not in compliance_risk(["hot glue gun, nail gun, fascia gun"])[1]
assert "restricted" in compliance_risk(["air gun for sale"])[1]
assert "medical_claim" in compliance_risk(["this cures diabetes"])[1]
assert "medical_claim" in compliance_risk(["cured my back pain"])[1]

# ---- market fit: unclassified product is neutral
dna = {"PH": {"active_ads": 500, "top_categories": [(None, 0.6), ("beauty", 0.2)], "top_angles": [], "loved_categories": {}}}
assert market_fit_score(dna, "PH", None, None) == 50.0
assert market_fit_score(dna, "PH", "beauty", None) > 50.0

# ---- taste model: no None / generic features, ≥3 votes
tm = TasteModel.__new__(TasteModel)
tm.weights, tm.n_votes = {"category:beauty": [100, 100], "market:PH": [100, 100, 100]}, 5
p0 = Product(canonical_name="x", category=None, country="PH")
assert TasteModel.features(p0, set()) == ["market:PH"]
assert tm.predict(p0, set()) == 80.0  # only market:PH (3 votes) counts: (300 + 2*50) / 5
tm.weights = {"category:beauty": [100, 100]}
assert tm.predict(Product(canonical_name="x", category="beauty"), set()) is None
from app.services.discovery import ad_feature_set
assert ad_feature_set("statement", "general", None) == set()
assert ad_feature_set("problem", "general", "mess") == {"hook:problem", "funnel:mess"}


# ---- demand recalibration
def mk(n_ads, n_adv, days, country="PH"):
    now = datetime.utcnow()
    prod = Product(id=1, canonical_name="Posture Corrector", category="health", country=country, first_seen_at=now - timedelta(days=days))
    ads = []
    for i in range(n_ads):
        ads.append(Ad(id=i + 1, external_id=str(i), source="meta_ad_library", platform="facebook", product_id=1, advertiser_id=(i % n_adv) + 1,
                      country=country, countries=[country], channel="ads", network="meta", is_active=True, is_internal=False,
                      first_seen_at=now - timedelta(days=days), last_seen_at=now, ad_text=f"posture corrector {i}", price=599, currency="PHP",
                      creative_fingerprint=f"fp{i}", variation_key=f"vk{i}", hook="problem", angle="pain_relief", funnel="mess"))
    f = compute_features(ProductData(product=prod, ads=ads))
    taste = SimpleNamespace(predict=lambda *a, **k: None, n_votes=0)
    return prod, ads, f, potential_vector(prod, f, ads, [], [], taste, {})


p, ads, f, pv = mk(12, 6, 60)
assert f["longevity_days"] == 60 and f["variants"] == 1 and f["advertisers_at_primary_market"] == 6, f
assert abs(f["avg_price"] - to_usd(599, "PHP")) < 0.01  # prices averaged in USD (rounded to cents)
assert pv["vector"]["market_demand"] >= 60, pv["vector"]["market_demand"]
assert pv["vector"]["priority_boost"] == 15 and pv["vector"]["opportunity"] == min(100, pv["vector"]["opportunity_raw"] + 15)
sat, state, _ = saturation_score(f)
assert sat < 45, (sat, state)  # 6 sellers at PH = healthy band, not saturated
f16 = {**f, "advertisers_at_primary_market": 18}
assert saturation_score(f16)[0] > sat  # >15 sellers penalised
p1, ads1, f1, pv1 = mk(1, 1, 120)
assert pv1["vector"]["market_demand"] < 30, pv1["vector"]["market_demand"]
assert rarity_score(f1)[0] > rarity_score(f)[0]
assert f1["creative_growth_7d"] == 0.0
_, _, f_new, _ = mk(1, 1, 2)
assert f_new["creative_growth_7d"] == round(1 / 3, 3)  # 1 new ad on 1 ad is +33 %, not +100 %

# ---- removed networks stay out of features
bad = Ad(id=99, external_id="g", source="google_ads_transparency", platform="google", product_id=1, channel="ads", network="google",
         is_active=True, is_internal=False, first_seen_at=datetime.utcnow() - timedelta(days=5), last_seen_at=datetime.utcnow())
assert compute_features(ProductData(product=p1, ads=ads1 + [bad]))["total_ads"] == 1

# ---- FX
assert abs(to_usd(10, "AUD") - 6.5) < 1e-9
assert to_usd(100, "EGP") < 5 and to_usd(1, "KWD") > 3 and to_usd(1, "EUR") > 1

# ---- lexicons
assert classify_category("pampaputi serum para sa kutis") == "beauty"
assert classify_category("مدلك الرقبة والظهر") == "health"
assert classify_category("damit at sapatos") == "fashion"

# ---- entity resolution
assert title_similarity("Back Brace", "Back Brace Support Belt Posture") < 0.62
assert title_similarity("Neck Massager", "Neck Massager") > 0.8
assert clean_name("₱199 only buy now", "Shop PH") is None
assert clean_name("Shop PH", "Shop PH") is None
assert clean_name("Posture Corrector 50% off", None) == "Posture Corrector"

# ---- agent: 'us' is not the United States
assert agent.parse_rules("show us the top products in saudi")["country"] == "SA"
assert agent.parse_rules("give us hidden winners")["country"] is None
assert agent.parse_rules("hidden winners in US")["country"] == "US"

# ---- llm.status shape
from app.services import llm
st = llm.status()
assert set(st) == {"engine", "available", "cooldown_until", "reason"}, st

print("test_scoring_rules: OK")
print(f"  demand 12 ads/6 adv/60d = {pv['vector']['market_demand']}  ·  1 ad = {pv1['vector']['market_demand']}")
print(f"  saturation 12/6 PH = {sat} ({state})  ·  rarity 1-ad = {rarity_score(f1)[0]}  ·  decision 12/6 = {pv['quadrant']}")
