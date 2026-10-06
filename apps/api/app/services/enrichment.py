"""AI Enrichment (blueprint §2, §15-17, §20): unstructured → structured.

Rule-based classifiers ship by default (vi + en lexicons). When an Anthropic key
is configured, comment ABSA and refusal-reason classification are upgraded to
Claude via structured outputs, in batches.
"""
import hashlib
import re

from . import llm

# ============================================================ helpers
def _has(text: str, words) -> bool:
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
            return True
    return False


# ============================================================ Comment ABSA (§17)
ASPECTS = [
    "quality", "effectiveness", "price", "shipping", "packaging", "authenticity", "trust",
    "sizing", "usability", "side_effects", "support", "refund",
]

# aspect → (trigger words, positive words, negative words)
ASPECT_LEXICON: dict[str, tuple[list[str], list[str], list[str]]] = {
    "quality": (
        ["chất lượng", "sản phẩm", "hàng", "quality", "product", "material", "chất liệu", "dùng", "xài", "item", "build"],
        ["tốt", "ổn", "đẹp", "xịn", "bền", "chắc chắn", "good", "great", "excellent", "solid", "love", "amazing", "nice", "perfect", "tuyệt", "ưng"],
        ["kém", "tệ", "dở", "hỏng", "hư", "rởm", "cheap quality", "broke", "broken", "bad", "poor", "terrible", "flimsy", "lỗi", "defective", "doesn't match", "không giống", "khác hình"],
    ),
    "effectiveness": (
        ["hiệu quả", "tác dụng", "works", "work", "effective", "result", "kết quả", "giảm", "hết", "đỡ"],
        ["hiệu quả", "có tác dụng", "works", "worked", "effective", "đỡ đau", "hết đau", "hết mụn", "giảm", "results", "thấy rõ", "helps", "helped"],
        ["không hiệu quả", "không tác dụng", "chẳng tác dụng", "doesn't work", "didn't work", "useless", "no effect", "vô dụng", "không thấy"],
    ),
    "price": (
        ["giá", "tiền", "price", "cost", "expensive", "cheap", "đắt", "rẻ", "worth", "sar", "aed", "đ"],
        ["rẻ", "hợp lý", "đáng tiền", "worth", "affordable", "good price", "giá tốt", "cheap"],
        ["đắt", "mắc", "cao", "hơi cao", "expensive", "overpriced", "too much", "not worth", "chát"],
    ),
    "shipping": (
        ["giao", "ship", "shipping", "delivery", "vận chuyển", "nhận hàng", "courier", "giao hàng", "arrived", "deliver"],
        ["nhanh", "fast", "quick", "đúng hẹn", "on time", "sớm"],
        ["chậm", "lâu", "slow", "late", "delay", "delayed", "never arrived", "chưa nhận", "không nhận được", "mất hàng"],
    ),
    "packaging": (
        ["đóng gói", "hộp", "package", "packaging", "box", "bao bì"],
        ["cẩn thận", "đẹp", "chắc", "well packed", "nice box"],
        ["móp", "vỡ", "rách", "bể", "damaged", "broken box", "crushed", "sơ sài"],
    ),
    "authenticity": (
        ["chính hãng", "hàng thật", "fake", "authentic", "original", "real", "giả", "nhái"],
        ["chính hãng", "authentic", "hàng thật", "genuine", "real"],
        ["fake", "giả", "nhái", "counterfeit", "replica"],
    ),
    "trust": (
        ["lừa", "scam", "uy tín", "trust", "legit", "lừa đảo", "shop", "page", "seller"],
        ["uy tín", "legit", "trustworthy", "tin tưởng", "recommend"],
        ["lừa", "lừa đảo", "scam", "scammer", "fraud", "không uy tín", "cẩn thận", "đừng mua", "don't buy"],
    ),
    "sizing": (
        ["size", "kích thước", "rộng", "chật", "fit", "small", "big", "nhỏ", "to"],
        ["vừa", "fits", "fit well", "vừa vặn", "true to size"],
        ["rộng", "chật", "too small", "too big", "nhỏ quá", "to quá", "doesn't fit"],
    ),
    "usability": (
        ["dùng", "sử dụng", "use", "easy", "khó", "dễ", "setup", "hướng dẫn", "manual", "pin", "battery", "sạc"],
        ["dễ dùng", "dễ sử dụng", "easy to use", "easy", "tiện", "convenient", "simple", "pin trâu"],
        ["khó dùng", "khó sử dụng", "hard to use", "complicated", "confusing", "không biết dùng", "pin yếu", "hết pin nhanh"],
    ),
    "side_effects": (
        ["kích ứng", "dị ứng", "rát", "ngứa", "đỏ", "irritation", "rash", "allergic", "burn", "side effect", "nổi mụn"],
        [],
        ["kích ứng", "dị ứng", "rát", "ngứa", "irritation", "rash", "allergic", "burn", "burning", "nổi mụn", "side effect", "side effects"],
    ),
    "support": (
        ["cskh", "tư vấn", "support", "customer service", "inbox", "rep", "trả lời", "hỗ trợ", "seller"],
        ["nhiệt tình", "helpful", "responsive", "tư vấn kỹ", "hỗ trợ tốt", "fast reply"],
        ["không trả lời", "no reply", "ignored", "không ai trả lời", "rude", "thái độ"],
    ),
    "refund": (
        ["hoàn tiền", "refund", "trả hàng", "return", "đổi trả", "money back"],
        ["hoàn tiền nhanh", "refunded", "đã hoàn", "easy return"],
        ["không hoàn", "no refund", "không cho đổi", "refuse refund", "chưa hoàn", "want refund", "muốn hoàn"],
    ),
}

GENERIC_POS = ["tốt", "tuyệt", "thích", "ưng", "love", "great", "amazing", "good", "perfect", "recommend", "best", "👍", "❤️", "😍", "đỉnh", "xịn"]
GENERIC_NEG = ["tệ", "dở", "thất vọng", "bad", "terrible", "worst", "disappointed", "awful", "scam", "lừa", "😡", "👎", "phí tiền", "waste"]
INTENT_WORDS = ["muốn mua", "đặt", "order", "how to buy", "mua ở đâu", "còn hàng", "ib", "inbox", "giá bao nhiêu", "how much",
                "where to buy", "link", "ship về", "lấy 1", "lấy 2", "cho mình 1", "i want", "need this", "want one", "pm"]
QUESTION_WORDS = ["?", "bao nhiêu", "how much", "how", "where", "có ship", "có không", "không ạ", "được không", "là gì", "when"]

CLAUSE_SPLIT = re.compile(r"(?:,|\.|;|!|\bnhưng\b|\bbut\b|\bmà\b|\bvà\b|\band\b|\btuy nhiên\b|\bhowever\b)", re.IGNORECASE)


def analyze_comment_rules(text: str) -> dict:
    t = (text or "").lower()
    aspects: dict[str, str] = {}
    for clause in CLAUSE_SPLIT.split(t):
        clause = clause.strip()
        if not clause:
            continue
        specific = False
        for aspect, (triggers, pos, neg) in ASPECT_LEXICON.items():
            if aspect == "quality" or not _has(clause, triggers):
                continue
            if _has(clause, neg):
                aspects[aspect] = "negative"
                specific = True
            elif pos and _has(clause, pos):
                aspects.setdefault(aspect, "positive")
                specific = True
            # trigger without an opinion word → mention only, not tagged
        if specific:
            continue
        # No specific aspect in this clause → opinion words are about the product itself
        _, qpos, qneg = ASPECT_LEXICON["quality"]
        if _has(clause, qneg) or _has(clause, GENERIC_NEG):
            aspects["quality"] = "negative"
        elif _has(clause, qpos) or _has(clause, GENERIC_POS):
            aspects.setdefault("quality", "positive")

    pos = sum(1 for v in aspects.values() if v == "positive") + (1 if not aspects and _has(t, GENERIC_POS) else 0)
    neg = sum(1 for v in aspects.values() if v == "negative") + (1 if not aspects and _has(t, GENERIC_NEG) else 0)
    if pos and neg and abs(pos - neg) <= 1:
        overall = "neutral"
    elif pos > neg:
        overall = "positive"
    elif neg > pos:
        overall = "negative"
    else:
        overall = "neutral"

    is_question = _has(t, QUESTION_WORDS) or t.strip().endswith("?")
    if _has(t, INTENT_WORDS):
        intent = "high"
    elif overall == "negative" or aspects.get("trust") == "negative":
        intent = "low"
    elif pos > 0 or is_question:
        intent = "medium"
    else:
        intent = "low"
    return {"overall": overall, "aspects": aspects, "purchase_intent": intent, "is_question": is_question}


_ABSA_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "overall": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                    "aspects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "aspect": {"type": "string", "enum": ASPECTS},
                                "polarity": {"type": "string", "enum": ["positive", "negative"]},
                            },
                            "required": ["aspect", "polarity"],
                            "additionalProperties": False,
                        },
                    },
                    "purchase_intent": {"type": "string", "enum": ["high", "medium", "low"]},
                    "is_question": {"type": "boolean"},
                },
                "required": ["i", "overall", "aspects", "purchase_intent", "is_question"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def analyze_comments(texts: list[str]) -> tuple[list[dict], str]:
    """Batch ABSA. Returns (results, engine)."""
    if llm.available() and texts:
        out: list[dict] = []
        for start in range(0, len(texts), 40):
            chunk = texts[start:start + 40]
            numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(chunk))
            data = llm.complete_json(
                system=(
                    "You do aspect-based sentiment analysis on e-commerce ad comments and reviews "
                    "(Vietnamese, English, Arabic, Thai...). Only tag an aspect when the commenter expresses an "
                    "opinion about it. Return one result per numbered comment, using its number as `i`."
                ),
                user=numbered,
                schema=_ABSA_SCHEMA,
            )
            if not data:
                out = []
                break
            by_i = {r["i"]: r for r in data["results"]}
            for i, c in enumerate(chunk):
                r = by_i.get(i)
                if r is None:
                    out.append(analyze_comment_rules(c))
                    continue
                out.append({
                    "overall": r["overall"],
                    "aspects": {a["aspect"]: a["polarity"] for a in r["aspects"]},
                    "purchase_intent": r["purchase_intent"],
                    "is_question": r["is_question"],
                })
        if out:
            return out, "claude"
    return [analyze_comment_rules(t) for t in texts], "rules"


# ============================================================ COD refusal reasons (§16.1)
REFUSAL_REASONS = {
    "changed_mind": ["đổi ý", "không cần nữa", "changed mind", "change mind", "không muốn mua", "no longer want", "không lấy nữa"],
    "price_too_high": ["đắt", "giá cao", "mắc", "expensive", "too expensive", "price high", "không đủ tiền", "no money"],
    "fake_order": ["fake", "đặt bừa", "đặt nhầm", "không đặt", "didn't order", "prank", "trẻ con đặt", "ảo"],
    "cannot_contact": ["không nghe máy", "thuê bao", "không liên lạc", "no answer", "unreachable", "switched off", "sai số", "wrong number", "tắt máy"],
    "expectation_mismatch": ["không giống", "khác hình", "khác quảng cáo", "not as described", "different from ad", "nhỏ hơn", "not like picture", "không như"],
    "delivery_too_slow": ["lâu quá", "chậm", "too slow", "took too long", "late", "đợi lâu"],
    "duplicate_order": ["trùng", "duplicate", "đặt 2 lần", "double order", "đã nhận đơn khác"],
    "bought_elsewhere": ["mua chỗ khác", "đã mua", "bought elsewhere", "already bought", "mua ngoài"],
    "quality_concern": ["chất lượng", "kém", "quality", "sợ hỏng", "looks cheap", "broken", "hỏng"],
    "trust_issue": ["lừa", "scam", "không tin", "sợ lừa", "don't trust", "kiểm hàng", "không cho xem hàng", "not allowed to check"],
}


def classify_refusal_rules(note: str | None) -> str | None:
    if not note:
        return None
    t = note.lower()
    for reason, words in REFUSAL_REASONS.items():
        if _has(t, words):
            return reason
    return "other"


_REFUSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "reason": {"type": "string", "enum": [*REFUSAL_REASONS.keys(), "other"]}},
                "required": ["i", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def classify_refusals(notes: list[str]) -> list[str | None]:
    if llm.available() and notes:
        numbered = "\n".join(f"{i}. {n}" for i, n in enumerate(notes))
        data = llm.complete_json(
            system="Classify why a COD customer refused the delivery, based on the call-center / courier note.",
            user=numbered,
            schema=_REFUSAL_SCHEMA,
        )
        if data:
            by_i = {r["i"]: r["reason"] for r in data["results"]}
            return [by_i.get(i) or classify_refusal_rules(n) for i, n in enumerate(notes)]
    return [classify_refusal_rules(n) for n in notes]


# ============================================================ Creative: hook / angle / offer (§20)
HOOKS = {
    "question": ["?", "bạn có", "do you", "are you", "have you", "có phải"],
    "problem": ["đau", "mệt", "khổ", "tired of", "struggling", "pain", "problem", "suffer", "mụn", "rụng tóc"],
    "before_after": ["before", "after", "trước và sau", "trước sau", "transformation", "sau 7 ngày", "after 7 days"],
    "testimonial": ["review", "khách hàng", "customer", "feedback", "chị", "em đã dùng", "i tried", "my mom", "testimonial"],
    "demo": ["xem", "watch", "how it works", "cách dùng", "chỉ cần", "just", "demo", "in seconds"],
    "urgency": ["hôm nay", "today", "only", "chỉ còn", "limited", "last chance", "hurry", "flash sale", "cuối cùng"],
    "curiosity": ["bí mật", "secret", "nobody tells", "không ai nói", "you won't believe", "viral", "tiktok made me"],
}
ANGLES = {
    "pain_relief": ["đau", "pain", "relief", "nhức", "mỏi", "đỡ", "relax", "thư giãn"],
    "beauty": ["đẹp", "da", "skin", "glow", "trắng", "beauty", "mụn", "acne", "nếp nhăn", "wrinkle", "tóc", "hair"],
    "convenience": ["tiện", "easy", "nhanh", "save time", "tiết kiệm thời gian", "portable", "mang theo", "anywhere"],
    "health": ["sức khỏe", "health", "healthy", "posture", "ngủ", "sleep", "tư thế"],
    "saving_money": ["tiết kiệm", "save money", "rẻ hơn", "cheaper", "thay vì", "instead of"],
    "gift": ["quà", "gift", "tặng", "mother", "mẹ", "valentine", "eid", "ramadan"],
    "pet_care": ["pet", "chó", "mèo", "dog", "cat"],
}
OFFER_PATTERNS = [
    (r"(\d{1,2})\s?%\s?(off|giảm|discount)|giảm\s?(\d{1,2})\s?%", "discount"),
    (r"free\s?ship|freeship|miễn phí vận chuyển|miễn phí ship|free delivery|توصيل مجاني", "free_shipping"),
    (r"buy\s?1\s?get\s?1|mua 1 tặng 1|bogo|1\+1", "bogo"),
    (r"\bcod\b|thanh toán khi nhận|cash on delivery|الدفع عند الاستلام", "cod"),
    (r"bảo hành|warranty|guarantee|hoàn tiền|money back", "guarantee"),
    (r"tặng kèm|free gift|quà tặng", "free_gift"),
]


def classify_creative(text: str | None) -> dict:
    t = (text or "").lower()
    hook = next((h for h, w in HOOKS.items() if _has(t, w) or (h == "question" and "?" in t[:80])), "statement")
    angle = next((a for a, w in ANGLES.items() if _has(t, w)), "general")
    offers = [name for pat, name in OFFER_PATTERNS if re.search(pat, t)]
    return {"hook": hook, "angle": angle, "offer": ",".join(dict.fromkeys(offers)) or None}


def creative_fingerprint(text: str | None, media_url: str | None) -> str:
    """Near-duplicate key: normalized first 60 chars of copy + media path."""
    norm = re.sub(r"\W+", "", (text or "").lower())[:60]
    media = (media_url or "").split("?")[0].rsplit("/", 1)[-1]
    return hashlib.sha1(f"{norm}|{media}".encode()).hexdigest()[:16]


# ============================================================ Market classifier (§7)
COUNTRY_INFO = {
    "SA": ("Saudi Arabia", "GCC", "ar", "SAR"), "AE": ("UAE", "GCC", "ar", "AED"), "KW": ("Kuwait", "GCC", "ar", "KWD"),
    "QA": ("Qatar", "GCC", "ar", "QAR"), "OM": ("Oman", "GCC", "ar", "OMR"), "BH": ("Bahrain", "GCC", "ar", "BHD"),
    "VN": ("Vietnam", "SEA", "vi", "VND"), "TH": ("Thailand", "SEA", "th", "THB"), "PH": ("Philippines", "SEA", "en", "PHP"),
    "MY": ("Malaysia", "SEA", "ms", "MYR"), "ID": ("Indonesia", "SEA", "id", "IDR"),
    "US": ("United States", "NA", "en", "USD"), "GB": ("United Kingdom", "EU", "en", "GBP"),
    "PL": ("Poland", "EU", "pl", "PLN"), "RO": ("Romania", "EU", "ro", "RON"),
}
COD_COUNTRIES = {"SA", "AE", "KW", "QA", "OM", "BH", "VN", "TH", "PH", "MY", "ID", "PL", "RO"}
# rough USD conversion for price segmentation
FX_TO_USD = {"SAR": 0.27, "AED": 0.27, "KWD": 3.25, "QAR": 0.27, "OMR": 2.6, "BHD": 2.65, "VND": 0.00004,
             "THB": 0.028, "PHP": 0.018, "MYR": 0.21, "IDR": 0.000063, "USD": 1, "GBP": 1.27, "PLN": 0.25, "RON": 0.22}


def price_segment(price: float | None, currency: str | None) -> str | None:
    if not price:
        return None
    usd = price * FX_TO_USD.get((currency or "USD").upper(), 1)
    if usd < 15:
        return "budget (<$15)"
    if usd < 35:
        return "mid ($15-35)"
    if usd < 70:
        return "upper ($35-70)"
    return "premium (>$70)"


def to_usd(price: float | None, currency: str | None) -> float | None:
    if price is None:
        return None
    return price * FX_TO_USD.get((currency or "USD").upper(), 1)


def classify_market(country: str | None, platform: str | None, category: str | None, price: float | None,
                    currency: str | None, ad_text: str | None = None) -> dict:
    code = (country or "").upper()
    name, region, lang, cur = COUNTRY_INFO.get(code, (country, None, None, currency))
    t = (ad_text or "").lower()
    funnel = "COD" if (code in COD_COUNTRIES or re.search(OFFER_PATTERNS[3][0], t)) else "Prepaid"
    if re.search(r"whatsapp|messenger|inbox|nhắn tin|ib", t):
        funnel = f"{funnel} + Chat"
    return {
        "country": code or None, "country_name": name, "region": region, "language": lang,
        "platform": platform, "category": category, "price_segment": price_segment(price, currency or cur),
        "currency": currency or cur, "funnel_type": funnel,
    }


# ============================================================ Category classifier
CATEGORY_KEYWORDS = {
    "beauty": ["serum", "cream", "kem", "acne", "mụn", "skin", "da", "whitening", "lipstick", "makeup", "hair", "tóc", "perfume", "nước hoa", "mask", "collagen"],
    "health": ["massager", "massage", "posture", "corrector", "knee", "brace", "pain", "đau", "supplement", "vitamin", "neck", "back", "foot", "sleep"],
    "home": ["kitchen", "blender", "vacuum", "cleaner", "mop", "organizer", "lamp", "light", "pillow", "bếp", "nồi", "chảo", "cleaning"],
    "gadgets": ["phone", "charger", "earbuds", "camera", "smart", "projector", "drone", "watch", "led", "usb", "bluetooth"],
    "fashion": ["dress", "shoe", "bag", "túi", "giày", "váy", "áo", "jeans", "abaya", "jewelry", "ring", "necklace"],
    "pets": ["pet", "dog", "cat", "chó", "mèo", "leash", "litter"],
}


def classify_category(name: str, text: str | None = None) -> str | None:
    t = f"{name} {text or ''}".lower()
    best, hits = None, 0
    for cat, words in CATEGORY_KEYWORDS.items():
        n = sum(1 for w in words if _has(t, [w]))
        if n > hits:
            best, hits = cat, n
    return best


# ============================================================ Product name extraction (LLM, optional)
_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "product_name": {"type": ["string", "null"], "description": "short generic product name in English, e.g. 'Electric Neck Massager'"},
                    "is_physical_product": {"type": "boolean"},
                    "category": {"type": "string", "enum": ["beauty", "health", "home", "gadgets", "fashion", "pets", "kids", "food", "other", "service"]},
                },
                "required": ["i", "product_name", "is_physical_product", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def extract_products(items: list[dict]) -> list[dict] | None:
    """items: [{text, title, url}] → [{product_name, is_physical_product, category}] or None without LLM."""
    if not llm.available() or not items:
        return None
    out: list[dict] = []
    for start in range(0, len(items), 25):
        chunk = items[start:start + 25]
        lines = "\n".join(f"{i}. title={c.get('title')!r} url={c.get('url')!r} text={(c.get('text') or '')[:400]!r}" for i, c in enumerate(chunk))
        data = llm.complete_json(
            system=("You read e-commerce ads (any language) and name the product being sold as a short generic English "
                    "product name (no brand hype, no prices). is_physical_product=false for services, apps, media, "
                    "events, real estate, courses. Use the number as i."),
            user=lines, schema=_PRODUCT_SCHEMA,
        )
        if not data:
            return None
        by_i = {r["i"]: r for r in data["results"]}
        out += [by_i.get(i, {"product_name": None, "is_physical_product": True, "category": "other"}) for i in range(len(chunk))]
    return out
