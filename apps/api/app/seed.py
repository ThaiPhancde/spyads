"""Demo data generator.

Everything goes through the real pipeline (raw lake → normalize → entity resolution →
enrichment → scoring → snapshots → alerts), so the seed also exercises the engine.
Product names deliberately vary between advertisers to test entity resolution.
"""
import random
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine
from .models import Ad, Advertiser, Experiment, Product
from .services.connectors import ensure_default_connectors, normalize_pending, store_raw
from .services.entity_resolution import find_best_match

# (name variants, category, primary country, extra markets, price, currency, unit cost, archetype, search trend, kd)
CONCEPTS = [
    (["Portable Neck Massager", "Electric Neck Massage Device", "Cervical Massage Machine"], "health", "SA", ["AE", "KW"], 149, "SAR", 32, "rising", 35, 0.45),
    (["Acne Patch Pimple Healing", "Pimple Patch Acne Dots"], "beauty", "SA", ["AE", "KW", "QA", "PH", "MY"], 79, "SAR", 9, "saturated", -5, 0.85),
    (["LED Teeth Whitening Kit", "Teeth Whitening LED Light Kit"], "beauty", "AE", ["SA", "KW"], 129, "AED", 21, "saturated", 2, 0.8),
    (["Snail Mucin Repair Serum", "Snail Repair Essence Serum"], "beauty", "SA", [], 119, "SAR", 18, "hidden", 70, 0.2),
    (["Posture Corrector Back Brace", "Back Posture Corrector"], "health", "AE", ["SA", "VN", "PH", "MY", "TH"], 99, "AED", 14, "saturated", -10, 0.9),
    (["Mini Portable Blender Bottle", "Portable Juicer Blender"], "home", "VN", ["TH", "PH", "MY"], 390000, "VND", 95000, "declining", -25, 0.75),
    (["Red Light Therapy Wand", "Red Light Face Wand"], "beauty", "AE", ["SA"], 249, "AED", 48, "hidden", 85, 0.15),
    (["Knee Massager Heat Therapy", "Heated Knee Massager"], "health", "SA", ["KW"], 199, "SAR", 45, "hidden", 60, 0.25),
    (["Smart Pet Water Fountain", "Cat Water Fountain Smart"], "pets", "AE", ["SA"], 159, "AED", 34, "rising", 40, 0.4),
    (["Hair Growth Oil Rosemary", "Rosemary Hair Oil Growth"], "beauty", "SA", ["AE", "KW", "QA"], 89, "SAR", 11, "rising", 55, 0.6),
    (["Foot Massager Shiatsu Machine", "Shiatsu Foot Massager"], "health", "SA", ["AE"], 349, "SAR", 95, "steady", 10, 0.55),
    (["Cordless Handheld Vacuum Cleaner", "Handheld Car Vacuum"], "home", "VN", ["TH"], 450000, "VND", 140000, "steady", 5, 0.7),
    (["Magnetic Phone Charger Stand", "Wireless Magnetic Charger"], "gadgets", "PH", ["MY", "TH"], 1290, "PHP", 320, "saturated", -8, 0.9),
    (["Anti Snoring Device Nose Clip", "Snore Stopper Nose Clip"], "health", "AE", [], 89, "AED", 7, "hidden", 45, 0.2),
    (["Lymphatic Drainage Ginger Oil", "Belly Ginger Drainage Oil"], "beauty", "SA", ["AE", "KW", "QA", "OM"], 69, "SAR", 8, "rising", 90, 0.5),
    (["Car Scratch Remover Pen", "Scratch Repair Pen Car"], "gadgets", "VN", ["TH", "PH"], 199000, "VND", 25000, "declining", -30, 0.65),
    (["Abaya Silk Premium Dress", "Premium Silk Abaya"], "fashion", "SA", ["AE"], 299, "SAR", 90, "steady", 15, 0.6),
    (["Eyebrow Stamp Stencil Kit", "Brow Stamp Kit"], "beauty", "PH", ["MY"], 590, "PHP", 70, "hidden", 50, 0.25),
    (["Dog Paw Cleaner Cup", "Pet Paw Washer Cup"], "pets", "MY", ["PH", "SA"], 59, "MYR", 9, "steady", 12, 0.45),
    (["Electric Lunch Box Heater", "Heated Lunch Box Electric"], "home", "SA", ["AE"], 129, "SAR", 30, "rising", 30, 0.35),
    (["Wrinkle Remover Face Tape", "Anti Wrinkle Patches Face"], "beauty", "AE", [], 79, "AED", 6, "noise", 0, 0.5),
    (["Bamboo Charcoal Toothpaste Foam", "Charcoal Teeth Foam"], "beauty", "TH", [], 299, "THB", 45, "noise", 0, 0.5),
    (["Smart Ring Sleep Tracker", "Sleep Tracker Smart Ring"], "gadgets", "AE", ["SA"], 399, "AED", 120, "hidden", 75, 0.3),
    (["Cellulite Massager Roller", "Anti Cellulite Roller Massager"], "beauty", "SA", ["AE", "KW"], 139, "SAR", 26, "steady", 5, 0.55),
    (["Gua Sha Jade Stone Set", "Jade Gua Sha Face Tool"], "beauty", "VN", ["TH", "MY", "PH"], 159000, "VND", 22000, "saturated", -12, 0.85),
    (["Massage Gun Deep Tissue", "Deep Tissue Massage Gun"], "health", "SA", ["AE", "KW", "QA", "VN", "TH"], 299, "SAR", 70, "saturated", -15, 0.95),
    (["Hair Removal IPL Laser Device", "IPL Hair Remover Laser"], "beauty", "SA", ["AE"], 599, "SAR", 140, "rising", 25, 0.7),
    (["Oud Perfume Oil Arabic", "Arabic Oud Oil Perfume"], "beauty", "SA", ["AE", "KW"], 179, "SAR", 30, "steady", 8, 0.65),
    (["Baby Nasal Aspirator Electric", "Electric Nose Cleaner Baby"], "health", "VN", ["TH"], 290000, "VND", 70000, "hidden", 40, 0.2),
    (["Wall Mounted Toothbrush Holder", "Toothpaste Dispenser Wall"], "home", "PH", ["MY", "TH"], 499, "PHP", 90, "noise", 0, 0.5),
]

FRESH_NAMES = [
    ("Collagen Lip Plumper Gloss", "beauty", "AE", 89, "AED"), ("Scalp Massager Shampoo Brush", "beauty", "SA", 49, "SAR"),
    ("Portable Car Jump Starter", "gadgets", "VN", 890000, "VND"), ("Orthopedic Seat Cushion", "health", "SA", 129, "SAR"),
    ("Cat Laser Toy Automatic", "pets", "AE", 79, "AED"), ("Electric Spin Scrubber Brush", "home", "PH", 990, "PHP"),
]

ARCH = {
    #            adv range   ads range   age(days) dist          ramp   end_ratio  stores  traffic  t_growth  pos  neg
    "saturated": ((60, 140), (220, 380), "uniform90",            1.0,   0.35,      (25, 45), (90e3, 300e3), (-15, 10), 0.55, 0.25),
    "rising":    ((14, 30),  (70, 140),  "recent30",             2.0,   0.10,      (6, 14), (20e3, 80e3), (25, 70), 0.72, 0.12),
    "hidden":    ((4, 9),    (18, 40),   "recent14",             3.0,   0.05,      (2, 5), (3e3, 15e3), (60, 180), 0.8, 0.08),
    "steady":    ((10, 22),  (50, 100),  "uniform60",            1.0,   0.20,      (5, 10), (15e3, 50e3), (0, 15), 0.66, 0.15),
    "declining": ((30, 60),  (120, 200), "old",                  0.5,   0.70,      (15, 30), (30e3, 90e3), (-40, -15), 0.45, 0.32),
    "noise":     ((1, 2),    (1, 3),     "last3",                1.0,   0.0,       (0, 1), (500, 2e3), (0, 10), 0.6, 0.2),
}

HOOK_LINES = {
    "en": ["Tired of {pain}?", "Before vs after 7 days 😱", "TikTok made me buy it!", "My mom tried this and cried",
           "Watch how it works in 10 seconds", "The secret nobody tells you about {pain}", "Only today: limited stock!"],
    "vi": ["Bạn có đang bị {pain}?", "Trước và sau 7 ngày dùng 😱", "Xem cách dùng chỉ 10 giây", "Chị em đã dùng đều khen",
           "Chỉ còn hôm nay - flash sale!"],
    "ar": ["هل تعاني من {pain}؟", "قبل وبعد ٧ أيام", "شاهد كيف يعمل"],
}
OFFERS = ["50% OFF + Free shipping", "Buy 1 Get 1 free", "COD - Cash on delivery available", "Giảm 40% + freeship toàn quốc",
          "Free delivery | الدفع عند الاستلام", "30-day money back guarantee"]
PAINS = {"health": "neck pain", "beauty": "acne and dull skin", "home": "wasting time cleaning", "gadgets": "slow charging",
         "pets": "messy pets", "fashion": "boring outfits"}
LANG = {"SA": "ar", "AE": "en", "KW": "ar", "QA": "ar", "OM": "ar", "VN": "vi", "TH": "en", "PH": "en", "MY": "en"}

POS_COMMENTS = [
    "Sản phẩm dùng rất tốt, hiệu quả thấy rõ sau 1 tuần 👍", "Love it! works great and fast delivery", "Giao nhanh, đóng gói cẩn thận, hàng đẹp",
    "Amazing quality, my neck pain is gone", "Dùng ổn, giá hợp lý, sẽ mua thêm", "Highly recommend, legit seller",
    "Đã nhận hàng, chất lượng tốt, shop tư vấn nhiệt tình", "Worth every riyal, effective!", "Easy to use and helped a lot ❤️",
    "Hàng chính hãng, hiệu quả thật sự",
]
NEG_COMMENTS = [
    "Giao quá chậm, đợi 2 tuần chưa nhận", "Product doesn't match the advertisement, very disappointed", "Hàng không giống hình, chất lượng kém",
    "Too expensive for this quality", "Scam! never arrived", "Bôi bị kích ứng, rát da", "Dùng 1 tháng không thấy tác dụng",
    "Hộp bị móp, sản phẩm hỏng", "Shop không trả lời tin nhắn, muốn hoàn tiền", "Fake product, not original",
    "Giá hơi cao so với chất lượng", "Doesn't work at all, waste of money",
]
NEU_COMMENTS = [
    "Giá bao nhiêu vậy shop?", "How much? do you deliver to Riyadh?", "Có ship về Hà Nội không ạ", "Where to buy? I want one",
    "Cho mình đặt 2 cái", "Còn hàng không shop?", "Sản phẩm dùng ổn nhưng giao quá chậm và giá hơi cao.", "Is it COD?",
]
REFUSAL_NOTES = [
    "Khách không nghe máy 3 lần", "Khách đổi ý, không cần nữa", "Customer says too expensive", "Not as described, product looks different from ad",
    "Khách nói không đặt hàng", "Customer already bought elsewhere", "Giao lâu quá khách hủy", "Khách sợ lừa, đòi kiểm hàng",
    "Duplicate order", "Khách chê chất lượng kém",
]

FIRST = ["Glow", "Pure", "Smart", "Best", "Daily", "Happy", "Mega", "Royal", "Prime", "Lux", "Nova", "Zen", "Hala", "Souq", "Viet", "Shop"]
SECOND = ["Store", "Mart", "Deals", "Hub", "Shop", "Market", "Corner", "Bazaar", "Outlet", "Life", "Home", "Beauty"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _age(rng: random.Random, dist: str) -> float:
    if dist == "uniform90":
        return rng.uniform(0, 90)
    if dist == "uniform60":
        return rng.uniform(0, 60)
    if dist == "recent30":
        return min(rng.expovariate(1 / 12), 45)
    if dist == "recent14":
        return min(rng.expovariate(1 / 6), 30)
    if dist == "old":
        return rng.uniform(20, 90)
    if dist == "last3":
        return rng.uniform(0, 3)
    return rng.uniform(0, 30)


def _ad_text(rng, cat, country, name, offer=None):
    lang = LANG.get(country, "en")
    hook = rng.choice(HOOK_LINES[lang]).format(pain=PAINS.get(cat, "problems"))
    return f"{hook} {name} — {offer or rng.choice(OFFERS)}"


def generate_ads(rng: random.Random, now: datetime) -> tuple[list[dict], dict]:
    ads, meta = [], {}
    for ci, (names, cat, country, extra, price, cur, cost, arch, trend, kd) in enumerate(CONCEPTS):
        a = ARCH[arch]
        n_adv = rng.randint(*a[0])
        n_ads = rng.randint(*a[1])
        advertisers = []
        for k in range(n_adv):
            nm = f"{rng.choice(FIRST)} {rng.choice(SECOND)} {ci}{k}"
            adv_country = country if (not extra or rng.random() < 0.6) else rng.choice(extra)
            advertisers.append((nm, f"https://{_slug(nm)}.com", adv_country))
        weights = [1 / (i + 1) ** 0.8 for i in range(n_adv)]
        markets = [country, *extra]
        creative_pool = [_ad_text(rng, cat, country, rng.choice(names)) for _ in range(max(3, n_ads // (4 if arch == "saturated" else 2)))]
        for j in range(n_ads):
            adv = rng.choices(advertisers, weights)[0]
            age = _age(rng, a[2])
            # new markets for rising / hidden products appear in the last week
            ad_country = adv[2]
            if arch in ("rising", "hidden") and extra and age < 6 and rng.random() < 0.3:
                ad_country = rng.choice(extra)
            first = now - timedelta(days=age, hours=rng.uniform(0, 23))
            ended = rng.random() < a[4] if arch != "declining" else (age > 7 and rng.random() < 0.85)
            last = first + timedelta(days=rng.uniform(1, max(1.5, age - 1))) if ended else now
            p = price * rng.uniform(0.9, 1.1)
            if arch in ("saturated", "declining") and age < 14:
                p *= 0.82  # price compression
            text = rng.choice(creative_pool) if rng.random() < 0.6 else _ad_text(rng, cat, ad_country, rng.choice(names))
            eng = {"saturated": 120, "rising": 380, "hidden": 450, "steady": 200, "declining": 90, "noise": 40}[arch]
            ads.append({
                "external_id": f"seed-{ci}-{j}", "source": rng.choice(["minea", "minea", "meta_ad_library", "pipiads"]),
                "platform": rng.choice(["meta", "meta", "meta", "tiktok"]), "advertiser": adv[0], "advertiser_url": adv[1],
                "product_name": rng.choice(names), "ad_text": text,
                "landing_url": f"{adv[1]}/products/{_slug(names[0])}", "country": ad_country,
                "media_type": rng.choice(["video", "video", "image"]), "media_url": f"https://cdn.example.com/{ci}/{hash(text) % 997}.mp4",
                "price": round(p, 2), "currency": cur, "likes": int(rng.expovariate(1 / eng)),
                "comments_count": int(rng.expovariate(1 / (eng / 8))), "shares": int(rng.expovariate(1 / (eng / 15))),
                "first_seen": first.isoformat(), "last_seen": last.isoformat(), "is_active": not ended, "category": cat,
            })
        meta[ci] = {"advertisers": advertisers, "arch": arch, "markets": markets}
    return ads, meta


def _comment_time(rng, now, arch):
    return now - timedelta(days=min(rng.expovariate(1 / (10 if arch in ("hidden", "rising") else 25)), 60))


def generate_followups(db: Session, rng: random.Random, now: datetime, meta: dict) -> tuple[dict, dict[int, Product]]:
    out = {"store": [], "product_signal": [], "comment": []}
    products: dict[int, Product] = {}
    for ci, (names, cat, country, extra, price, cur, cost, arch, trend, kd) in enumerate(CONCEPTS):
        p, _ = find_best_match(db, {"name": names[0]})
        if not p:
            continue
        products[ci] = p
        p.cost = cost
        a = ARCH[arch]
        out["product_signal"].append({"product_code": p.product_code, "search_trend": trend, "keyword_competition": kd})
        for adv in meta[ci]["advertisers"][: rng.randint(*a[5]) or 0]:
            out["store"].append({"domain": adv[1].removeprefix("https://"), "product_code": p.product_code, "country": adv[2],
                                 "price": price, "estimated_traffic": round(rng.uniform(*a[6]) / max(1, len(meta[ci]["advertisers"]) ** 0.5)),
                                 "traffic_growth": round(rng.uniform(*a[7]), 1)})
        n_com = {"saturated": 260, "rising": 180, "hidden": 70, "steady": 120, "declining": 140, "noise": 6}[arch]
        for _ in range(n_com):
            r = rng.random()
            pool = POS_COMMENTS if r < a[8] else NEG_COMMENTS if r < a[8] + a[9] else NEU_COMMENTS
            out["comment"].append({"product_code": p.product_code, "text": rng.choice(pool), "source": rng.choice(["ad_comment", "ad_comment", "review", "inbox"]),
                                   "created_at": _comment_time(rng, now, arch).isoformat()})
    # Negative-sentiment surge for one product (alert demo: "Product doesn't match advertisement")
    p = products.get(9)
    if p:
        for _ in range(45):
            out["comment"].append({"product_code": p.product_code, "source": "ad_comment",
                                   "text": rng.choice(["Product doesn't match the advertisement, very disappointed",
                                                       "Hàng không giống hình quảng cáo", "Not like picture, chất lượng kém"]),
                                   "created_at": (now - timedelta(days=rng.uniform(0, 6))).isoformat()})
    return out, products


# Experiment scenarios — (concept index, scenario, market, creative_type, angle, funnel, ended?)
EXPERIMENTS = [
    (0, "win", "SA", "UGC", "pain_relief", "COD", True),
    (0, "running_good", "AE", "UGC", "pain_relief", "COD", False),
    (1, "fail_creative", "SA", "Static", "beauty", "COD", True),
    (2, "fail_landing", "AE", "Studio", "beauty", "COD", True),
    (4, "fail_creative", "AE", "Static", "health", "COD", True),
    (5, "fail_logistics", "VN", "UGC", "convenience", "COD", True),
    (9, "hold_refusal", "SA", "UGC", "beauty", "COD", True),
    (10, "promising", "SA", "UGC", "pain_relief", "COD", True),
    (11, "win", "VN", "UGC", "convenience", "COD", True),
    (12, "fail_price", "PH", "Studio", "convenience", "Prepaid", True),
    (14, "win", "SA", "UGC", "beauty", "COD", True),
    (16, "promising", "SA", "Studio", "beauty", "COD + Chat", True),
    (19, "running_wait", "SA", "UGC", "convenience", "COD", False),
    (24, "fail_compliance", "VN", "Static", "beauty", "COD", True),
    (25, "fail_creative", "SA", "Studio", "pain_relief", "COD", True),
    (26, "running_good", "SA", "UGC", "beauty", "COD", False),
]

SCEN = {
    #                 spend  ctr     atc    chk   pur   confirm deliv refuse_note_p return
    "win":            (2400, 0.021, 0.11, 0.55, 0.62, 0.82, 0.86, 0.10, 0.03),
    "running_good":   (900,  0.018, 0.09, 0.50, 0.60, 0.78, 0.82, 0.13, 0.04),
    "running_wait":   (90,   0.015, 0.07, 0.45, 0.55, 0.75, 0.80, 0.15, 0.05),
    "promising":      (1500, 0.014, 0.075, 0.45, 0.55, 0.74, 0.78, 0.17, 0.06),
    "fail_creative":  (700,  0.004, 0.06, 0.40, 0.50, 0.70, 0.75, 0.20, 0.06),
    "fail_landing":   (900,  0.016, 0.012, 0.40, 0.50, 0.70, 0.75, 0.20, 0.06),
    "fail_price":     (800,  0.015, 0.015, 0.35, 0.50, 0.70, 0.75, 0.20, 0.06),
    "fail_logistics": (1100, 0.017, 0.08, 0.48, 0.55, 0.75, 0.48, 0.38, 0.12),
    "hold_refusal":   (1500, 0.021, 0.14, 0.52, 0.60, 0.80, 0.62, 0.33, 0.05),
    "fail_compliance": (400, 0.011, 0.05, 0.40, 0.50, 0.70, 0.75, 0.20, 0.06),
}

USD = {"SAR": 0.27, "AED": 0.27, "VND": 0.00004, "PHP": 0.018, "MYR": 0.21, "THB": 0.028}


def generate_experiments(rng: random.Random, now: datetime, products: dict[int, Product]) -> tuple[list[dict], list[tuple]]:
    exps, order_plans = [], []
    for k, (ci, scen, market, ctype, angle, funnel, ended) in enumerate(EXPERIMENTS):
        p = products.get(ci)
        if not p:
            continue
        _, cat, _, _, price, cur, cost, *_ = CONCEPTS[ci]
        spend_usd, ctr, atc, chk, pur, conf, deliv, refuse, ret = SCEN[scen]
        fx = USD.get(cur, 1)
        spend = spend_usd * rng.uniform(0.85, 1.15)
        sell = price * (1.35 if scen == "fail_price" else 1.0)
        cpm = 6.5
        impressions = int(spend / cpm * 1000)
        clicks = int(impressions * ctr)
        lpv = int(clicks * 0.82)
        n_atc = int(lpv * atc)
        n_chk = int(n_atc * chk)
        n_pur = int(n_chk * pur)
        aov = sell * rng.uniform(1.05, 1.25)
        start = (now - timedelta(days=rng.randint(20, 75) if ended else rng.randint(3, 12))).date()
        end_d = (start + timedelta(days=rng.randint(10, 18))) if ended else None
        name = f"EXP-{k + 101} {p.canonical_name} · {market} · {ctype}"
        exps.append({
            "product_code": p.product_code, "name": name, "market": market, "platform": "meta" if k % 4 else "tiktok",
            "creative": f"{ctype} video v{rng.randint(1, 4)}", "creative_type": ctype, "angle": angle,
            "offer": rng.choice(["Discount + Free shipping", "BOGO", "COD"]), "funnel": funnel,
            "sell_price": round(sell * fx, 2), "unit_cost": round(cost * fx, 2),
            "started_at": start.isoformat(), "ended_at": end_d.isoformat() if end_d else "",
            "spend": round(spend, 2), "impressions": impressions, "clicks": clicks, "landing_views": lpv,
            "atc": n_atc, "checkout": n_chk, "purchase": n_pur, "revenue": round(n_pur * aov * fx, 2),
            "ads_submitted": rng.randint(6, 14), "ads_rejected": 0,
        })
        if scen == "fail_compliance":
            exps[-1]["ads_rejected"] = int(exps[-1]["ads_submitted"] * 0.5)
        else:
            exps[-1]["ads_rejected"] = rng.randint(0, 2)
        order_plans.append((name, p.product_code, n_pur, aov * fx, cost * fx, market, conf, deliv, refuse, ret, start, end_d or now.date()))
    return exps, order_plans


def generate_orders(rng: random.Random, now: datetime, plans: list[tuple], exp_ids: dict[str, int]) -> tuple[list[dict], dict]:
    orders, counts = [], {}
    oid = 0
    for name, code, n, aov, unit_cost, market, conf, deliv, refuse_p, ret, start, end in plans:
        c = {"confirmed_orders": 0, "shipped": 0, "delivered": 0, "refused": 0, "returned": 0}
        span = max(1, (end - start).days)
        phones = [f"+9665{rng.randint(10000000, 99999999)}" for _ in range(max(1, int(n * 0.93)))]
        # Surge: refusal rate gets worse in the last 2 weeks for the hold_refusal product
        for i in range(n):
            oid += 1
            created = datetime.combine(start, datetime.min.time()) + timedelta(days=rng.uniform(0, span))
            if created > now:
                created = now - timedelta(hours=rng.uniform(1, 48))
            recent = (now - created).days <= 14
            r = rng.random()
            note = None
            refuse_now = refuse_p + (0.10 if recent and refuse_p > 0.3 else 0)
            if r > conf:
                status = rng.choice(["cancelled", "cancelled", "pending"])
            else:
                c["confirmed_orders"] += 1
                if (now - created).days < 2:
                    status = "shipped"
                else:
                    r2 = rng.random()
                    if r2 < refuse_now:
                        status, note = "refused", rng.choice(REFUSAL_NOTES)
                    elif r2 < 1 - deliv:
                        status, note = "failed", rng.choice(REFUSAL_NOTES[:1])
                    else:
                        status = "returned" if rng.random() < ret else "delivered"
                c["shipped"] += 1
                if status in ("delivered", "returned"):
                    c["delivered"] += 1
                if status == "refused":
                    c["refused"] += 1
                if status == "returned":
                    c["returned"] += 1
            orders.append({
                "external_id": f"ORD-{oid:06d}", "product_code": code, "experiment_id": exp_ids.get(name), "country": market,
                "amount": round(aov * rng.uniform(0.9, 1.1), 2), "cogs": round(unit_cost * rng.uniform(1.0, 1.3), 2),
                "shipping_cost": round(5.5 * rng.uniform(0.8, 1.2), 2), "status": status, "refusal_note": note or "",
                "refunded": status == "returned", "phone": rng.choice(phones), "created_at": created.isoformat(), "source": "crm",
            })
        counts[name] = c
    return orders, counts


def simulate_daily_feed(db: Session, rng: random.Random) -> list[dict]:
    """Used by the demo_spy connector: today's new ads + refresh of active ones."""
    now = datetime.utcnow()
    out = []
    products = db.scalars(select(Product)).all()
    if not products:
        return out
    growth_bias = sorted(products, key=lambda p: -(p.features or {}).get("creative_growth_7d", 0))[:12]
    for p in rng.sample(growth_bias, min(8, len(growth_bias))) + rng.sample(products, min(4, len(products))):
        advs = db.scalars(select(Advertiser).join(Ad, Ad.advertiser_id == Advertiser.id).where(Ad.product_id == p.id).distinct()).all()
        for _ in range(rng.randint(1, 6)):
            if advs and rng.random() < 0.8:
                adv_name, adv_url, country = (lambda a: (a.name, a.page_url, a.country))(rng.choice(advs))
            else:
                adv_name = f"{rng.choice(FIRST)} {rng.choice(SECOND)} n{rng.randint(100, 999)}"
                adv_url, country = f"https://{_slug(adv_name)}.com", p.country
            alias = rng.choice([p.canonical_name, *[a.name for a in p.aliases]])
            out.append({
                "external_id": f"feed-{now:%Y%m%d}-{p.id}-{rng.randint(0, 10 ** 9)}", "source": "demo_spy",
                "platform": rng.choice(["meta", "tiktok"]), "advertiser": adv_name, "advertiser_url": adv_url,
                "product_name": alias, "ad_text": _ad_text(rng, p.category, country or "AE", alias),
                "landing_url": f"{adv_url}/products/{_slug(p.canonical_name)}", "country": country or p.country,
                "media_type": "video", "price": (p.features or {}).get("avg_price") or p.price, "currency": p.currency,
                "likes": rng.randint(0, 800), "comments_count": rng.randint(0, 60), "shares": rng.randint(0, 30),
                "first_seen": now.isoformat(), "last_seen": now.isoformat(), "is_active": True, "category": p.category,
            })
    # keep currently-active ads alive (simulates the provider re-reporting them)
    for a in db.scalars(select(Ad).where(Ad.is_active.is_(True), Ad.is_internal.is_(False))).all():
        if rng.random() < 0.93:
            out.append({"external_id": a.external_id, "source": a.source, "last_seen": now.isoformat(), "is_active": True,
                        "likes": a.likes + rng.randint(0, 20)})
        else:
            out.append({"external_id": a.external_id, "source": a.source, "last_seen": a.last_seen_at.isoformat(), "is_active": False})
    if rng.random() < 0.5:
        name, cat, country, price, cur = rng.choice(FRESH_NAMES)
        for k in range(rng.randint(1, 3)):
            adv_name = f"{rng.choice(FIRST)} {rng.choice(SECOND)} f{rng.randint(100, 999)}"
            out.append({"external_id": f"fresh-{now:%Y%m%d}-{_slug(name)}-{k}-{rng.randint(0, 10 ** 6)}", "source": "demo_spy",
                        "platform": "tiktok", "advertiser": adv_name, "advertiser_url": f"https://{_slug(adv_name)}.com",
                        "product_name": name, "ad_text": _ad_text(rng, cat, country, name),
                        "landing_url": f"https://{_slug(adv_name)}.com/products/{_slug(name)}", "country": country,
                        "price": price, "currency": cur, "first_seen": now.isoformat(), "last_seen": now.isoformat(),
                        "is_active": True, "category": cat})
    # existing-ad refresh records only carry partial fields → they hit the dedupe branch of _norm_ad
    return out


def seed(reset: bool = True, seed_value: int = 42):
    from .services.alerts import run_alerts
    from .services.engine import backfill_snapshots, score_all

    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rng = random.Random(seed_value)
    now = datetime.utcnow()
    with SessionLocal() as db:
        ensure_default_connectors(db)
        ads, meta = generate_ads(rng, now)
        store_raw(db, "ad", ads)
        print("ads →", normalize_pending(db))
        follow, products = generate_followups(db, rng, now, meta)
        for et in ("product_signal", "store", "comment"):
            store_raw(db, et, follow[et])
        exps, plans = generate_experiments(rng, now, products)
        store_raw(db, "experiment", exps)
        print("signals/stores/comments/experiments →", normalize_pending(db))
        exp_ids = {e.name: e.id for e in db.scalars(select(Experiment)).all()}
        orders, counts = generate_orders(rng, now, plans, exp_ids)
        store_raw(db, "order", orders)
        # experiment back-end counters mirror the CRM orders
        store_raw(db, "experiment", [{"product_code": next(o for o in exps if o["name"] == n)["product_code"], "name": n, **c}
                                     for n, c in counts.items()])
        print("orders →", normalize_pending(db))
        print("scored", score_all(db))
        print("snapshots", backfill_snapshots(db, 30))
        print("scored (with snapshots)", score_all(db))
        print("alerts", run_alerts(db))
        for p in db.scalars(select(Product)).all():
            p.is_demo = True  # simulated — never mix with real data in decisions
        db.commit()


if __name__ == "__main__":
    seed()
